"""VoiceFirstVerifier: the FaceVerifier-Protocol implementation behind ``auth.face = "driveauth"``.

Flow: random digit challenge (spoken by Nova, shown on screen) -> capture -> [digit ASR | speaker embedding |
anti-spoof] in parallel plus the two-channel check -> calibrated voice score -> DriveAuth ladder (voice, then
the F455 face rung, then a one-time code). DriveOS never approves a payment itself: it returns a successful
``AuthResult`` only when DriveAuth says ACCEPT.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from ..config import BiometricsConfig
from ..core.logging_setup import describe_text
from ..domain.models import AuthTier
from ..wallet.auth import AuthResult
from . import driveauth_adapter as da
from .calibrate import Calibrator
from .capture import Capture, RingSource, capture_utterance
from .cm import CmResult, Countermeasure, apply_mode, cm_input
from .cm_calibration import CmCalibration
from .liveness import (
    DIGIT_PROMPT,
    ChannelCheck,
    challenge_matches,
    channel_check,
    generate_challenge,
    spoken_digits,
)
from .rung import FACE_UNAVAILABLE, FaceRung, rung_available
from .store import TemplateStore, VoiceTemplate
from .sv import SpeakerEmbedder, cosine

log = logging.getLogger("driveos.biometrics.verifier")


class Host(Protocol):
    """What Nova provides to the verifier."""

    async def announce(self, text: str) -> None: ...
    def set_status(self, stage: str, challenge: str | None, detail: str) -> None: ...
    def set_capturing(self, on: bool) -> None: ...
    async def otp_roundtrip(self, timeout_s: float) -> bool: ...
    def payment_context(self) -> dict[str, Any]: ...


class Transcriber(Protocol):
    async def transcribe(self, pcm16: bytes, *, vocabulary: str = "") -> str: ...


@dataclass
class Evidence:
    digits: str = ""
    transcript: str = ""
    digits_ok: bool = False
    channel: ChannelCheck = field(default_factory=lambda: ChannelCheck("n/a"))
    cm: CmResult = field(default_factory=lambda: CmResult("unavailable"))
    cm_channel: str = "n/a"
    cm_threshold: float = 0.5
    cosine: float = 0.0
    p_genuine: float = 0.0
    gates_ok: bool = False
    failures: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


def evaluate_gates(ev: Evidence, *, cm_required: bool) -> list[str]:
    fails = []
    if not ev.digits_ok:
        fails.append("the spoken digits did not match")
    if ev.channel.status == "fail":
        fails.append("the microphone channels look injected")
    if ev.cm.status == "ok" and ev.cm.spoof:
        fails.append("the anti-spoof check was unsure about the audio" if ev.cm.unsure
                     else "the audio looks synthetic or replayed")
    if ev.cm.status not in ("ok", "advisory", "off") and cm_required:
        fails.append("the anti-spoof check is unavailable")
    return fails


class VoiceFirstVerifier:
    name = "voice_id"
    human_errors = True       # AuthResult.detail is already a sentence for the driver
    timeout_detail = "Voice check timed out"

    def __init__(self, cfg: BiometricsConfig, *, embedder: SpeakerEmbedder, cm: Countermeasure, store: TemplateStore,
                 rung: FaceRung, stt: Transcriber | None = None, source: RingSource | None = None,
                 seam: da.DriveAuthSeam | None = None):
        self.cfg = cfg
        self.embedder, self.cm, self.store, self.rung = embedder, cm, store, rung
        self.stt, self.source = stt, source
        self._digit_stt: Transcriber | None = None
        self.host: Host | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self.last: Evidence | None = None
        self.last_risk: dict[str, Any] | None = None
        self.seam = seam or da.DriveAuthSeam(cfg.store_dir, cfg.driver_id, face_probe=self._face_probe,
                                             strict_version=cfg.strict_driveauth_version)
        tpl = store.load(cfg.driver_id)
        self.calibrator = Calibrator.from_dict(tpl.calibration) if tpl else Calibrator()
        self.cm_cal = CmCalibration.load(cfg.home, cfg.driver_id)
        self.cm_threshold = self.cm_cal.threshold_for(cfg.cm_channel)
        if self.cm_threshold is None:
            self.cm_threshold = cfg.cm_spoof_threshold

    # wiring --------------------------------------------------------------------
    def bind(self, host: Host) -> None:
        self.host = host

    def attach_audio(self, source: RingSource | None, stt: Transcriber | None) -> None:
        self.source, self.stt = source, stt
        if stt is not None and self.cfg.challenge_whisper_model:
            threading.Thread(target=self._load_digit_stt, daemon=True, name="digit-stt").start()

    def _load_digit_stt(self) -> None:
        try:
            from ..config import VoiceConfig
            from ..voice.stt import WhisperSTT

            self._digit_stt = WhisperSTT(VoiceConfig(whisper_model=self.cfg.challenge_whisper_model,
                                                     whisper_device="cpu"), beam_size=self.cfg.challenge_beam_size)
        except Exception as e:
            log.warning("challenge ASR model unavailable, using the shared STT: %s", e)

    @property
    def enrolled(self) -> bool:
        return self.store.exists(self.cfg.driver_id)

    def cancel(self) -> None:
        if self._cancel:
            self._cancel.set()
        if self._task and not self._task.done():
            self._task.cancel()

    def _channel(self, cap: Capture) -> ChannelCheck:
        mode = self.cfg.channel_check
        name = str(getattr(self.source, "device_name", "")).lower()
        if mode == "off":
            return ChannelCheck("n/a", "channel check off")
        if mode == "auto" and not any(p.lower() in name for p in self.cfg.channel_check_devices):
            return ChannelCheck("n/a", f"not a mic array ({name or 'unknown device'})")
        return channel_check(cap.ch0, cap.ch1, self.cfg.channel_corr_max)

    # helpers -------------------------------------------------------------------
    def _status(self, stage: str, challenge: str | None, detail: str) -> None:
        if self.host:
            self.host.set_status(stage, challenge, detail)

    def _face_probe(self):
        if self._loop and self.host:
            self._loop.call_soon_threadsafe(self._status, "face", None, "Look at the camera")
        return self.rung.probe()

    def _fail(self, detail: str) -> AuthResult:
        return AuthResult(AuthTier.T2_STRONG, self.name, False, detail=detail)

    # main ----------------------------------------------------------------------
    async def verify(self, timeout: float) -> AuthResult:
        self._loop = asyncio.get_running_loop()
        self._cancel = asyncio.Event()
        self._task = asyncio.current_task()
        try:
            return await asyncio.wait_for(self._run(), timeout)
        except TimeoutError:
            return self._fail(self.timeout_detail)
        except asyncio.CancelledError:
            if self._cancel.is_set():
                return self._fail("Cancelled")
            raise
        finally:
            if self.host:
                self.host.set_capturing(False)

    async def _run(self) -> AuthResult:
        assert self.host is not None, "bind(host) before verify()"
        tpl = self.store.load(self.cfg.driver_id)
        if tpl is None or self.source is None:
            return self._fail("Voice ID isn't set up. Use a code instead.")
        digits = generate_challenge(self.cfg.challenge_digits)
        ev = Evidence(digits=digits)
        self.last = ev
        self._status("challenge", digits, "Say these digits: " + " ".join(digits))
        self.host.set_capturing(True)
        await self.host.announce("Please say: " + spoken_digits(digits))
        cap = await capture_utterance(self.source, start_timeout_s=self.cfg.capture_start_timeout_s,
                                      max_s=self.cfg.capture_max_s)
        self.host.set_capturing(False)
        t_end = time.perf_counter()
        self._status("verifying", None, "Checking your voice")
        voice = await self._analyse(cap, tpl, ev)
        self.seam.face.reset()
        if ev.cm.hard_spoof:
            self._log_decision(ev, "REJECT", "hard_spoof")
            return self._fail("I couldn't verify you: " + ev.failures[0])
        return await self._decide(cap, voice, ev, t_end)

    async def _analyse(self, cap: Capture, tpl: VoiceTemplate, ev: Evidence):
        audio = cap.ch0

        async def asr() -> str:
            stt = self._digit_stt or self.stt
            if not stt or not cap.voiced:
                return ""
            t = time.perf_counter()
            try:
                return await stt.transcribe(cap.pcm16, vocabulary=DIGIT_PROMPT)
            finally:
                ev.timings_ms["asr"] = (time.perf_counter() - t) * 1000

        async def sv() -> np.ndarray | None:
            if not cap.voiced or len(audio) < 8000:
                return None
            t = time.perf_counter()
            try:
                return await asyncio.to_thread(self.embedder.embed, audio)
            finally:
                ev.timings_ms["embed"] = (time.perf_counter() - t) * 1000

        cm_audio, ev.cm_channel = cm_input(cap.ch0, cap.ch1, self.cfg.cm_channel, self.cfg.cm_ch1_gain)
        ev.cm_threshold = self.cm_threshold

        async def cmc() -> CmResult:
            if self.cfg.cm_mode == "off":
                return CmResult("off", detail="anti-spoof off")
            if not cap.voiced:
                return CmResult("unavailable", detail="no speech")
            t = time.perf_counter()
            try:
                return await asyncio.to_thread(self.cm.check, cm_audio)
            finally:
                ev.timings_ms["cm"] = (time.perf_counter() - t) * 1000

        text, emb, cmr = await asyncio.gather(asr(), sv(), cmc())
        ev.transcript = text
        ev.cm = apply_mode(cmr, self.cfg.cm_mode, self.cm_threshold, self.cfg.cm_hard_floor)
        ev.digits_ok = challenge_matches(text, ev.digits)
        ev.channel = self._channel(cap)
        ev.cosine = cosine(tpl.template, emb) if emb is not None else 0.0
        ev.p_genuine = self.calibrator(ev.cosine) if emb is not None else 0.0
        ev.failures = evaluate_gates(ev, cm_required=self.cfg.cm_required)
        if emb is None:
            ev.failures.append("I didn't hear enough speech")
        if ev.failures:
            return da.not_confident()
        ev.gates_ok = True
        return da.confident_score(ev.p_genuine)

    async def _decide(self, cap: Capture, voice, ev: Evidence, t_end: float) -> AuthResult:
        assert self.host is not None
        ctx = self.host.payment_context()
        self.seam.set_context(**da.vehicle_risk_kwargs(ctx.get("vehicle"), ctx.get("merchant_location"),
                                                       self.cfg.merchant_far_km))
        kw = dict(amount=ctx["amount"], beneficiary=ctx["merchant"], beneficiary_known=ctx["known"],
                  transcript=ev.transcript)
        self.seam.begin(voice)
        d = await asyncio.to_thread(self.seam.authenticate, cap.ch0, **kw)
        self._report_risk(d)
        if d.verdict == "STEP_UP_REQUIRED" and d.step_up_method == "otp_mobile":
            hold = getattr(self.host, "hold_face_result", None)
            if hold is not None:
                await hold()              # let the driver see "Face verified" before the code screen replaces it
            self._status("otp", None, "Say the code or type it")
            if not await self.host.otp_roundtrip(self.cfg.otp_timeout_s):
                return self._fail("The code wasn't entered in time")
            self._status("verifying", None, "Checking your code")
            self.seam.begin(voice, otp_verified=True)
            d = await asyncio.to_thread(self.seam.authenticate, cap.ch0, stage3_mode="otp_only", **kw)
            self._report_risk(d)
        ev.timings_ms["decision_after_clip"] = (time.perf_counter() - t_end) * 1000
        self._log_decision(ev, d.verdict, d.step_up_method)
        if d.verdict == "ACCEPT":
            return AuthResult(AuthTier.T2_STRONG, self.name, True, detail=self.summary(ev))
        return self._fail(self._reject_detail(ev))

    def _log_decision(self, ev: Evidence, verdict: str, method: object) -> None:
        """Scores and timings only: never the digits, the transcript or audio (describe_text gives a length)."""
        cm_p = f"{ev.cm.bonafide:.3f}" if ev.cm.bonafide is not None else "n/a"
        log.info("%s %s digits=%s heard=%s cos=%.3f p=%.2f channel=%s cm=%s cm_p=%s cm_ch=%s cm_thr=%.3f gates=%s -> %s %s (%s)",
                 self.name, "ok" if ev.gates_ok else "gated", describe_text(ev.digits), describe_text(ev.transcript),
                 ev.cosine, ev.p_genuine, ev.channel.status, ev.cm.status, cm_p, ev.cm_channel, ev.cm_threshold,
                 ev.failures, verdict, method, {k: round(v) for k, v in ev.timings_ms.items()})

    def _reject_detail(self, ev: Evidence) -> str:
        if ev.failures:
            return "I couldn't verify you: " + ev.failures[0]
        return "I couldn't verify your voice"

    def _report_risk(self, d: da.Decision) -> None:
        """Risk score and policy rule only; never a biometric value."""
        self.last_risk = {"score": round(d.risk, 3), "rule": d.rule}
        report = getattr(self.host, "set_risk", None)
        if report:
            report(self.last_risk)

    def summary(self, ev: Evidence) -> str:
        if ev.cm.status == "ok":
            cm = f"anti-spoof {ev.cm.detail}"
        elif ev.cm.status == "advisory":
            cm = f"anti-spoof advisory ({ev.cm.detail})"
        else:
            cm = "anti-spoof: off"
        return f"voice {ev.p_genuine:.2f} ({self.calibrator.label}), channels {ev.channel.status}, {cm}"


_NO_AUDIO = np.zeros(1600, dtype=np.float32)


class FaceFirstVerifier(VoiceFirstVerifier):
    """The Face ID tile: the same DriveAuth decision as Voice ID, with voice reported unavailable and the F455 rung
    as the face evidence. DriveAuth decides ACCEPT / one-time code / REJECT, applying risk, audit and the
    new-driver bootstrap rule. Needs no voice enrollment. A missing sensor is reported as such and never falls
    back to voice."""

    name = "face-f455"
    timeout_detail = "I couldn't see you clearly"

    def __init__(self, cfg: BiometricsConfig, *, rung: FaceRung, seam: da.DriveAuthSeam | None = None,
                 owner: VoiceFirstVerifier | None = None):
        self.cfg, self.rung, self._owner = cfg, rung, owner
        self.embedder = self.cm = self.store = self.stt = self.source = None
        self._digit_stt = None
        self.host: Host | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self.last: Evidence | None = None
        self.last_risk: dict[str, Any] | None = None
        self.calibrator = Calibrator()
        self.seam = seam or da.DriveAuthSeam(cfg.store_dir, cfg.driver_id, face_probe=self._face_probe,
                                             strict_version=cfg.strict_driveauth_version)

    @classmethod
    def sharing(cls, voice: VoiceFirstVerifier) -> FaceFirstVerifier:
        """Face tile next to an existing Voice ID verifier: same DriveAuth state (profile, fraud ladder, audit)."""
        f = cls(voice.cfg, rung=voice.rung, seam=voice.seam, owner=voice)
        f.host = voice.host
        return f

    @property
    def available(self) -> bool:
        return rung_available(self.rung)

    @property
    def enrolled(self) -> bool:
        return True

    def attach_audio(self, source, stt) -> None:
        return None

    async def verify(self, timeout: float) -> AuthResult:
        if self._owner is not None:
            self._owner._loop = asyncio.get_running_loop()     # the shared seam's probe reports stage through it
        return await super().verify(timeout)

    async def _run(self) -> AuthResult:
        assert self.host is not None, "bind(host) before verify()"
        if not self.available:
            log.info("Face ID requested but no face rung is configured")
            return self._fail(FACE_UNAVAILABLE)
        ev = Evidence()
        self.last = ev
        self._status("face", None, "Look at the camera")
        self.seam.face.reset()
        t_end = time.perf_counter()
        return await self._decide(Capture(_NO_AUDIO, None, False, 0.0), da.unavailable(), ev, t_end)

    def _reject_detail(self, ev: Evidence) -> str:
        seen = self.seam.face.last
        if seen is not None and not getattr(seen, "available", True):
            return FACE_UNAVAILABLE
        return "Face didn't match"

    def summary(self, ev: Evidence) -> str:
        rule = (self.last_risk or {}).get("rule", "")
        return f"face match, DriveAuth {rule}".strip()
