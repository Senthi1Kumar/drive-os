"""Speech-to-text. faster-whisper runs fully on the edge box; the prompt is seeded with the live menu and
merchant names so domain words ("cortado", "Halfmoon", "oat milk") are recognised reliably."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from ..config import VoiceConfig

log = logging.getLogger("driveos.stt")


class SpeechToText(Protocol):
    name: str

    async def transcribe(self, pcm16: bytes, *, vocabulary: str = "") -> str: ...


def load_whisper(factory, name: str, *, device: str, compute_type: str):
    """Use the local cache when the model is already there, so a start never contacts Hugging Face."""
    try:
        return factory(name, device=device, compute_type=compute_type, local_files_only=True)
    except Exception:
        log.info("whisper %s not cached yet; downloading", name)
        return factory(name, device=device, compute_type=compute_type, local_files_only=False)


class WhisperSTT:
    name = "whisper"

    def __init__(self, cfg: VoiceConfig, beam_size: int = 1):
        from faster_whisper import WhisperModel  # optional dependency: driveos[voice]

        device = cfg.whisper_device
        if device == "auto":
            device = "cuda" if _has_cuda() else "cpu"
        self.model = load_whisper(WhisperModel, cfg.whisper_model, device=device, compute_type=cfg.whisper_compute_type)
        self.language = cfg.language
        self.beam_size = max(1, int(beam_size))
        log.info("whisper %s on %s (%s)", cfg.whisper_model, device, cfg.whisper_compute_type)

    def _run(self, pcm16: bytes, vocabulary: str) -> str:
        import numpy as np

        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            audio, language=self.language, beam_size=self.beam_size, vad_filter=False, condition_on_previous_text=False,
            initial_prompt=vocabulary[:600] or None, without_timestamps=True,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, pcm16: bytes, *, vocabulary: str = "") -> str:
        return await asyncio.to_thread(self._run, pcm16, vocabulary)


def _has_cuda() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def build_stt(cfg: VoiceConfig, models_dir: Path | None = None) -> SpeechToText | None:
    if cfg.stt == "none":
        return None
    if cfg.stt == "parakeet":
        from .parakeet_stt import ParakeetSTT, resolve_paths

        lib, model = resolve_paths(cfg, models_dir)
        try:
            if lib is None or model is None:
                missing = " and ".join(n for n, p in (("libparakeet.so", lib), ("the parakeet GGUF", model)) if p is None)
                raise FileNotFoundError(f"{missing} not found")
            return ParakeetSTT(lib, model)
        except Exception as e:
            log.warning("parakeet unavailable (%s); using faster-whisper %s. Run scripts/build_parakeet.sh and "
                        "`driveos fetch-models`", e, cfg.whisper_model)
    try:
        return WhisperSTT(cfg)
    except ImportError as e:
        log.error("speech recognition unavailable (%s) — run scripts/setup to install voice support", e)
        return None
    except Exception as e:
        log.error("speech recognition model %r could not load (%s) — the first run downloads it from "
                  "Hugging Face, so check the internet connection; typed commands still work", cfg.whisper_model, e)
        return None
