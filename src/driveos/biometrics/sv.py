"""Speaker embeddings (ONNX Runtime, CPU). Primary ReDimNet2-B6; fallback WeSpeaker ResNet34-LM."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import BiometricsConfig
from .fbank import fbank
from .models import redimnet_path, wespeaker_path

log = logging.getLogger("driveos.biometrics.sv")

RATE = 16000
REDIMNET_SAMPLES = 6 * RATE


class SpeakerEmbedder(Protocol):
    name: str
    dim: int

    def embed(self, audio: np.ndarray) -> np.ndarray: ...


def l2(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(l2(a), l2(b)))


def fit_window(audio: np.ndarray, n: int) -> np.ndarray:
    """Fixed-length input: repeat short speech to fill the window, centre-crop long speech."""
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(a) == 0:
        return np.zeros(n, dtype=np.float32)
    if len(a) >= n:
        s = (len(a) - n) // 2
        return a[s:s + n]
    reps = -(-n // len(a))
    return np.tile(a, reps)[:n]


def _session(path: Path, threads: int):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.intra_op_num_threads = threads
    so.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])


class ReDimNetEmbedder:
    name = "redimnet2-b6"
    dim = 192

    def __init__(self, path: Path, threads: int = 4):
        self._s = _session(path, threads)

    def embed(self, audio: np.ndarray) -> np.ndarray:
        x = fit_window(audio, REDIMNET_SAMPLES)[None, :]
        return l2(self._s.run(None, {"audio": x})[0][0])


class WeSpeakerEmbedder:
    name = "wespeaker-resnet34-lm"
    dim = 256

    def __init__(self, path: Path, threads: int = 4):
        self._s = _session(path, threads)

    def embed(self, audio: np.ndarray) -> np.ndarray:
        feats = fbank(np.asarray(audio, dtype=np.float32))[None, :, :]
        return l2(self._s.run(None, {"fbank": feats})[0][0])


def build_embedder(cfg: BiometricsConfig, threads: int = 4) -> SpeakerEmbedder | None:
    order = ["redimnet2", "wespeaker"] if cfg.sv_backend == "redimnet2" else ["wespeaker", "redimnet2"]
    for kind in order:
        path = redimnet_path(cfg.models_dir) if kind == "redimnet2" else wespeaker_path(cfg.models_dir)
        if not path.is_file():
            continue
        try:
            emb = (ReDimNetEmbedder if kind == "redimnet2" else WeSpeakerEmbedder)(path, threads)
            if kind != cfg.sv_backend:
                log.warning("speaker model %s missing; using fallback %s", cfg.sv_backend, emb.name)
            return emb
        except Exception as e:
            log.error("speaker model %s failed to load: %s", kind, e)
    return None


def timed_embed(emb: SpeakerEmbedder, audio: np.ndarray) -> tuple[np.ndarray, float]:
    t = time.perf_counter()
    v = emb.embed(audio)
    return v, (time.perf_counter() - t) * 1000.0
