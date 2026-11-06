"""Anti-spoof countermeasure (AASIST-L, ONNX). A prefilter only: it is trained on ASVspoof2019 LA and does not
generalise to modern voice clones, so it never replaces the digit challenge or the channel check."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import BiometricsConfig
from .models import aasist_path
from .sv import fit_window

log = logging.getLogger("driveos.biometrics.cm")

AASIST_SAMPLES = 64600       # 4.04 s at 16 kHz, the model's fixed input


@dataclass
class CmResult:
    status: str                          # "ok" | "unavailable"
    bonafide: float | None = None        # P(bona fide) in [0, 1]
    spoof: bool = False                  # bona fide probability below the threshold
    detail: str = ""
    hard_spoof: bool = False             # below the hard floor (only when a floor is configured)
    unsure: bool = False                 # below the threshold but above the hard floor


def cm_input(ch0: np.ndarray, ch1: np.ndarray | None, channel: str = "ch0", ch1_gain: float = 1.0
             ) -> tuple[np.ndarray, str]:
    """The audio the countermeasure reads and the channel it really came from. ch1 is the XVF3800's mic 3 after
    echo cancellation only (about 26 dB below the processed ch0), so it gets ``ch1_gain``. A single-channel device
    falls back to ch0."""
    if channel == "ch0" or ch1 is None:
        return np.asarray(ch0, dtype=np.float32), "ch0"
    g = np.clip(np.asarray(ch1, dtype=np.float32) * float(ch1_gain), -1.0, 1.0)
    if channel == "ch1":
        return g, "ch1"
    n = min(len(ch0), len(g))
    return (np.asarray(ch0[:n], dtype=np.float32) + g[:n]) / 2.0, "mix"


def judge(r: CmResult, threshold: float, hard_floor: float = 0.0) -> CmResult:
    """Applies the device threshold to a bona fide probability. Below the threshold is a spoof flag, which only ever
    makes voice not-confident. With a hard floor, the band between floor and threshold is "unsure" (step-up), and
    only a probability under the floor is a hard spoof."""
    if r.status != "ok" or r.bonafide is None:
        return r
    spoof = r.bonafide < threshold
    hard = bool(spoof and hard_floor > 0 and r.bonafide < hard_floor)
    return CmResult(r.status, r.bonafide, spoof, r.detail, hard_spoof=hard, unsure=bool(spoof and hard_floor > 0 and not hard))


def apply_mode(r: CmResult, mode: str, threshold: float, hard_floor: float = 0.0) -> CmResult:
    """``gate``: the device threshold decides (see ``judge``). ``advisory``: the probability is kept for the log but the
    result never flags a spoof. ``off``: no score at all."""
    if mode == "off":
        return CmResult("off", None, False, "anti-spoof off")
    j = judge(r, threshold, hard_floor)
    if mode == "advisory" and j.status == "ok":
        return CmResult("advisory", j.bonafide, False, j.detail)
    return j


class Countermeasure(Protocol):
    name: str

    def check(self, audio: np.ndarray) -> CmResult: ...


class UnavailableCM:
    name = "none"

    def __init__(self, reason: str = "anti-spoof model not installed"):
        self.reason = reason

    def check(self, audio: np.ndarray) -> CmResult:
        return CmResult("unavailable", None, False, self.reason)


class AasistCM:
    name = "aasist-l"

    def __init__(self, path: Path, threshold: float = 0.5, threads: int = 2):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self._s = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
        self._in = self._s.get_inputs()[0].name
        self.threshold = threshold

    def check(self, audio: np.ndarray) -> CmResult:
        x = fit_window(audio, AASIST_SAMPLES)[None, :]
        out = np.asarray(self._s.run(None, {self._in: x})[0], dtype=np.float64).reshape(-1)
        # AASIST output is [spoof, bonafide] logits (label 1 = bona fide)
        z = out - out.max()
        p = np.exp(z) / np.exp(z).sum()
        bona = float(p[1]) if len(p) > 1 else float(p[0])
        return CmResult("ok", bona, bona < self.threshold, f"bona fide {bona:.2f}")


def build_cm(cfg: BiometricsConfig) -> Countermeasure:
    path = aasist_path(cfg.models_dir)
    if not path.is_file():
        return UnavailableCM()
    try:
        return AasistCM(path, cfg.cm_spoof_threshold)
    except Exception as e:
        log.error("anti-spoof model failed to load: %s", e)
        return UnavailableCM(f"anti-spoof model failed to load: {e}")
