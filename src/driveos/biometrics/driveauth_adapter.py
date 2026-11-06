"""The only module that touches DriveAuth internals.

DriveAuth (https://github.com/couder-04 / Drive_auth_edge) decides ACCEPT / STEP_UP_REQUIRED / REJECT. DriveOS
computes the voice and face evidence itself (speaker embedding, anti-spoof, challenge, channel check), so
``DriveAuth.load()`` is not usable: in production it refuses mock matchers and has no way to take external
scores. Instead this module assembles ``DriveAuth`` through its public constructor with a ``DecisionEngine``
whose matcher bundle holds two thin adapters (``ExtVoice``, ``ExtFace``) that return one-shot scores set by
DriveOS, exactly as ``driveauth/api.py:131-386`` assembles the stock engine.

Pinned to Drive_auth_edge ``76f33c8`` (``PINNED_COMMIT``). Private surface used, and checked by ``check_version``:
``DriveAuth._risk_ctx`` / ``_ctx_lock`` (set as api.py:363-364 does) and ``DecisionEngine`` keyword arguments.
No Trust / Risk / policy logic is copied here.
"""

from __future__ import annotations

import inspect
import logging
import math
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.private_fs import ensure_private_dir, harden_tree

log = logging.getLogger("driveos.biometrics.driveauth")

PINNED_COMMIT = "76f33c8"
CURRENCY = "USD"


class DriveAuthUnavailable(RuntimeError):
    pass


def driveauth_commit() -> str | None:
    try:
        import driveauth

        root = Path(driveauth.__file__).resolve().parent.parent
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=7", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def check_version(strict: bool = False) -> str:
    """Returns a one-line status. Raises DriveAuthUnavailable when the private surface is gone, or when
    ``strict`` and the checkout is not the pinned commit."""
    try:
        from driveauth.api import DriveAuth
        from driveauth.decision_engine import DecisionEngine, MatcherBundle
    except Exception as e:
        raise DriveAuthUnavailable(f"driveauth is not installed: {e}") from e
    need = {"matchers", "quality", "ood", "risk", "trust", "confidence", "policy", "fraud", "risk_ctx",
            "ctx_lock", "escalation", "profile", "driver_id", "ladder_otp"}
    have = set(inspect.signature(DecisionEngine.__init__).parameters)
    if not need <= have:
        raise DriveAuthUnavailable(f"DecisionEngine signature changed (missing {sorted(need - have)})")
    if not {"voice", "face", "finger", "behavioral", "fingerprint_available"} <= set(MatcherBundle.__dataclass_fields__):
        raise DriveAuthUnavailable("MatcherBundle fields changed")
    if "stage3_mode" not in inspect.signature(DriveAuth.authenticate).parameters:
        raise DriveAuthUnavailable("DriveAuth.authenticate no longer takes stage3_mode")
    commit = driveauth_commit()
    msg = f"driveauth {commit or 'unknown'} (pinned {PINNED_COMMIT})"
    if commit and not commit.startswith(PINNED_COMMIT[:7]):
        if strict:
            raise DriveAuthUnavailable(msg + ": version mismatch")
        log.warning("%s: version mismatch, the seam was verified against the pinned commit only", msg)
    return msg


def _mr(score, confident, available=True, quality=1.0):
    from driveauth.types import ModalityResult

    return ModalityResult(score, confident, quality=quality, available=available)


def confident_score(p: float):
    """A calibrated P(genuine) the ladder may act on."""
    return _mr(float(min(1.0, max(0.0, p))), True)


def not_confident():
    """Spoof / liveness / quality failure: the sensor ran, but there is no usable evidence. Never a low score."""
    return _mr(None, False, available=True)


def unavailable():
    return _mr(None, False, available=False)


class ExtVoice:
    """Matcher Protocol (matchers/base.py VoiceMatcher): returns the score DriveOS computed for this clip."""

    def __init__(self):
        self._pending = None
        self._lock = threading.Lock()

    def set_pending(self, result) -> None:
        with self._lock:
            self._pending = result

    def score(self, audio_f32: np.ndarray, sample_rate: int = 16_000):
        with self._lock:
            r, self._pending = self._pending, None
        return r if r is not None else unavailable()


class ExtFace:
    """FaceMatcher Protocol. Deliberately has no ``capture_frame`` so DriveAuth skips its own camera and PAD.
    The probe runs lazily, only when the ladder reaches the face rung; its result is cached for the second
    pass of a two-pass (OTP) authentication."""

    def __init__(self, probe: Callable[[], object] | None):
        self._probe = probe
        self._cache = None

    def reset(self) -> None:
        self._cache = None

    @property
    def last(self):
        """The probe result of the current authentication, or None when the ladder never reached the face rung."""
        return self._cache

    def capture_and_score(self):
        if self._cache is None:
            self._cache = self._probe() if self._probe else unavailable()
        return self._cache


class ExtOtpLane:
    """Stage-3 ladder lane. Offered to the ladder only once DriveOS has verified a code with the driver."""

    def __init__(self):
        self.verified = False

    def can_attempt(self) -> bool:
        return self.verified

    def probe(self, verify_code: str | None = None):
        return _mr(1.0, True) if self.verified else unavailable()


class _NoDelivery:
    """DriveAuth's own payment-OTP provider is unused: DriveOS owns the OTP ladder lane."""

    def deliver(self, mobile_number: str, code: str) -> bool:
        return False


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


def vehicle_risk_kwargs(vehicle: dict | None, merchant: dict | None, far_km: float) -> dict:
    """Facts for ``RiskContext`` from DriveOS state; scoring stays in DriveAuth (risk_model.py).

    ``vehicle``  {lat, lon, speed_kmh, parked}; ``merchant`` {lat, lon}. Every field is set on every call so a
    stale value from the previous payment never lingers. Paying from further than ``far_km`` from the merchant
    is reported as outside the trusted zone (DriveAuth's ``out_of_zone`` feature); otherwise the zone is left at
    DriveAuth's defaults so its learned home still applies. Ignition is on whenever a vehicle source is live.
    """
    kw: dict = {"gps_lat": None, "gps_lon": None, "speed_kmh": 0.0, "ignition_on": True,
                "dist_from_home_km": 0.0, "in_trusted_zone": True, "is_tunnel": False}
    if not vehicle:
        return kw
    kw["gps_lat"], kw["gps_lon"] = float(vehicle["lat"]), float(vehicle["lon"])
    kw["speed_kmh"] = 0.0 if vehicle.get("parked") else max(0.0, float(vehicle.get("speed_kmh") or 0.0))
    if merchant:
        d = haversine_km(kw["gps_lat"], kw["gps_lon"], float(merchant["lat"]), float(merchant["lon"]))
        if d > far_km:
            kw["dist_from_home_km"], kw["in_trusted_zone"] = d, False
    return kw


@dataclass
class Decision:
    verdict: str                 # ACCEPT | STEP_UP_REQUIRED | REJECT
    step_up_method: str | None
    explanations: list[str]
    trust: float
    risk: float
    raw: object = None

    @property
    def rule(self) -> str:
        return str(getattr(self.raw, "policy_rule", "") or "")


class DriveAuthSeam:
    def __init__(self, store_dir: Path, driver_id: str, *, face_probe: Callable[[], object] | None = None,
                 strict_version: bool = False):
        self.version = check_version(strict_version)
        from driveauth.api import DriveAuth
        from driveauth.audit_log import AuditLog
        from driveauth.decision_engine import DecisionEngine, MatcherBundle
        from driveauth.escalation import EscalationPolicy
        from driveauth.fraud_state import FraudStateMachine
        from driveauth.fusion import ConfidenceScorer, TrustFusion
        from driveauth.integrity import verify_store_integrity
        from driveauth.matchers.mock import MockBehavioralMonitor, MockFingerMatcher
        from driveauth.ood_detector import OODDetector
        from driveauth.policy_engine import PolicyEngine
        from driveauth.profile_store import ProfileStore
        from driveauth.quality_gate import QualityGate
        from driveauth.risk_model import RiskModel
        from driveauth.step_up_fallback import StepUpFallback
        from driveauth.step_up_otp import OTPStepUp
        from driveauth.types import RiskContext

        store = Path(store_dir)
        ensure_private_dir(store)
        harden_tree(store)
        verify_store_integrity(store)
        self.voice, self.face, self.otp_lane = ExtVoice(), ExtFace(face_probe), ExtOtpLane()
        matchers = MatcherBundle(voice=self.voice, face=self.face,
                                 finger=MockFingerMatcher(available=False),
                                 behavioral=MockBehavioralMonitor(available=False),
                                 fingerprint_available=False)
        fraud = FraudStateMachine(store / "fraud" / "ladder.json", driver_id)
        profile = ProfileStore(store / "profiles" / f"{driver_id}.json", driver_id)
        risk_ctx, ctx_lock = RiskContext(), threading.Lock()
        engine = DecisionEngine(
            matchers=matchers, quality=QualityGate(), ood=OODDetector.load(str(store), driver_id),
            risk=RiskModel.load(str(store)), trust=TrustFusion.load(str(store), orchestrator=None),
            confidence=ConfidenceScorer(), policy=PolicyEngine(), fraud=fraud, risk_ctx=risk_ctx,
            ctx_lock=ctx_lock, escalation=EscalationPolicy(), profile=profile, driver_id=driver_id,
            ladder_otp=self.otp_lane)
        self.auth = DriveAuth(driver_id=driver_id, store_dir=str(store), engine=engine,
                              otp=OTPStepUp(delivery=_NoDelivery()), fallback=StepUpFallback(str(store), driver_id),
                              audit=AuditLog(store / "audit" / "driveauth_events.jsonl"), fraud=fraud,
                              profile=profile)
        self.auth._risk_ctx = risk_ctx
        self.auth._ctx_lock = ctx_lock
        self.driver_id = driver_id

    def set_context(self, **kw) -> None:
        """Feed vehicle / location facts into DriveAuth's RiskContext (under its context lock)."""
        self.auth.update_vehicle_context(**kw)

    def begin(self, voice_result, *, otp_verified: bool = False) -> None:
        """Set the one-shot voice evidence for the next authenticate() call."""
        self.voice.set_pending(voice_result)
        self.otp_lane.verified = otp_verified

    def authenticate(self, audio_f32: np.ndarray, *, amount: float, beneficiary: str, beneficiary_known: bool,
                     stage3_mode: str | None = None, transcript: str = "") -> Decision:
        r = self.auth.authenticate(audio_np=np.asarray(audio_f32, dtype=np.float32), tier_hint="payment",
                                   amount=float(amount), beneficiary=beneficiary, currency=CURRENCY,
                                   channel="voice", beneficiary_known=beneficiary_known, is_payment=True,
                                   voice_expected=True, stage3_mode=stage3_mode, transcript=transcript,
                                   event="driveos_payment")
        return Decision(r.decision.value, r.step_up_method, list(r.explanations), float(r.trust_score),
                        float(r.risk_score), r)
