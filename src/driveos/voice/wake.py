"""Wake detection, on-device.

* ``PhraseSpotter`` (default) — the microphone's speech segments are transcribed by a tiny Whisper model
  and matched against the wake phrase ("hey nova") and common mis-hearings. Any phrase works without
  training, and "Hey Nova, find me a coffee" is handled in one breath: the words after the wake phrase
  become the first request.
* ``OpenWakeWord`` — a trained frame-level detector (lower CPU, fixed phrase such as "hey jarvis").

The steering-wheel voice button always works regardless.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

from ..config import VoiceConfig
from .stt import load_whisper

log = logging.getLogger("driveos.wake")


class WakeWord(Protocol):
    def feed(self, frame: bytes) -> bool: ...
    def reset(self) -> None: ...


class OpenWakeWord:
    CHUNK = 1280  # 80 ms at 16 kHz
    kind = "model"

    def __init__(self, cfg: VoiceConfig):
        import numpy as np
        from openwakeword.model import Model  # optional dependency: driveos[voice]

        self.np = np
        try:
            self.model = Model(wakeword_models=[cfg.wake_model], inference_framework="onnx")
        except Exception:
            from openwakeword.utils import download_models

            download_models(model_names=[cfg.wake_model])
            self.model = Model(wakeword_models=[cfg.wake_model], inference_framework="onnx")
        self.threshold = cfg.wake_threshold
        self._buf = bytearray()
        log.info("wake word model %s (threshold %.2f)", cfg.wake_model, self.threshold)

    def feed(self, frame: bytes) -> bool:
        self._buf.extend(frame)
        hit = False
        while len(self._buf) >= self.CHUNK * 2:
            chunk = self.np.frombuffer(bytes(self._buf[: self.CHUNK * 2]), dtype=self.np.int16)
            del self._buf[: self.CHUNK * 2]
            scores = self.model.predict(chunk)
            if any(v >= self.threshold for v in scores.values()):
                hit = True
        return hit

    def reset(self) -> None:
        self._buf.clear()
        try:
            self.model.reset()
        except Exception:
            pass


def _norm(t: str) -> str:
    t = t.lower().replace("’", "'")
    t = re.sub(r"[^a-z' ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def split_wake(text: str, phrase: str, aliases: list[str]) -> tuple[bool, str]:
    """(woke, remainder). Matches the phrase at the start of the utterance (or alone)."""
    t = _norm(text)
    for p in sorted({_norm(phrase), *(_norm(a) for a in aliases)}, key=len, reverse=True):
        if not p:
            continue
        m = re.match(rf"^(?:(?:um|uh|so|okay|ok)\s+)?{re.escape(p)}\b[\s,]*(.*)$", t)
        if m:
            # a bare "nova" alias only counts at the very start
            return True, m.group(1).strip()
    return False, ""


class PhraseSpotter:
    """Keyword spotting over VAD-delimited speech segments: the shared parakeet model when available
    (``wake_engine = "parakeet"``), else a small Whisper model."""

    kind = "phrase"

    def __init__(self, cfg: VoiceConfig, stt=None):
        self.phrase = cfg.wake_phrase
        self.aliases = cfg.wake_aliases
        self.model = None
        self._shared = stt if cfg.wake_engine == "parakeet" and hasattr(stt, "transcribe_sync") else None
        if self._shared is not None:
            self.engine = "parakeet"
            log.info('wake phrase "%s" (shared parakeet model)', self.phrase)
            return
        from faster_whisper import WhisperModel  # optional dependency: driveos[voice]

        self.engine = "whisper"
        self.model = load_whisper(WhisperModel, cfg.wake_whisper_model, device="cpu", compute_type="int8")
        log.info('wake phrase "%s" (whisper %s)', self.phrase, cfg.wake_whisper_model)

    def _run(self, pcm16: bytes) -> str:
        if self._shared is not None:
            return self._shared.transcribe_sync(pcm16)
        import numpy as np

        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                        condition_on_previous_text=False, without_timestamps=True,
                                        initial_prompt=f"{self.phrase.title()}, find me a coffee.")
        return " ".join(s.text.strip() for s in segs).strip()

    async def check(self, pcm16: bytes) -> tuple[bool, str, str]:
        text = await asyncio.to_thread(self._run, pcm16)
        woke, rest = split_wake(text, self.phrase, self.aliases)
        return woke, rest, text


def build_wake(cfg: VoiceConfig, stt=None):
    if cfg.wake == "none":
        return None
    try:
        return PhraseSpotter(cfg, stt) if cfg.wake == "phrase" else OpenWakeWord(cfg)
    except Exception as e:
        log.error("wake detection unavailable (%s) — use the wheel voice button or Space", e)
        return None
