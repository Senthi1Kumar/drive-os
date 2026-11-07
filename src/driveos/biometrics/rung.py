"""Face rung: the second modality DriveAuth asks for when voice alone is not enough."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Protocol

from ..domain.models import AuthTier
from ..wallet.auth import AuthResult
from .driveauth_adapter import confident_score, not_confident, unavailable
from .f455 import F455_MATCH_SCORE, F455Client, F455Rung, FaceUserMap, driver_display_name
from .preview import HUB

log = logging.getLogger("driveos.biometrics.rung")



class FaceRung(Protocol):
    name: str

    def probe(self):
        """Blocking. Returns a driveauth ModalityResult: confident score on a match, not-confident when the
        sensor ran but did not authenticate the driver, unavailable when there is no usable sensor."""
        ...


class NoFaceRung:
    name = "none"

    def probe(self):
        return unavailable()


class AiBoxFaceRung:
    """Calls a companion RealSense ID verification service (``POST /v1/face/authenticate``) over HTTP.
    No SDK is imported here; if the service or the camera is absent the rung is simply unavailable and DriveAuth
    steps up to the one-time code. Fallback for ``face_backend = "aibox"``."""

    name = "aibox"

    def __init__(self, url: str, user_id: str | None, timeout_s: float = 5.0):
        self.url, self.user_id, self.timeout_s = url, user_id, timeout_s

    def probe(self):
        try:
            req = urllib.request.Request(self.url, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310 - local AI Box
                body = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            log.info("F455 face rung unavailable: %s", e)
            return unavailable()
        if not body.get("authenticated"):
            return not_confident()
        if self.user_id and body.get("user_id") != self.user_id:
            log.info("F455 authenticated a different enrolled user than expected")
            return not_confident()
        return confident_score(F455_MATCH_SCORE)


FACE_UNAVAILABLE = "Face ID unavailable — use Voice ID or code"


def rung_available(rung: FaceRung | None) -> bool:
    if rung is None or isinstance(rung, NoFaceRung):
        return False
    return bool(getattr(rung, "device_present", True))


class FaceRungVerifier:
    """The Face ID tile when Voice ID is the primary biometric: the F455 face rung on its own, as a FaceVerifier.
    A match is a strong (T2) approval; a missing sensor is reported as such and never falls back to voice."""

    name = "face-f455"
    human_errors = True

    def __init__(self, rung: FaceRung | None):
        self.rung = rung
        self._task: asyncio.Task | None = None
        self._cancelled = False

    @property
    def available(self) -> bool:
        return rung_available(self.rung)

    def cancel(self) -> None:
        self._cancelled = True
        if self._task and not self._task.done():
            self._task.cancel()

    def _fail(self, detail: str) -> AuthResult:
        return AuthResult(AuthTier.T2_STRONG, self.name, False, detail=detail)

    async def verify(self, timeout: float) -> AuthResult:
        self._cancelled = False
        self._task = asyncio.current_task()
        if not self.available:
            log.info("Face ID requested but no face rung is configured")
            return self._fail(FACE_UNAVAILABLE)
        try:
            res = await asyncio.wait_for(asyncio.to_thread(self.rung.probe), timeout)
        except TimeoutError:
            return self._fail("I couldn't see you clearly")
        except asyncio.CancelledError:
            if self._cancelled:
                return self._fail("Cancelled")
            raise
        if not getattr(res, "available", True):
            log.info("Face ID requested but the face rung is unavailable")
            return self._fail(FACE_UNAVAILABLE)
        if getattr(res, "confident", False):
            return AuthResult(AuthTier.T2_STRONG, self.name, True, detail="face match")
        return self._fail("Face didn't match")


def build_rung(cfg, driver_name: str | None) -> FaceRung:
    backend = cfg.face_backend
    if backend == "none":
        return NoFaceRung()
    if backend == "sim":
        from .sim import SimFaceRung

        return SimFaceRung(cfg.sim_face)
    if backend in ("auto", "f455"):
        client = F455Client(cfg.f455_lib)
        if backend == "f455" or client.device_present():
            users = FaceUserMap(cfg.home)

            def user_id() -> str | None:
                return cfg.face_user_id or users.get(users.selected_id(cfg.driver_id))

            def display_name() -> str | None:
                selected = users.selected_id(cfg.driver_id)
                return driver_display_name(cfg.home, selected, user_id()) or driver_name

            HUB.configure(enabled=cfg.preview, fps=cfg.preview_fps, size=cfg.preview_size, mirror=cfg.preview_mirror)
            return F455Rung(client, user_id, cfg.face_timeout_s, display_name=display_name)
    return AiBoxFaceRung(cfg.face_url, cfg.face_user_id or driver_name, cfg.face_timeout_s)
