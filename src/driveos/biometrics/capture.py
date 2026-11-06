"""Challenge / enrollment capture from the microphone ring buffer (both channels) with robust endpointing.

The plain voice-loop endpointer opens on 60 ms of VAD "speech" and closes after a short silence, so on an
AGC'd array mic a keyboard tap or room noise produced 0.1 s "utterances" before the driver had spoken. Here a
take only starts on sustained speech that is clearly above the room's noise floor, and a burst shorter than
``min_speech_s`` is discarded so capture keeps listening for the real answer.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..voice.audio import FRAME_MS, RATE


class RingSource(Protocol):
    """The Microphone's ring buffer: ``seq`` frames pushed so far; ``ring`` holds (seq, ch0, ch1|None) bytes."""

    seq: int

    def frames_since(self, seq: int) -> list[tuple[int, bytes, bytes | None]]: ...


@dataclass
class Capture:
    ch0: np.ndarray                  # float32 [-1, 1]
    ch1: np.ndarray | None
    voiced: bool
    seconds: float

    @property
    def pcm16(self) -> bytes:
        return (np.clip(self.ch0, -1, 1) * 32767).astype(np.int16).tobytes()


def _f32(chunks: list[bytes]) -> np.ndarray:
    return np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0


def _rms(frame: bytes) -> float:
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def _vad_fn(aggressiveness: int) -> Callable[[bytes], bool]:
    try:
        import webrtcvad

        vad = webrtcvad.Vad(aggressiveness)

        def is_speech(frame: bytes) -> bool:
            try:
                return vad.is_speech(frame, RATE)
            except Exception:
                return False

        return is_speech
    except Exception:
        return lambda frame: _rms(frame) > 1500.0


LEAD_IN_FRAMES = 15        # 300 ms kept before onset so the first syllable isn't clipped
ONSET_FRAMES = 10          # 200 ms of consecutive loud speech to open a take
FLOOR_FRAMES = 15          # first 300 ms of quiet estimate the room floor
ONSET_OVER_FLOOR = 3.0     # a speech frame must be ~10 dB above the floor to count towards onset
HOLD_OVER_FLOOR = 1.8      # ...and ~5 dB above it to keep a take open


async def capture_utterance(src: RingSource, *, start_timeout_s: float = 6.0, max_s: float = 6.0,
                            end_silence_ms: int = 500, vad: int = 3, poll_s: float = 0.02,
                            min_speech_s: float = 0.6,
                            speech_fn: Callable[[bytes], bool] | None = None) -> Capture:
    """Waits for sustained speech, returns once it ends (or ``max_s`` after onset).

    Bursts with less than ``min_speech_s`` of speech are discarded and listening continues until
    ``start_timeout_s`` passes with no accepted take, which returns an unvoiced capture.
    """
    is_speech = speech_fn or _vad_fn(vad)
    end_frames = max(1, end_silence_ms // FRAME_MS)
    max_frames = int(max_s * 1000 / FRAME_MS)
    min_frames = int(min_speech_s * 1000 / FRAME_MS)
    deadline = time.monotonic() + start_timeout_s + max_s + 2

    seq = src.seq
    floor: float | None = None
    floor_buf: list[float] = []
    pre0: list[bytes] = []
    pre1: list[bytes | None] = []
    run = 0                                  # consecutive onset frames
    take0: list[bytes] = []
    take1: list[bytes | None] = []
    speech_frames = silence = 0
    in_take = False
    waited = 0                               # frames spent waiting with no take open

    def reset_take() -> None:
        nonlocal in_take, speech_frames, silence, run
        take0.clear()
        take1.clear()
        in_take, speech_frames, silence, run = False, 0, 0, 0

    while True:
        for s, a, b in src.frames_since(seq):
            seq = s + 1
            level = _rms(a)
            if floor is None:
                floor_buf.append(level)
                if len(floor_buf) >= FLOOR_FRAMES:
                    floor = max(float(np.median(floor_buf)), 30.0)
            ref = floor if floor is not None else max(float(np.median(floor_buf)), 30.0)
            voiced = is_speech(a)
            if not in_take:
                waited += 1
                pre0.append(a)
                pre1.append(b)
                del pre0[:-LEAD_IN_FRAMES], pre1[:-LEAD_IN_FRAMES]
                run = run + 1 if (voiced and level > ref * ONSET_OVER_FLOOR) else 0
                if run >= ONSET_FRAMES:
                    in_take = True
                    take0.extend(pre0)
                    take1.extend(pre1)
                    speech_frames = run
                    silence = 0
                elif waited * FRAME_MS / 1000 > start_timeout_s:
                    return Capture(np.zeros(0, np.float32), None, False, 0.0)
                elif floor is not None and not voiced:
                    floor = 0.98 * floor + 0.02 * max(level, 30.0)     # track slow changes in cabin noise
                continue
            take0.append(a)
            take1.append(b)
            if voiced and level > ref * HOLD_OVER_FLOOR:
                speech_frames += 1
                silence = 0
            else:
                silence += 1
            if silence >= end_frames or len(take0) > max_frames:
                if speech_frames >= min_frames:
                    return _build(take0, take1)
                reset_take()                  # a cough / tap / "uh": keep listening for the real answer
                pre0.clear()
                pre1.clear()
        if time.monotonic() > deadline:
            if in_take and speech_frames >= min_frames:
                return _build(take0, take1)
            return Capture(np.zeros(0, np.float32), None, False, 0.0)
        await asyncio.sleep(poll_s)


def _build(c0: list[bytes], c1: list[bytes | None]) -> Capture:
    second = _f32([x for x in c1 if x is not None]) if c1 and all(x is not None for x in c1) else None
    return Capture(_f32(c0), second, True, len(c0) * FRAME_MS / 1000)
