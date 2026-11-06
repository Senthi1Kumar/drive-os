"""Per-utterance quality gate for enrollment and challenge audio."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

RATE = 16000
FRAME = 320   # 20 ms


@dataclass
class Quality:
    ok: bool
    voiced_s: float
    snr_db: float
    clip_frac: float
    reasons: list[str] = field(default_factory=list)


def _vad_mask(x: np.ndarray, aggressiveness: int = 2) -> np.ndarray:
    n = len(x) // FRAME
    try:
        import webrtcvad

        v = webrtcvad.Vad(aggressiveness)
        pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
        return np.array([v.is_speech(pcm[i * FRAME:(i + 1) * FRAME].tobytes(), RATE) for i in range(n)], dtype=bool)
    except Exception:
        e = np.array([np.sqrt(np.mean(x[i * FRAME:(i + 1) * FRAME] ** 2) + 1e-12) for i in range(n)])
        return e > max(3.0 * np.percentile(e, 10), 0.01) if n else np.zeros(0, dtype=bool)


def assess(audio: np.ndarray, *, min_voiced_s: float = 2.0, min_snr_db: float = 10.0, max_clip: float = 0.01) -> Quality:
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    reasons: list[str] = []
    mask = _vad_mask(x)
    voiced_s = float(mask.sum() * FRAME / RATE)
    n = len(mask)
    frames = x[: n * FRAME].reshape(n, FRAME) if n else np.zeros((0, FRAME), dtype=np.float32)
    energy = (frames ** 2).mean(axis=1) if n else np.zeros(0)
    if n and mask.any() and (~mask).any():
        snr = 10 * np.log10((energy[mask].mean() + 1e-12) / (energy[~mask].mean() + 1e-12))
    elif n and mask.any():
        snr = 30.0           # no quiet frames to compare with: not noise-limited
    else:
        snr = 0.0
    clip = float((np.abs(x) >= 0.999).mean()) if len(x) else 0.0
    if voiced_s < min_voiced_s:
        reasons.append(f"speak a little longer ({voiced_s:.1f}s of speech, need {min_voiced_s:.0f}s)")
    if snr < min_snr_db:
        reasons.append(f"too noisy (SNR {snr:.0f} dB)")
    if clip > max_clip:
        reasons.append("too loud, the mic is clipping")
    return Quality(not reasons, voiced_s, float(snr), clip, reasons)
