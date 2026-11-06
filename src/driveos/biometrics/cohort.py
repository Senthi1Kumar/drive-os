"""Impostor cohort for calibration: LibriSpeech clean-validation clips (``hf-internal-testing/librispeech_asr_dummy``,
a few dozen speakers' utterances), embedded with the active speaker model and cached as ``cohort.npz``.

A small, read-speech, English cohort: good enough to place the impostor side of the demo calibration, not to
estimate in-cabin FAR. Needs ``pyarrow`` (the ``biometrics`` extra) only while building."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import numpy as np

from .models import cohort_path
from .sv import SpeakerEmbedder

log = logging.getLogger("driveos.biometrics.cohort")

REPO = "openslr/librispeech_asr"                 # dev-clean: 40 speakers, CC-BY-4.0
FILE = "clean/validation/0000.parquet"
ASSET = "assets/cohort.npz"                        # shipped pre-built (embeddings only)


def load_cohort(models_dir: Path, model: str) -> np.ndarray | None:
    p = cohort_path(models_dir)
    if not p.is_file():
        return None
    z = np.load(p)
    return z[model] if model in z.files else None


def _read_flac(blob: bytes) -> np.ndarray | None:
    try:
        import soundfile as sf

        x, sr = sf.read(io.BytesIO(blob), dtype="float32")
        return x if sr == 16000 else None
    except Exception:
        return None


def build_cohort(embedder: SpeakerEmbedder, models_dir: Path, per_speaker: int = 2, max_speakers: int = 40) -> int:
    """Embeds ``per_speaker`` utterances (3-8 s) for up to ``max_speakers`` distinct LibriSpeech dev-clean
    speakers, reading only the needed row groups of the parquet over HTTP range requests."""
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    seen: dict[int, int] = {}
    embs = []
    with HfFileSystem().open(f"datasets/{REPO}/{FILE}", "rb") as f:
        pf = pq.ParquetFile(f)
        for g in range(pf.num_row_groups):
            if len(seen) >= max_speakers and all(v >= per_speaker for v in seen.values()):
                break
            for row in pf.read_row_group(g, columns=["audio", "speaker_id"]).to_pylist():
                spk = int(row["speaker_id"])
                if seen.get(spk, 0) >= per_speaker or (spk not in seen and len(seen) >= max_speakers):
                    continue
                x = _read_flac(row["audio"]["bytes"])
                if x is None or not 3 * 16000 <= len(x) <= 8 * 16000:
                    continue
                embs.append(embedder.embed(x))
                seen[spk] = seen.get(spk, 0) + 1
    if not embs:
        raise RuntimeError("no decodable cohort clips (install soundfile)")
    p = cohort_path(models_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = dict(np.load(p).items()) if p.is_file() else {}
    existing[embedder.name] = np.stack(embs).astype(np.float32)
    np.savez(p, **existing)
    log.info("cohort: %d clips from %d speakers embedded with %s", len(embs), len(seen), embedder.name)
    return len(embs)
