"""Authorisation methods offered at checkout: voice, Face ID, one-time code.

Tiering (decided by ``PolicyEngine``):

* **T1 voice** — explicit spoken (or tapped) consent to the exact total read back from the verified quote.
  Allowed only under the voice cap and daily voice limit.
* **T2 strong** — Face ID or a one-time code. Required above the cap; also always on offer.

The verifier back-ends are pluggable. Simulated ones drive the showroom/demo; production ones are:

* Face — ``CommandFaceVerifier`` runs the vehicle's face-recognition service (e.g. a cabin-camera daemon
  or ``howdy``) and trusts only its exit status. Templates stay in that service.
* OTP — ``OtpService`` issues 6-digit codes (stored only as salted HMACs, 5-minute expiry, 3 attempts)
  and delivers them through ``TwilioSms`` or the paired-phone notification channel.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from ..config import AuthConfig
from ..core.ids import new_id, utcnow
from ..domain.models import AuthTier

log = logging.getLogger("driveos.auth")


class AuthMethod(StrEnum):
    VOICE = "voice"
    FACE = "face"
    OTP = "otp"


@dataclass
class AuthResult:
    tier: AuthTier
    method: str
    success: bool
    at: datetime = field(default_factory=utcnow)
    detail: str = ""


def voice_consent(tier: AuthTier = AuthTier.T1_VOICE) -> AuthResult:
    return AuthResult(tier, "voice-confirm", True, detail="explicit spoken confirmation of the read-back total")


# ====================================================================== Face ID
class FaceVerifier(Protocol):
    name: str

    async def verify(self, timeout: float) -> AuthResult: ...
    def cancel(self) -> None: ...


class SimulatedFaceVerifier:
    """Showroom/demo: a scan that matches after `scan_s` unless the UI reports a mismatch."""

    name = "face-sim"
    simulated = True

    def __init__(self, scan_s: float = 1.6):
        self.scan_s = scan_s
        self._pending: asyncio.Future[bool] | None = None

    @property
    def scanning(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def report(self, match: bool) -> None:
        if self._pending and not self._pending.done():
            self._pending.set_result(match)

    def cancel(self) -> None:
        self.report(False)

    async def verify(self, timeout: float) -> AuthResult:
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()
        loop.call_later(self.scan_s, self.report, True)
        try:
            ok = await asyncio.wait_for(self._pending, timeout)
            return AuthResult(AuthTier.T2_STRONG, self.name, ok, detail="match" if ok else "no-match")
        except TimeoutError:
            return AuthResult(AuthTier.T2_STRONG, self.name, False, detail="timeout")
        finally:
            self._pending = None


class CommandFaceVerifier:
    """Production: run the vehicle's face-recognition check; exit status 0 means the enrolled owner."""

    name = "face"

    def __init__(self, command: list[str]):
        self.command = command
        self._proc: asyncio.subprocess.Process | None = None

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    async def verify(self, timeout: float) -> AuthResult:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            rc = await asyncio.wait_for(self._proc.wait(), timeout)
            return AuthResult(AuthTier.T2_STRONG, self.name, rc == 0, detail="match" if rc == 0 else "no-match")
        except TimeoutError:
            self.cancel()
            return AuthResult(AuthTier.T2_STRONG, self.name, False, detail="timeout")
        except FileNotFoundError:
            log.error("face verification command not found: %s", self.command[0])
            return AuthResult(AuthTier.T2_STRONG, self.name, False, detail="camera-unavailable")


class NoFaceVerifier:
    name = "none"

    def cancel(self) -> None:
        pass

    async def verify(self, timeout: float) -> AuthResult:
        return AuthResult(AuthTier.T2_STRONG, self.name, False, detail="camera-unavailable")


# ====================================================================== One-time codes
@dataclass
class OtpChallenge:
    challenge_id: str
    destination: str          # masked, e.g. "•••• 4417"
    channel: str              # "phone" | "sms"
    expires_at: datetime
    length: int = 6
    attempts_left: int = 3

    @property
    def expired(self) -> bool:
        return utcnow() >= self.expires_at


class OtpDelivery(Protocol):
    name: str

    async def send(self, to: str, code: str, purpose: str) -> None: ...


class PhoneNotificationDelivery:
    """Delivers to the paired phone (demo: rendered as a phone notification banner on the head unit)."""

    name = "phone"

    def __init__(self, notify: Callable[[str, str], None] | None = None):
        self.notify = notify

    async def send(self, to: str, code: str, purpose: str) -> None:
        if self.notify:
            self.notify(code, purpose)
        log.info("OTP delivered to the paired phone")


class TwilioSms:
    """SMS through Twilio's Messages API (form POST, basic auth)."""

    name = "sms"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, http):
        self.sid, self.token, self.from_number, self.http = account_sid, auth_token, from_number, http

    async def send(self, to: str, code: str, purpose: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        r = await self.http.request("POST", url, service="twilio", auth=(self.sid, self.token), data={
            "To": to, "From": self.from_number, "Body": f"{code} is your DriveOS code to approve {purpose}. "
                                                       f"It expires in 5 minutes. Don't share it."})
        if r.status_code >= 300:
            from ..domain.errors import ProviderUnavailable
            raise ProviderUnavailable(f"twilio HTTP {r.status_code}")


class OtpService:
    TTL = timedelta(minutes=5)

    def __init__(self, delivery: OtpDelivery, *, length: int = 6, max_attempts: int = 3):
        self.delivery = delivery
        self.length = length
        self.max_attempts = max_attempts
        self._key = secrets.token_bytes(32)
        self._digests: dict[str, bytes] = {}
        self._challenges: dict[str, OtpChallenge] = {}
        self._last_send: dict[str, float] = {}

    def _digest(self, cid: str, code: str) -> bytes:
        return hmac.new(self._key, f"{cid}:{code}".encode(), hashlib.sha256).digest()

    async def issue(self, to: str, purpose: str) -> OtpChallenge:
        last = self._last_send.get(to, 0.0)
        if time.monotonic() - last < 20:  # resend throttle
            await asyncio.sleep(0)
        code = "".join(str(secrets.randbelow(10)) for _ in range(self.length))
        cid = new_id("otp")
        ch = OtpChallenge(cid, f"•••• {to[-4:]}", self.delivery.name, utcnow() + self.TTL, self.length,
                          self.max_attempts)
        self._digests[cid] = self._digest(cid, code)
        self._challenges[cid] = ch
        self._last_send[to] = time.monotonic()
        await self.delivery.send(to, code, purpose)
        return ch

    def verify(self, challenge_id: str, code: str) -> AuthResult:
        ch = self._challenges.get(challenge_id)
        digits = "".join(c for c in code if c.isdigit())
        if not ch:
            return AuthResult(AuthTier.T2_STRONG, "otp", False, detail="unknown-challenge")
        if ch.expired:
            self._drop(challenge_id)
            return AuthResult(AuthTier.T2_STRONG, "otp", False, detail="expired")
        if ch.attempts_left <= 0:
            return AuthResult(AuthTier.T2_STRONG, "otp", False, detail="locked")
        ok = len(digits) == ch.length and hmac.compare_digest(self._digest(challenge_id, digits),
                                                              self._digests[challenge_id])
        if ok:
            self._drop(challenge_id)
            return AuthResult(AuthTier.T2_STRONG, f"otp-{ch.channel}", True, detail="match")
        ch.attempts_left -= 1
        return AuthResult(AuthTier.T2_STRONG, "otp", False,
                          detail="locked" if ch.attempts_left <= 0 else f"mismatch:{ch.attempts_left}")

    def challenge(self, challenge_id: str) -> OtpChallenge | None:
        return self._challenges.get(challenge_id)

    def _drop(self, cid: str) -> None:
        self._challenges.pop(cid, None)
        self._digests.pop(cid, None)


# ====================================================================== factory
def build_face(cfg: AuthConfig, biometrics=None) -> FaceVerifier:
    if cfg.face in ("auto", "driveauth") and biometrics is not None:
        from ..biometrics.factory import build_simulated, build_voice_first

        mode = biometrics.mode
        why = "biometrics.mode = simulated"
        if mode != "simulated":
            try:
                verifier, why = build_voice_first(biometrics, require_enrollment=True)
            except Exception as e:      # missing optional dependency, broken store: never block start-up
                verifier, why = None, str(e)
            if verifier is not None:
                log.info("voice-first payment verification is on (%s)", verifier.seam.version)
                return verifier
            try:
                from ..biometrics.factory import build_face_first

                face_first = build_face_first(biometrics)
            except Exception as e:
                face_first = None
                why = f"{why}; face-first unavailable: {e}"
            if face_first is not None:
                log.info("Face ID goes through DriveAuth without a Voice ID enrollment (%s)", face_first.seam.version)
                return face_first
        if mode in ("auto", "simulated"):
            try:
                sim = build_simulated(biometrics)
                log.info("simulated biometrics through the real DriveAuth ladder (%s)", why)
                return sim
            except Exception as e:
                why = f"{why}; simulated ladder unavailable: {e}"
        log.info("voice-first verification off, using the simulated scan: %s", why)
    if cfg.face == "command":
        return CommandFaceVerifier(cfg.face_command)
    if cfg.face == "none":
        return NoFaceVerifier()
    return SimulatedFaceVerifier(cfg.face_scan_s)


def build_otp(cfg: AuthConfig, http, notify: Callable[[str, str], None] | None) -> OtpService:
    if cfg.otp_channel == "sms" and cfg.twilio_account_sid and cfg.twilio_auth_token and cfg.twilio_from:
        delivery: OtpDelivery = TwilioSms(cfg.twilio_account_sid, cfg.twilio_auth_token.get_secret_value(),
                                          cfg.twilio_from, http)
    else:
        delivery = PhoneNotificationDelivery(notify)
    return OtpService(delivery)


AwaitAuth = Callable[[], Awaitable[AuthResult]]
