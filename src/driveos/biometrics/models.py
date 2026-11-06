"""Model locations and downloads (cached under ``<biometrics.home>/models``)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("driveos.biometrics")

REDIMNET_REPO = "soniqo/ReDimNet2-B6-ONNX-FP32"          # MIT, 49 MB, 192-d, fixed 6 s raw-audio input
REDIMNET_FILE = "ReDimNet2B6.onnx"
WESPEAKER_REPO = "soniqo/WeSpeaker-ResNet34-LM-ONNX"      # CC-BY-4.0, 25 MB, 256-d, kaldi fbank input
WESPEAKER_FILE = "wespeaker-resnet34.onnx"
AASIST_FILE = "aasist-l.onnx"                              # MIT (clovaai/aasist), exported by scripts/export_aasist_l.py


def redimnet_path(models_dir: Path) -> Path:
    return models_dir / "redimnet2-b6" / REDIMNET_FILE


def wespeaker_path(models_dir: Path) -> Path:
    return models_dir / "wespeaker-r34" / WESPEAKER_FILE


def aasist_path(models_dir: Path) -> Path:
    return models_dir / "aasist-l" / AASIST_FILE


def cohort_path(models_dir: Path) -> Path:
    return models_dir / "cohort" / "cohort.npz"


def fetch_all(models_dir: Path) -> list[tuple[str, bool, str]]:
    """Download the speaker models. Returns (name, ok, detail) rows."""
    from huggingface_hub import hf_hub_download

    rows = []
    for name, repo, fname, dest in (("redimnet2-b6", REDIMNET_REPO, REDIMNET_FILE, redimnet_path(models_dir).parent),
                                    ("wespeaker-r34", WESPEAKER_REPO, WESPEAKER_FILE, wespeaker_path(models_dir).parent)):
        try:
            dest.mkdir(parents=True, exist_ok=True)
            p = hf_hub_download(repo, fname, local_dir=str(dest))
            rows.append((name, True, p))
        except Exception as e:
            rows.append((name, False, str(e)))
    return rows
