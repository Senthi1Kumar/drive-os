"""Speech-to-text with mudler/parakeet.cpp (NVIDIA parakeet-tdt_ctc-110m, GGUF), over its C ABI via ctypes.

The library (``libparakeet.so``, C ABI >= 5) is a native build, see ``scripts/build_parakeet.sh``. One model is
loaded once and shared by the command path and the wake-phrase spotter; ggml computes one transcription at a time.
Parakeet has no prompt biasing, so the ``vocabulary`` argument only feeds a conservative fuzzy name repair.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import re
import threading
from pathlib import Path

from ..config import VoiceConfig
from ..core.logging_setup import describe_text
from .entity_fix import entities_from_vocabulary, fix_entities

log = logging.getLogger("driveos.stt")

MIN_ABI = 5
MIN_SAMPLES = 1600            # under 0.1 s there is nothing to transcribe
LIB_NAME = "libparakeet.so"
MODEL_NAME = "tdt_ctc-110m-q8_0.gguf"
MODEL_REPO = "mudler/parakeet-cpp-gguf"
MODEL_REVISION = "07a08a3a405d20c5434b9a1442f6e6cf571f57ad"
MODEL_SHA256 = "614feee3a990cf0e672b0314f4da0c80ae8da9094507f5ccb7c42e43b5fc5a12"
_CONTROL = re.compile(r"<\|[^|>]{0,24}\|>")


def _models_default() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "drive-os" / "models"


def resolve_paths(cfg: VoiceConfig, models_dir: Path | None = None) -> tuple[Path | None, Path | None]:
    """(lib, model), each from config, then DRIVEOS_PARAKEET_LIB / _MODEL, then <models_dir>/parakeet/.
    A path is returned only when it exists as a file."""
    default = (models_dir or _models_default()) / "parakeet"
    out = []
    for configured, env, fallback in ((cfg.parakeet_lib, "DRIVEOS_PARAKEET_LIB", default / LIB_NAME),
                                      (cfg.parakeet_model, "DRIVEOS_PARAKEET_MODEL", default / MODEL_NAME)):
        cands = [Path(os.path.expanduser(str(p))) for p in (configured, os.environ.get(env)) if p]
        cands.append(fallback)
        out.append(next((p for p in cands if p.is_file()), None))
    return out[0], out[1]


def fetch_model(models_dir: Path | None = None) -> Path:
    """Download the pinned q8_0 GGUF into <models_dir>/parakeet/ and check its sha256."""
    import hashlib

    from huggingface_hub import hf_hub_download

    dest = (models_dir or _models_default()) / "parakeet"
    dest.mkdir(parents=True, exist_ok=True)
    path = Path(hf_hub_download(MODEL_REPO, MODEL_NAME, revision=MODEL_REVISION, local_dir=dest))
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != MODEL_SHA256:
        raise RuntimeError(f"{path.name} sha256 {h.hexdigest()[:12]}… does not match the pinned model")
    return path


def _preload_ggml(lib: Path) -> None:
    """Load the ggml shipped beside libparakeet first so a different ggml on LD_LIBRARY_PATH cannot win."""
    for name in ("libggml-base.so", "libggml-cpu.so", "libggml.so"):
        p = lib.parent / name
        if p.exists():
            try:
                ctypes.CDLL(str(p.resolve()), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


class ParakeetSTT:
    name = "parakeet"

    def __init__(self, lib_path: str | Path, model_path: str | Path, *, loader=ctypes.CDLL):
        lib_path = Path(lib_path)
        _preload_ggml(lib_path)
        lib = self._lib = loader(str(lib_path))
        lib.parakeet_capi_abi_version.restype = ctypes.c_int
        abi = int(lib.parakeet_capi_abi_version())
        if abi < MIN_ABI:
            raise RuntimeError(f"libparakeet C ABI {abi} is older than {MIN_ABI}")
        lib.parakeet_capi_load.restype = ctypes.c_void_p
        lib.parakeet_capi_load.argtypes = [ctypes.c_char_p]
        lib.parakeet_capi_free.argtypes = [ctypes.c_void_p]
        lib.parakeet_capi_free_string.argtypes = [ctypes.c_void_p]
        lib.parakeet_capi_last_error.restype = ctypes.c_char_p
        lib.parakeet_capi_last_error.argtypes = [ctypes.c_void_p]
        lib.parakeet_capi_transcribe_pcm.restype = ctypes.c_void_p
        lib.parakeet_capi_transcribe_pcm.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float),
                                                     ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self._ctx = lib.parakeet_capi_load(str(model_path).encode())
        if not self._ctx:
            raise RuntimeError(f"parakeet model did not load: {self._last_error()}")
        self._lock = threading.Lock()
        self.model_name = Path(model_path).name
        log.info("parakeet %s (C ABI %d)", self.model_name, abi)

    def _last_error(self) -> str:
        try:
            err = self._lib.parakeet_capi_last_error(self._ctx)
            return err.decode("utf-8", "replace") if err else "unknown"
        except Exception:
            return "unknown"

    def transcribe_sync(self, pcm16: bytes) -> str:
        """16 kHz mono int16 bytes to raw parakeet text (no name repair)."""
        import numpy as np

        x = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        if x.size < MIN_SAMPLES:
            return ""
        x = np.ascontiguousarray(x)
        with self._lock:                       # one ggml compute at a time; the model is shared with wake
            ptr = self._lib.parakeet_capi_transcribe_pcm(
                self._ctx, x.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), x.size, 16000, 0)
        if not ptr:
            raise RuntimeError(f"parakeet transcribe failed: {self._last_error()}")
        try:
            raw = ctypes.cast(ptr, ctypes.c_char_p).value or b""
        finally:
            self._lib.parakeet_capi_free_string(ctypes.c_void_p(ptr))
        return re.sub(r"\s+", " ", _CONTROL.sub(" ", raw.decode("utf-8", "replace"))).strip()

    def _run(self, pcm16: bytes, vocabulary: str) -> str:
        text = self.transcribe_sync(pcm16)
        if text and vocabulary and not _numeric_context(vocabulary):
            fixed = fix_entities(text, entities_from_vocabulary(vocabulary))
            if fixed != text:
                log.debug("parakeet: names repaired (%s)", describe_text(fixed))
            text = fixed
        log.debug("parakeet: %s", describe_text(text))
        return text

    async def transcribe(self, pcm16: bytes, *, vocabulary: str = "") -> str:
        return await asyncio.to_thread(self._run, pcm16, vocabulary)

    def close(self) -> None:
        with self._lock:
            if self._ctx:
                self._lib.parakeet_capi_free(ctypes.c_void_p(self._ctx))
                self._ctx = None


def _numeric_context(vocabulary: str) -> bool:
    """A pending one-time code (or the digit challenge) must reach the parser exactly as heard."""
    from ..agent.nova import OTP_VOCABULARY
    from ..biometrics.liveness import DIGIT_PROMPT

    return vocabulary in (OTP_VOCABULARY, DIGIT_PROMPT)
