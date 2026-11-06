"""Simulated biometrics: scripted voice and face outcomes that still run the real DriveAuth DecisionEngine ladder.

The scripted result is handed to the same ``ExtVoice`` / ``ExtFace`` adapters the live path uses (never DriveAuth's
``MANUAL_SCORES``), so trust fusion, risk, policy, fraud state and the one-time-code stage behave exactly as live.

outcome      voice evidence                      face evidence
-----------  ----------------------------------  ----------------------------
accept       confident P = 0.93                  confident P = 0.95
impostor     confident P = 0.04 (wrong person)   not confident (no match)
spoof        not confident (gated)               not confident (gated)
unavailable  sensor unavailable                  sensor unavailable
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from ..config import BiometricsConfig
from . import driveauth_adapter as da
from .capture import Capture
from .f455 import F455_MATCH_SCORE
from .liveness import generate_challenge, spoken_digits
from .verifier import Evidence, VoiceFirstVerifier

log = logging.getLogger("driveos.biometrics.sim")

OUTCOMES = ("accept", "impostor", "spoof", "unavailable")
MODALITIES = ("voice", "face")
SIM_VOICE_ACCEPT_P = 0.93
SIM_VOICE_IMPOSTOR_P = 0.04
STEP_S = 0.9


def voice_evidence(outcome: str):
    if outcome == "accept":
        return da.confident_score(SIM_VOICE_ACCEPT_P)
    if outcome == "impostor":
        return da.confident_score(SIM_VOICE_IMPOSTOR_P)
    if outcome == "spoof":
        return da.not_confident()
    return da.unavailable()


def face_evidence(outcome: str):
    if outcome == "accept":
        return da.confident_score(F455_MATCH_SCORE)
    if outcome in ("impostor", "spoof"):
        return da.not_confident()
    return da.unavailable()


def _clip(seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(16000 * seconds)) / 16000
    env = (np.sin(2 * np.pi * 3 * t) > -0.2).astype(np.float32)
    r = np.random.default_rng(1)
    return (0.2 * np.sin(2 * np.pi * 180 * t) * env + 0.002 * r.standard_normal(len(t))).astype(np.float32)


class SimFaceRung:
    name = "sim"
    device_present = True

    def __init__(self, outcome: str = "accept"):
        self.outcome = outcome

    def probe(self):
        return face_evidence(self.outcome)


class SimulatedVoiceFirstVerifier(VoiceFirstVerifier):
    """Same Host protocol and ladder as the live verifier, with scripted evidence in place of mic and camera."""

    name = "voice_id"
    simulated = True

    def __init__(self, cfg: BiometricsConfig, *, step_s: float = STEP_S, seam: da.DriveAuthSeam | None = None):
        self.cfg = cfg
        self.rung = SimFaceRung(cfg.sim_face)
        self.outcomes = {"voice": cfg.sim_voice, "face": cfg.sim_face}
        self.step_s = step_s
        self.host = None
        self._loop = self._cancel = self._task = None
        self.last = None
        self.last_risk = None
        self.seam = seam or da.DriveAuthSeam(cfg.home / "driveauth-store-sim", cfg.driver_id,
                                             face_probe=self._face_probe,
                                             strict_version=cfg.strict_driveauth_version)
        if seam is None:
            self.seam.auth._profile.seed_mature()

    @property
    def enrolled(self) -> bool:
        return True

    def attach_audio(self, source, stt) -> None:
        return None

    def set_outcome(self, modality: str, outcome: str) -> None:
        if modality not in MODALITIES or outcome not in OUTCOMES:
            raise ValueError(f"modality in {MODALITIES}, outcome in {OUTCOMES}")
        self.outcomes[modality] = outcome
        if modality == "face":
            self.rung.outcome = outcome

    def _face_probe(self):
        if self._loop and self.host:
            self._loop.call_soon_threadsafe(self._status, "face", None, "Look at the camera")
        import time
        time.sleep(self.step_s)
        return self.rung.probe()

    async def _run(self):
        assert self.host is not None
        digits = generate_challenge(self.cfg.challenge_digits)
        ev = Evidence(digits=digits)
        self.last = ev
        outcome = self.outcomes["voice"]
        self._status("challenge", digits, "Say these digits: " + " ".join(digits))
        self.host.set_capturing(True)
        await self.host.announce("Please say: " + spoken_digits(digits))
        await asyncio.sleep(self.step_s)
        self.host.set_capturing(False)
        self._status("verifying", None, "Checking your voice")
        await asyncio.sleep(self.step_s * 0.5)
        ev.digits_ok = outcome != "spoof"
        ev.p_genuine = SIM_VOICE_ACCEPT_P if outcome == "accept" else SIM_VOICE_IMPOSTOR_P if outcome == "impostor" else 0.0
        ev.gates_ok = outcome in ("accept", "impostor")
        if outcome == "spoof":
            ev.failures.append("the audio looks synthetic or replayed")
        elif outcome == "unavailable":
            ev.failures.append("the voice check is unavailable")
        self.seam.face.reset()
        import time
        clip = _clip()
        return await self._decide(Capture(clip, clip, True, 2.0), voice_evidence(outcome), ev, time.perf_counter())

    def summary(self, ev: Evidence) -> str:
        return f"simulated voice {ev.p_genuine:.2f}"
