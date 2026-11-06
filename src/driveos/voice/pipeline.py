"""Voice loop, hands-free from power-on.

    armed ──"Hey Nova"──▶ conversation ──▶ (listen ▶ turn judged complete ▶ transcribe ▶ Nova ▶ speak) ▶ listen…
      ▲                        │
      └── End / mute / 60 s without speech ◀──┘

* The app starts *armed*: the microphone is open and the wake phrase is all it takes. The wake spotter runs only
  while armed.
* "Hey Nova, find me a coffee" works in one breath — the words after the wake phrase are the request.
* After the wake phrase (or the wheel button) the conversation stays open: Silero VAD segments speech, Smart Turn
  judges whether a pause ends the turn (an incomplete pause resumes the same utterance), and the conversation
  closes on End, mute, or ``conversation_idle_s`` without speech. Utterances are bounded by
  ``conversation_max_utterance_s``. Audio stays in RAM.
* A spoken one-time code never goes through Smart Turn: it uses the legacy WebRTC endpointer and the local
  Whisper only. ``vad = "webrtc"`` (or missing models) selects that legacy path for everything.
* The wheel button (Space/F12 on a keyboard) opens a turn at any time and barges in on speech.
* Half-duplex: the mic is ignored while Nova speaks (cars without echo cancellation would hear her).
* A short echo tail (``echo_tail_ms``) after playback keeps her own voice out of the next turn.
* An empty transcription never reopens listening; only a wheel-button turn gets one quiet retry hint.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import numpy as np

from ..config import VoiceConfig
from ..core.logging_setup import describe_text
from .audio import AudioSource, Endpointer, rms_level
from .stt import SpeechToText

if TYPE_CHECKING:
    from ..agent.nova import Nova

log = logging.getLogger("driveos.voice")


INCOMPLETE_COMMIT_S = 3.0   # an "incomplete" pause that nobody continues is committed after this long


class VoicePipeline:
    def __init__(self, cfg: VoiceConfig, nova: Nova, *, mic: AudioSource | None, stt: SpeechToText | None,
                 wake=None, level_cb=None, segmenter=None, turn=None):
        self.cfg = cfg
        self.nova = nova
        self.mic = mic
        self.stt = stt
        self.wake = wake
        self.level_cb = level_cb
        self._listen = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._follow_until = 0.0
        self._explicit_turn = False  # the wheel button opened this turn: one apology is allowed
        self._apologized = False     # the one-shot apology for this explicit episode was spoken
        self._speak_end = 0.0        # last frame seen while Nova was speaking (echo tail)
        self.state = "armed"
        self.paused = False         # set while Voice ID enrollment owns the microphone
        # continuous conversation (Silero + Smart Turn); both stay None on the legacy WebRTC path
        self._seg = segmenter
        self._turn_an = turn
        self._conv_active = False
        self._muted = False
        self._rev = 0                       # bumped by speech, commit, End and mute: stale work compares and drops
        self._pause_audio: np.ndarray | None = None   # the utterance awaiting (or held after) a turn decision
        self._incomplete_at = 0.0           # when the turn detector last judged the pause incomplete (0 = undecided)
        self._queued: tuple[int, np.ndarray] | None = None   # the latest pause waiting for the inference worker
        self._infer: tuple[int, asyncio.Future] | None = None
        self._last_activity = 0.0
        self._resume_at = 0.0               # mic stays closed until here (echo tail after playback)
        self._need_drain = False
        self._explicit_conv = False
        self._pause_at = 0.0
        self.stats: dict[str, float] = {}   # last measured latencies (ms), no content
        nova.request_listen = self.listen
        nova.request_follow_up = self.follow_up

    @property
    def available(self) -> bool:
        return self.mic is not None and self.stt is not None

    @property
    def conversation_active(self) -> bool:
        return self._conv_active

    @property
    def muted(self) -> bool:
        return self._muted

    def listen(self) -> None:
        """Wheel button / mic tap: open a turn now (barges in on speech)."""
        if self.nova.voice.mode in ("speaking", "processing"):
            self.nova.barge_in()
        self._explicit_turn = True
        self._apologized = False  # a fresh button press re-arms the single apology
        self._listen.set()

    def follow_up(self) -> None:
        self._follow_until = time.monotonic() + self.cfg.follow_up_s
        self._listen.set()

    def end(self) -> None:
        """End the conversation now: drop pending audio and inference, stop speech, and go back to armed."""
        self._rev += 1
        self._conv_active = False
        self._clear_turn_work()
        if self._seg is not None:
            self._seg.reset()
        self._set_speaking(False)
        self.nova.voice.continuous = False
        self._listen.clear()
        self._follow_until = 0.0
        self._explicit_turn = False
        self._explicit_conv = False
        self.state = "armed"
        self.nova.barge_in()
        self.nova.set_voice("idle")

    def set_muted(self, muted: bool) -> None:
        """Mute closes the microphone to everything, including the wake phrase; unmuting returns to armed."""
        self._muted = bool(muted)
        if self._muted:
            self.end()

    async def start(self) -> None:
        if not self.available:
            log.warning("voice input unavailable — use the typed console (press / in the app)")
            self.nova.set_voice_available(False)
            return
        assert self.mic is not None
        try:
            await self.mic.start()
        except Exception as e:
            log.error("microphone unavailable: %s", e)
            self.mic = None
            self.nova.set_voice_available(False)
            return
        self._build_continuous()
        self.nova.set_voice_available(True, wake=getattr(self.wake, "kind", None) is not None,
                                      phrase=self.cfg.wake_phrase if self.cfg.wake == "phrase" else
                                      self.cfg.wake_model.replace("_", " "))
        self._task = asyncio.create_task(self._run(), name="voice")

    def _build_continuous(self) -> None:
        if self._seg is None and self.cfg.vad == "silero":
            try:
                from .silero_vad import SileroVadConfig, SileroVadSegmenter

                self._seg = SileroVadSegmenter(SileroVadConfig(
                    max_utterance_s=self.cfg.conversation_max_utterance_s, onnx_path=self.cfg.silero_model))
            except Exception as e:
                log.warning("Silero VAD unavailable (%s): using the legacy endpointer", e)
        if self._seg is not None and self._turn_an is None and self.cfg.turn_detector == "smart":
            try:
                from .smart_turn import SmartTurnAnalyzer

                self._turn_an = SmartTurnAnalyzer(self.cfg.smart_turn_model)
            except Exception as e:
                log.warning("Smart Turn unavailable (%s): every pause will end the turn", e)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.mic:
            await self.mic.stop()

    # ------------------------------------------------------------------ helpers
    def _otp_pending(self) -> bool:
        check = getattr(self.nova, "otp_pending", None)
        return bool(check()) if check else False

    def _set_speaking(self, on: bool) -> None:
        self.nova.voice.user_speaking = on

    def _clear_turn_work(self) -> None:
        self._pause_audio = None
        self._incomplete_at = 0.0
        self._queued = None
        if self._infer is not None and self._infer[1].done():
            self._infer = None      # a worker still running keeps the slot: stale work never piles up behind it

    def _drop_capture(self) -> None:
        """Forget audio that must not become part of a turn (playback echo, a superseded utterance)."""
        if self._seg is not None:
            self._seg.reset()
        self._clear_turn_work()
        self._set_speaking(False)

    def _drain_mic(self) -> None:
        drain = getattr(self.mic, "drain", None)
        if drain:
            drain()

    def _activate(self) -> None:
        """Open (or restart) the conversation: fresh capture, idle clock running."""
        self._rev += 1
        self._conv_active = True
        self.nova.voice.continuous = True
        self._drop_capture()
        self._drain_mic()
        self._last_activity = time.monotonic()
        self._explicit_conv = self._explicit_turn
        self._explicit_turn = False
        self.state = "listening"
        self.nova.set_voice("listening", keep=True)

    def _deactivate(self) -> None:
        """Inactivity: back to armed (the wake phrase reopens it)."""
        self._rev += 1
        self._conv_active = False
        self.nova.voice.continuous = False
        self._drop_capture()
        self.state = "armed"
        self.nova.set_voice("idle")
        log.info("conversation closed: no speech for %.0f s", self.cfg.conversation_idle_s)

    # ------------------------------------------------------------------ main loop
    async def _run(self) -> None:
        assert self.mic is not None
        frames = self.mic.frames()
        spot_ep = Endpointer(self.cfg.vad_aggressiveness, 450, 3600.0, 4.0)
        last_level = 0.0
        tail_s = self.cfg.echo_tail_ms / 1000
        while True:
            frame = await anext(frames)
            now = time.monotonic()
            if self.paused or getattr(self.nova, "bio_capture", False):
                spot_ep.reset()     # a biometric challenge owns the microphone
                self._drop_capture()
                continue
            if self.nova.voice.mode == "speaking":
                spot_ep.reset()     # Nova is talking: drop endpointer state, timestamp the echo tail
                self._speak_end = now
                self._resume_at = now + tail_s
                self._last_activity = now
                self._need_drain = True
                self._drop_capture()
                continue
            if self._muted:
                self._listen.clear()
                spot_ep.reset()
                continue
            if self._listen.is_set():
                self._listen.clear()
                if self._seg is None or self._otp_pending():
                    spot_ep.reset()
                    await self._turn(frames)    # legacy fixed-window turn (also the spoken-OTP path)
                    continue
                if not self._conv_active or self._explicit_turn:
                    self._activate()
            if self._conv_active:
                last_level = await self._conversation_frame(frame, frames, now, last_level)
                continue
            if self.level_cb and now - last_level > 0.06:
                last_level = now
                self.level_cb(0.0)
            if self.wake is None:
                continue
            if getattr(self.wake, "kind", "") == "model":
                if self.wake.feed(frame):
                    self.wake.reset()
                    await self._woke("")
                    await self._after_wake(frames)
                continue
            # phrase spotting on short speech segments
            r = spot_ep.feed(frame)
            if r in ("end",):
                audio = bytes(spot_ep.audio)
                spot_ep.reset()
                if len(audio) < 16000 * 2 * 0.35:
                    continue
                try:
                    woke, rest, heard = await self.wake.check(audio)
                except Exception:
                    log.exception("wake check failed")
                    continue
                if not woke:
                    continue
                log.info("wake: %s", describe_text(heard))
                if rest.strip():
                    await self._woke(rest)
                    if self._seg is not None:
                        self._activate()
                    await self.nova.handle_text(rest)
                    await self._after_response(self._rev)
                else:
                    await self._woke("")
                    await self._after_wake(frames)
            elif r == "timeout":
                spot_ep.reset()

    async def _after_wake(self, frames) -> None:
        if self._seg is None or self._otp_pending():
            await self._turn(frames)
        else:
            self._activate()

    async def _woke(self, rest: str) -> None:
        self._apologized = False
        await self.nova.wake(rest)

    # ------------------------------------------------------------------ continuous conversation
    async def _conversation_frame(self, frame: bytes, frames, now: float, last_level: float) -> float:
        nova = self.nova
        if self._otp_pending():
            self._drop_capture()                # a spoken code uses the legacy local capture, never Smart Turn
            await self._turn(frames)
            return last_level
        if now < self._resume_at:
            self._drop_capture()
            return last_level
        if self._need_drain:
            self._need_drain = False
            self._drain_mic()
            self._drop_capture()
            return last_level
        if nova.voice.mode == "idle":
            self.state = "listening"
            nova.set_voice("listening", keep=True)
        assert self._seg is not None
        speaking = getattr(self._seg, "speaking", False)
        if not speaking and self._pause_audio is None and now - self._last_activity >= self.cfg.conversation_idle_s:
            self._deactivate()
            return last_level
        if self.level_cb and now - last_level > 0.06:
            last_level = now
            self.level_cb(rms_level(frame))
        for kind, audio in self._seg.feed(frame):
            if kind == "speech_started":
                self._on_speech_started(now)
            elif kind == "pause":
                self._pause_audio = audio
                self._incomplete_at = 0.0
                self._pause_at = now
                if self._turn_an is None:
                    await self._commit(audio)
                    return last_level
                self._queued = (self._rev, audio)
            elif kind == "speech_stopped" and audio is not None:
                await self._commit(audio)
                return last_level
        if self._incomplete_at and getattr(self._seg, "speaking", False):
            self._on_speech_started(now)        # the same utterance carried on after an incomplete pause
        await self._poll_decision(now)
        return last_level

    def _on_speech_started(self, now: float) -> None:
        self._rev += 1                          # any decision still in flight belongs to the old pause
        self._last_activity = now
        self._set_speaking(True)
        if self._pause_audio is not None and not self._incomplete_at:
            self._seg.resume()                  # a pause the detector had not judged yet: merge it back
        self._pause_audio = None
        self._incomplete_at = 0.0
        self._queued = None

    async def _poll_decision(self, now: float) -> None:
        if self._queued is not None and (self._infer is None or self._infer[1].done()):
            rev, audio = self._queued
            self._queued = None
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
            started = time.monotonic()
            fut = asyncio.ensure_future(asyncio.to_thread(self._judge, pcm, started))
            self._infer = (rev, fut)
        if self._infer is not None and self._infer[1].done():
            rev, fut = self._infer
            self._infer = None
            if rev != self._rev or self._pause_audio is None:
                return                          # speech, End or a commit superseded this pause
            try:
                complete = fut.result()
            except Exception:
                log.exception("turn detection failed; ending the turn")
                complete = True
            if complete:
                await self._commit(self._pause_audio)
            else:
                self._incomplete_at = now
                self._seg.resume()
        elif self._pause_audio is not None and self._incomplete_at and \
                not getattr(self._seg, "speaking", False) and now - self._incomplete_at >= INCOMPLETE_COMMIT_S:
            await self._commit(self._pause_audio)

    def _judge(self, pcm: bytes, started: float) -> bool:
        complete = self._turn_an.complete(pcm)
        self.stats["turn_ms"] = (time.monotonic() - started) * 1000
        return complete

    async def _commit(self, audio: np.ndarray) -> None:
        """The turn is over: transcribe it, hand it to Nova, and come back to listening."""
        assert self.stt is not None and self._seg is not None
        self._rev += 1
        rev = self._rev
        pause_at = getattr(self, "_pause_at", 0.0)
        self._clear_turn_work()
        self._seg.reset()
        self._set_speaking(False)
        self.state = "processing"
        self.nova.set_voice("processing")
        if pause_at:
            self.stats["pause_to_thinking_ms"] = (time.monotonic() - pause_at) * 1000
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        try:
            text = await self.stt.transcribe(pcm, vocabulary=self.nova.vocabulary())
        except Exception:
            log.exception("transcription failed")
            text = ""
        if rev != self._rev or not self._conv_active:
            return                              # End, mute or a button restart arrived while transcribing
        explicit, self._explicit_conv = self._explicit_conv, False
        self._last_activity = time.monotonic()
        if not text.strip():
            self.state = "listening"
            self.nova.set_voice("listening", keep=True)
            if explicit and not self._apologized:
                self._apologized = True  # one shot, and it never schedules another turn
                await self.nova.say("I didn't catch that; please try again.", listen=False)
                await self._after_response(rev)
            return
        self._apologized = False
        await self.nova.handle_text(text)
        await self._after_response(rev)

    async def _after_response(self, rev: int) -> None:
        if rev != self._rev or not self._conv_active:
            return                              # ended meanwhile: nothing may reopen listening
        self._drop_capture()
        self._need_drain = True
        self._resume_at = max(self._resume_at, time.monotonic() + self.cfg.echo_tail_ms / 1000)
        self._last_activity = time.monotonic()
        self.state = "listening"

    # ------------------------------------------------------------------ legacy fixed-window turn
    async def _turn(self, frames) -> None:
        assert self.stt is not None
        explicit = self._explicit_turn
        self._explicit_turn = False
        follow = time.monotonic() < self._follow_until
        tail = self._speak_end + self.cfg.echo_tail_ms / 1000 - time.monotonic()
        if tail > 0:
            await asyncio.sleep(tail)   # let playback echo decay before opening the mic
        self._drain_mic()
        ep = Endpointer(self.cfg.vad_aggressiveness, self.cfg.end_silence_ms,
                        self.cfg.follow_up_s if follow else 6.0, self.cfg.max_utterance_s)
        self.state = "listening"
        self.nova.set_voice("listening", keep=self._conv_active)
        rev0 = self._rev
        result = "wait"
        while result in ("wait", "speech"):
            frame = await anext(frames)
            if self._rev != rev0:
                return                  # End, mute or a restart superseded this turn
            if self.level_cb:
                self.level_cb(rms_level(frame))
            result = ep.feed(frame)
        if self.level_cb:
            self.level_cb(0.0)
        self._follow_until = 0.0
        if result == "timeout" or len(ep.audio) < 16000 * 2 * 0.3:
            self.state = "listening" if self._conv_active else "armed"
            self.nova.set_voice("listening" if self._conv_active else "idle", keep=True)
            return
        self.state = "processing"
        self.nova.set_voice("processing")
        try:
            text = await self.stt.transcribe(bytes(ep.audio), vocabulary=self.nova.vocabulary())
        except Exception:
            log.exception("transcription failed")
            text = ""
        if self._rev != rev0:
            return
        self.state = "listening" if self._conv_active else "armed"
        if not text.strip():
            self.nova.set_voice("listening" if self._conv_active else "idle", keep=True)
            if explicit and not self._apologized:
                self._apologized = True  # one shot, and it never schedules another turn
                await self.nova.say("I didn't catch that; please try again.", listen=False)
            return
        self._apologized = False
        await self.nova.handle_text(text)
