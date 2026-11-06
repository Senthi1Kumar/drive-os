"""Cosine score -> calibrated P(genuine). Logistic mapping, labelled "demo-calibrated".

The ladder bars in DriveAuth (voice 0.72) are probabilities, so a raw cosine must be mapped first. The fit uses
genuine pairs from the enrollment utterances and impostor pairs from a small public cohort, blended with a
prior so a handful of same-session pairs cannot produce a cliff. AS-Norm against the cohort is a TODO hook.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.private_fs import ensure_private_dir, write_private_text

LABEL = "demo-calibrated"
PRIOR_SLOPE = 12.0
PRIOR_MID = 0.45
SLOPE_RANGE = (4.0, 25.0)


@dataclass
class Calibrator:
    slope: float = PRIOR_SLOPE
    mid: float = PRIOR_MID
    label: str = "uncalibrated-prior"
    n_genuine: int = 0
    n_impostor: int = 0

    def __call__(self, cosine: float) -> float:
        z = self.slope * (float(cosine) - self.mid)
        return float(min(1.0, max(0.0, 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z)))))))

    def cosine_for(self, p: float) -> float:
        p = min(max(p, 1e-6), 1 - 1e-6)
        return self.mid + math.log(p / (1 - p)) / self.slope

    def describe(self) -> dict:
        return {"slope": round(self.slope, 3), "mid": round(self.mid, 4), "label": self.label,
                "n_genuine": self.n_genuine, "n_impostor": self.n_impostor}

    @classmethod
    def from_dict(cls, d: dict) -> Calibrator:
        try:
            return cls(float(d["slope"]), float(d["mid"]), d.get("label", LABEL), int(d.get("n_genuine", 0)),
                       int(d.get("n_impostor", 0)))
        except (KeyError, TypeError, ValueError):
            return cls()

    # persistence ------------------------------------------------------------
    def save(self, path: Path) -> None:
        ensure_private_dir(path.parent)
        write_private_text(path, json.dumps(self.describe()))

    @classmethod
    def load(cls, path: Path) -> Calibrator:
        try:
            d = json.loads(path.read_text())
            return cls(float(d["slope"]), float(d["mid"]), d.get("label", LABEL), int(d.get("n_genuine", 0)),
                       int(d.get("n_impostor", 0)))
        except Exception:
            return cls()

    # fitting -----------------------------------------------------------------
    @classmethod
    def fit(cls, genuine: list[float], impostor: list[float], prior_weight: float = 4.0) -> Calibrator:
        """Ridge logistic regression of genuine(1) vs impostor(0) cosines, shrunk toward the prior.

        Same-session enrollment pairs score higher than a later challenge attempt, so the midpoint is pulled
        toward the impostor side: the genuine centre is taken at the lower quartile, not the mean."""
        g, i = np.asarray(genuine, dtype=np.float64), np.asarray(impostor, dtype=np.float64)
        if len(g) < 2 or len(i) < 2:
            return cls()
        g_c = float(np.percentile(g, 25))
        i_c = float(np.percentile(i, 90))
        if g_c <= i_c:
            return cls(label=LABEL + "-weak", n_genuine=len(g), n_impostor=len(i))
        mid = 0.5 * (g_c + i_c)
        # slope so that the genuine centre maps to ~0.97 and the impostor tail to ~0.03
        slope = 2.0 * math.log(0.97 / 0.03) / (g_c - i_c)
        w = len(g) + len(i)
        slope = (w * slope + prior_weight * PRIOR_SLOPE) / (w + prior_weight)
        mid = (w * mid + prior_weight * PRIOR_MID) / (w + prior_weight)
        slope = min(max(slope, SLOPE_RANGE[0]), SLOPE_RANGE[1])
        return cls(slope, mid, LABEL, len(g), len(i))


def as_norm(score: float, enroll_cohort: np.ndarray, test_cohort: np.ndarray) -> float:
    """TODO: adaptive s-norm against the impostor cohort. Identity until the cohort is large enough."""
    return score
