"""Builds the voice-first verifier from settings, or explains why it can't."""

from __future__ import annotations

import logging

from ..config import BiometricsConfig
from .cm import build_cm
from .rung import build_rung
from .store import TemplateStore
from .sv import build_embedder
from .verifier import FaceFirstVerifier, VoiceFirstVerifier

log = logging.getLogger("driveos.biometrics")


def build_voice_first(cfg: BiometricsConfig, *, require_enrollment: bool = True) -> tuple[VoiceFirstVerifier | None, str]:
    store = TemplateStore(cfg.home)
    if require_enrollment and not store.exists(cfg.driver_id):
        return None, f"no voice enrollment for {cfg.driver_id} (run: driveos enroll --name <driver>)"
    embedder = build_embedder(cfg)
    if embedder is None:
        return None, "speaker model not downloaded (run: driveos fetch-models)"
    try:
        tpl = store.load(cfg.driver_id)
        name = tpl.name if tpl else None
        v = VoiceFirstVerifier(cfg, embedder=embedder, cm=build_cm(cfg), store=store, rung=build_rung(cfg, name))
    except Exception as e:
        return None, f"DriveAuth unavailable: {e}"
    return v, "ok"


def build_simulated(cfg: BiometricsConfig):
    from .sim import SimulatedVoiceFirstVerifier

    return SimulatedVoiceFirstVerifier(cfg)


def build_face_first(cfg: BiometricsConfig) -> FaceFirstVerifier | None:
    """The Face ID tile when there is no Voice ID enrollment: a plugged-in F455 through DriveAuth. None when no F455
    is present, so the caller keeps its scripted fallback."""
    from .f455 import F455Rung

    rung = build_rung(cfg, None)
    if not (isinstance(rung, F455Rung) and rung.device_present):
        return None
    return FaceFirstVerifier(cfg, rung=rung)
