"""Microphone capture and voice-activity endpointing.

Frames are 20 ms of 16 kHz mono int16 (640 bytes). The capture callback runs on PortAudio's thread and
hands frames to asyncio with call_soon_threadsafe — no blocking work on the audio thread.
"""

from __future__ import annotations

import asyncio
import logging
import math
from array import array
from collections import deque
from collections.abc import AsyncIterator
from typing import Protocol

log = logging.getLogger("driveos.audio")

FRAME_MS = 20
RATE = 16000
FRAME_SAMPLES = RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


class AudioSource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def frames(self) -> AsyncIterator[bytes]: ...


RING_FRAMES = 600   # 12 s of both channels, for the biometric capture


class Microphone:
    """Opens two channels when the device has them (the XVF3800 does). Channel 0 feeds the voice pipeline
    exactly as before; both channels are kept in a ring buffer for the biometric capture."""

    def __init__(self, device: str | int | None = None, rate: int = RATE):
        self.device = device
        self.rate = rate
        self._q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.channels = 1
        self.device_name = ""
        self.seq = 0
        self.ring: deque[tuple[int, bytes, bytes | None]] = deque(maxlen=RING_FRAMES)

    async def start(self) -> None:
        import sounddevice as sd  # optional dependency: driveos[voice]

        self._loop = asyncio.get_running_loop()

        def make_cb(channels: int):
            def cb(indata, frames, t, status):  # PortAudio thread
                if status:
                    log.debug("mic status %s", status)
                data = bytes(indata)
                ch1 = None
                if channels == 2:
                    a = array("h")
                    a.frombytes(data)
                    data, ch1 = a[0::2].tobytes(), a[1::2].tobytes()
                assert self._loop is not None
                self._loop.call_soon_threadsafe(self._push, data, ch1)
            return cb

        want = 1
        try:
            info = sd.query_devices(self.device, "input")
            self.device_name = str(info.get("name", ""))
            want = 2 if int(info["max_input_channels"]) >= 2 else 1
        except Exception:
            pass
        for channels in ([want, 1] if want == 2 else [1]):
            try:
                self._stream = sd.RawInputStream(samplerate=self.rate, blocksize=FRAME_SAMPLES, channels=channels,
                                                 dtype="int16", device=self.device, callback=make_cb(channels))
                self._stream.start()
                self.channels = channels
                break
            except Exception:
                if channels == 1:
                    raise
                log.info("microphone: 2-channel open failed, falling back to 1 channel")
        log.info("microphone open (%s, %d ch)", self.device or "default", self.channels)

    def _push(self, data: bytes, ch1: bytes | None = None) -> None:
        self.ring.append((self.seq, data, ch1))
        self.seq += 1
        if self._q.full():
            self._q.get_nowait()  # drop oldest rather than block
        self._q.put_nowait(data)

    def frames_since(self, seq: int) -> list[tuple[int, bytes, bytes | None]]:
        return [f for f in self.ring if f[0] >= seq]

    async def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            yield await self._q.get()

    def drain(self) -> None:
        while not self._q.empty():
            self._q.get_nowait()


def rms_level(frame: bytes) -> float:
    """0..1 loudness for the UI waveform (dBFS mapped from -60..0)."""
    a = array("h")
    a.frombytes(frame)
    if not a:
        return 0.0
    ms = sum(x * x for x in a[::4]) / max(1, len(a[::4]))
    db = 10 * math.log10(ms / (32768.0 ** 2) + 1e-12)
    return max(0.0, min(1.0, (db + 60.0) / 60.0))


class Endpointer:
    """Decides when an utterance starts and ends. WebRTC VAD when available, energy fallback otherwise."""

    def __init__(self, aggressiveness: int = 2, end_silence_ms: int = 700, start_timeout_s: float = 6.0,
                 max_utterance_s: float = 9.0):
        self.end_frames = max(1, end_silence_ms // FRAME_MS)
        self.start_frames = int(start_timeout_s * 1000 / FRAME_MS)
        self.max_frames = int(max_utterance_s * 1000 / FRAME_MS)
        try:
            import webrtcvad

            self._vad = webrtcvad.Vad(aggressiveness)
        except Exception:
            self._vad = None
        self.reset()

    def reset(self) -> None:
        self.speaking = False
        self.silence = 0
        self.voiced = 0
        self.total = 0
        self.spoken = 0
        self.audio = bytearray()
        self._pre: list[bytes] = []

    def _is_speech(self, frame: bytes) -> bool:
        if self._vad is not None:
            try:
                return self._vad.is_speech(frame, RATE)
            except Exception:
                pass
        return rms_level(frame) > 0.45

    def feed(self, frame: bytes) -> str:
        """Returns 'wait' | 'speech' | 'end' | 'timeout'."""
        self.total += 1
        speech = self._is_speech(frame)
        if not self.speaking:
            self._pre.append(frame)
            self._pre = self._pre[-15:]  # keep 300 ms of lead-in
            self.voiced = self.voiced + 1 if speech else 0
            if self.voiced >= 3:
                self.speaking = True
                self.audio.extend(b"".join(self._pre))
                return "speech"
            return "timeout" if self.total > self.start_frames else "wait"
        self.audio.extend(frame)
        self.spoken += 1
        self.silence = 0 if speech else self.silence + 1
        if self.silence >= self.end_frames or self.spoken > self.max_frames:   # cap measured from speech onset
            return "end"
        return "speech"
