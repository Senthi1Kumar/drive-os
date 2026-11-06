"""Per-device anti-spoof threshold, written by ``driveos voice-check --apply``.

AASIST-L is trained on clean ASVspoof2019 audio; a mic array with on-board DSP shifts its scores, so the cut-off
is measured on the device and kept next to the voice template (owner-only, 0600). Thresholds are per channel
because the channel the model reads is configurable. Demo calibration, not certification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.private_fs import ensure_private_dir, write_private_text

LABEL = "demo-calibrated"


@dataclass
class CmCalibration:
    thresholds: dict[str, float] = field(default_factory=dict)
    n_genuine: int = 0
    label: str = LABEL

    def threshold_for(self, channel: str) -> float | None:
        v = self.thresholds.get(channel)
        return float(v) if isinstance(v, int | float) else None

    def describe(self) -> dict:
        return {"thresholds": {k: round(float(v), 4) for k, v in self.thresholds.items()},
                "n_genuine": self.n_genuine, "label": self.label}

    @staticmethod
    def path(home: Path, driver_id: str) -> Path:
        return Path(home) / "voice" / f"{driver_id}.cm.json"

    def save(self, home: Path, driver_id: str) -> Path:
        p = self.path(home, driver_id)
        ensure_private_dir(p.parent)
        write_private_text(p, json.dumps(self.describe()))
        return p

    @classmethod
    def load(cls, home: Path, driver_id: str) -> CmCalibration:
        try:
            d = json.loads(cls.path(home, driver_id).read_text())
            th = {str(k): float(v) for k, v in d.get("thresholds", {}).items() if 0.0 <= float(v) <= 1.0}
            return cls(th, int(d.get("n_genuine", 0)), str(d.get("label", LABEL)))
        except Exception:
            return cls()
