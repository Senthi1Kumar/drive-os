"""Few-shot voice enrollment: 3-5 utterances, quality gate, outlier drop, encrypted template."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .calibrate import Calibrator
from .quality import Quality, assess
from .store import TemplateStore, VoiceTemplate
from .sv import SpeakerEmbedder, cosine, l2

log = logging.getLogger("driveos.biometrics.enroll")

MIN_UTTERANCES = 3
TARGET_UTTERANCES = 5


class ConsentRequired(Exception):
    pass


class NotEnoughSpeech(Exception):
    pass


@dataclass
class UtteranceReport:
    index: int
    accepted: bool
    quality: Quality
    embedding: np.ndarray | None = None


@dataclass
class EnrollmentResult:
    template: VoiceTemplate
    kept: int
    dropped_outliers: list[int]
    pairwise_mean: float
    pairwise_min: float
    calibration: Calibrator


def drop_outliers(embs: np.ndarray, sigma: float = 2.0, min_sd: float = 0.02) -> list[int]:
    """Indices whose mean cosine to the others is more than ``sigma`` standard deviations below the mean of the
    other utterances (leave-one-out: with only 4-5 samples a plain z-score cannot exceed 2 even for a wild outlier)."""
    n = len(embs)
    if n < 4:
        return []
    sim = embs @ embs.T
    per = (sim.sum(axis=1) - 1.0) / (n - 1)
    out = []
    for i in range(n):
        rest = np.delete(per, i)
        if per[i] < rest.mean() - sigma * max(float(rest.std()), min_sd):
            out.append(i)
    return out


class EnrollmentSession:
    def __init__(self, embedder: SpeakerEmbedder, store: TemplateStore, *, driver_id: str, name: str, consent: bool,
                 cohort: np.ndarray | None = None):
        if not consent:
            raise ConsentRequired("recording consent is required to enroll a voice")
        self.embedder, self.store = embedder, store
        self.driver_id, self.name = driver_id, name
        self.cohort = cohort
        self.reports: list[UtteranceReport] = []

    def add(self, audio: np.ndarray) -> UtteranceReport:
        q = assess(audio)
        emb = self.embedder.embed(audio) if q.ok else None
        r = UtteranceReport(len(self.reports), q.ok, q, emb)
        self.reports.append(r)
        return r

    @property
    def accepted(self) -> list[UtteranceReport]:
        return [r for r in self.reports if r.accepted]

    def finalize(self) -> EnrollmentResult:
        acc = self.accepted
        if len(acc) < MIN_UTTERANCES:
            raise NotEnoughSpeech(f"need {MIN_UTTERANCES} good utterances, have {len(acc)}")
        embs = np.stack([r.embedding for r in acc]).astype(np.float32)
        drop = drop_outliers(embs)
        keep = [i for i in range(len(acc)) if i not in drop]
        if len(keep) < MIN_UTTERANCES:
            drop, keep = [], list(range(len(acc)))
        kept = embs[keep]
        template = l2(kept.mean(axis=0))
        iu = np.triu_indices(len(kept), 1)
        pair = (kept @ kept.T)[iu]
        genuine = [cosine(template, e) for e in kept] + list(pair)
        impostor = [cosine(template, c) for c in self.cohort] if self.cohort is not None and len(self.cohort) else []
        cal = Calibrator.fit(genuine, impostor)
        stats = {"utterances": len(self.reports), "kept": len(kept), "dropped": [acc[i].index for i in drop],
                 "pairwise_mean": float(pair.mean()) if len(pair) else 1.0,
                 "pairwise_min": float(pair.min()) if len(pair) else 1.0}
        tpl = VoiceTemplate(self.driver_id, self.name, self.embedder.name, template, kept, True, cal.describe(), stats)
        self.store.save(tpl)
        log.info("enrolled %s: %d/%d utterances kept, pairwise cosine %.3f (%s)", self.driver_id, len(kept),
                 len(self.reports), stats["pairwise_mean"], cal.label)
        return EnrollmentResult(tpl, len(kept), stats["dropped"], stats["pairwise_mean"], stats["pairwise_min"], cal)


PHRASES = [
    "Hey Nova, this is {name} and I am enrolling my voice for payments.",
    "My voice is my password. Four seven one nine.",
    "Nova, order my usual coffee from the closest cafe.",
    "Please confirm the total and pay with my card.",
    "Today I am driving to work along the coast road.",
]
