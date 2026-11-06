"""Voice ID enrollment over ``/v1/ui``: drives an ``EnrollmentSession`` from the HMI (see hmi_lvgl/ENROLL_CONTRACT.md).

The voice loop is paused for the whole session so the wake/endpoint logic does not fight the prompted takes; both
read the same microphone ring buffer. The session ends in ``done`` or ``failed`` (the loop resumes), or by
``enroll_cancel`` (state returns to ``null``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..biometrics.capture import capture_utterance
from ..biometrics.cohort import load_cohort
from ..biometrics.enroll import PHRASES, TARGET_UTTERANCES, EnrollmentSession, NotEnoughSpeech
from ..biometrics.f455 import F455Client, FaceUserMap, new_user_id
from ..biometrics.store import TemplateStore
from ..biometrics.sv import build_embedder
from ..voice.audio import FRAME_BYTES, rms_level

if TYPE_CHECKING:
    from ..agent.nova import Nova
    from ..config import Settings

log = logging.getLogger("driveos.server.enroll")

EXTRA_ATTEMPTS = 4          # re-records allowed on top of the target number of good takes
LEVEL_POLL_S = 0.03


class EnrollError(Exception):
    """A request the enrollment service refuses (becomes ``ack ok:false``)."""


class EnrollService:
    def __init__(self, settings: Settings, nova: Nova, pipeline=None, *, on_change: Callable[[], None] = lambda: None,
                 set_level: Callable[[float], None] = lambda v: None,
                 on_enrolled: Callable[[], Any] | None = None, total: int = TARGET_UTTERANCES,
                 embedder_factory=build_embedder, capture=capture_utterance):
        self.settings, self.nova, self.pipeline = settings, nova, pipeline
        self.on_change, self.set_level, self.on_enrolled = on_change, set_level, on_enrolled
        self.total = total
        self.embedder_factory, self.capture = embedder_factory, capture
        self._state: dict[str, Any] | None = None
        self._session: EnrollmentSession | None = None
        self._name = ""
        self._task: asyncio.Task | None = None
        self._paused = False
        self.face = FaceEnrollService(settings, nova, on_change=on_change)

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any] | None:
        return self._state

    @property
    def active(self) -> bool:
        return self._state is not None and self._state["stage"] not in ("done", "failed")

    def _set(self, **kw: Any) -> None:
        assert self._state is not None
        self._state.update(kw)
        self.on_change()

    def _prompt(self, attempt: int) -> str:
        return PHRASES[attempt % len(PHRASES)].format(name=self._name)

    def _pause(self, on: bool) -> None:
        self._paused = on
        p = self.pipeline
        if p is None:
            return
        p.paused = on
        if not on:
            drain = getattr(p.mic, "drain", None)
            if drain:
                drain()

    # ------------------------------------------------------------------ commands
    def start(self, name: str, consent: Any) -> asyncio.Future:
        if consent is not True:
            raise EnrollError("recording consent is required")
        name = (name or "").strip()
        if not name:
            raise EnrollError("missing argument: name")
        if self.active:
            raise EnrollError("enrollment already in progress")
        if self.nova.bio_capture or self.nova.shop.checkout is not None:
            raise EnrollError("busy with a checkout")
        self._name = name
        self._pause(True)
        self._state = {"stage": "analyzing", "take": 1, "total": self.total, "kept": 0, "prompt": self._prompt(0),
                       "quality": [], "error": ""}
        self.on_change()
        self._task = asyncio.ensure_future(self._open(name))
        return self._task

    async def _open(self, name: str) -> None:
        cfg = self.settings.biometrics
        try:
            embedder = await asyncio.to_thread(self.embedder_factory, cfg)
            if embedder is None:
                raise EnrollError("speaker model missing (run: driveos fetch-models)")
            cohort = await asyncio.to_thread(load_cohort, cfg.models_dir, embedder.name)
            self._session = EnrollmentSession(embedder, TemplateStore(cfg.home), driver_id=cfg.driver_id, name=name,
                                              consent=True, cohort=cohort)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._fail(str(e) or type(e).__name__)
            return
        self._set(stage="ready")

    def take(self) -> asyncio.Future:
        st = self._state
        if st is None or self._session is None or st["stage"] != "ready":
            raise EnrollError("not ready to record")
        mic = getattr(self.pipeline, "mic", None)
        if mic is None or getattr(self.pipeline, "available", True) is False:
            raise EnrollError("voice unavailable")
        if self.nova.voice.mode in ("speaking", "processing") or self.nova.bio_capture:
            raise EnrollError("voice unavailable")
        self._set(stage="recording")
        self._task = asyncio.ensure_future(self._take(mic))
        return self._task

    async def _take(self, mic) -> None:
        sess = self._session
        assert sess is not None
        poller = asyncio.ensure_future(self._level(mic))
        try:
            cap = await self.capture(mic, start_timeout_s=10.0, max_s=8.0, end_silence_ms=700)
            poller.cancel()
            self.set_level(0.0)
            self._set(stage="analyzing")
            report = await asyncio.to_thread(sess.add, cap.ch0)
            q = report.quality
            quality = list(self._state["quality"]) + [{
                "duration_s": round(q.voiced_s, 1), "snr_db": round(q.snr_db, 1),
                "clipping_pct": round(q.clip_frac * 100, 1), "outlier": False, "ok": report.accepted}]
            attempts, good = len(quality), len(sess.accepted)
            self._set(quality=quality, kept=good)
            if good >= self.total or attempts >= self.total + EXTRA_ATTEMPTS:
                await self._finish(sess)
            else:
                self._set(stage="ready", take=min(good + 1, self.total), prompt=self._prompt(attempts))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("enrollment take failed")
            self._fail(str(e) or type(e).__name__)
        finally:
            poller.cancel()
            self.set_level(0.0)

    async def _finish(self, sess: EnrollmentSession) -> None:
        try:
            res = await asyncio.to_thread(sess.finalize)
        except NotEnoughSpeech as e:
            self._fail(str(e))
            return
        quality = [dict(q) for q in self._state["quality"]]
        for i in res.dropped_outliers:
            if i < len(quality):
                quality[i]["outlier"] = True
        if self.on_enrolled is not None:
            try:
                r = self.on_enrolled()
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                log.exception("could not switch payments to Voice ID")
        self._set(stage="done", kept=res.kept, quality=quality)
        self._pause(False)

    async def _level(self, mic) -> None:
        seq = mic.seq
        while True:
            for s, a, _ in mic.frames_since(seq):
                seq = s + 1
                if len(a) == FRAME_BYTES:
                    self.set_level(rms_level(a))
            await asyncio.sleep(LEVEL_POLL_S)

    def _fail(self, error: str) -> None:
        if self._state is None:
            return
        self._set(stage="failed", error=error)
        self._pause(False)

    def cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        self._session, self._state = None, None
        self._pause(False)
        self.set_level(0.0)
        self.on_change()


FACE_ERRORS = {"Timeout": "I couldn't see a face. Look at the camera and try again.",
               "DuplicateUserId": "That name is already enrolled on the camera.",
               "DuplicateFaceprints": "This face is already enrolled on the camera.",
               "DatabaseFull": "The camera's user list is full.", "Unavailable": "Face camera not found.",
               "Busy": "The face camera is busy.", "DeviceError": "The face camera reported an error.",
               "Sunglasses": "Take off sunglasses and try again.", "MedicalMask": "Remove the mask and try again.",
               "MultipleFacesDetected": "Only one person in view, please."}


class FaceEnrollService:
    """F455 face enrollment: the camera owns the template; DriveOS keeps only the user id it generated."""

    def __init__(self, settings: Settings, nova: Nova, *, on_change: Callable[[], None] = lambda: None,
                 client: F455Client | None = None):
        self.settings, self.nova, self.on_change = settings, nova, on_change
        self.client = client or F455Client(settings.biometrics.f455_lib)
        self._state: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None

    def state(self) -> dict[str, Any] | None:
        return self._state

    @property
    def active(self) -> bool:
        return self._state is not None and self._state["stage"] == "capturing"

    def _set(self, **kw: Any) -> None:
        if self._state is not None:
            self._state.update(kw)
            self.on_change()

    def start(self, name: str, consent: Any) -> asyncio.Future:
        if consent is not True:
            raise EnrollError("face enrollment consent is required")
        name = (name or "").strip()
        if not name:
            raise EnrollError("missing argument: name")
        if self.active:
            raise EnrollError("enrollment already in progress")
        if self.nova.bio_capture or self.nova.shop.checkout is not None:
            raise EnrollError("busy with a checkout")
        self._state = {"stage": "capturing", "error": ""}
        self.on_change()
        self._task = asyncio.ensure_future(self._enroll(name))
        return self._task

    async def _enroll(self, name: str) -> None:
        cfg = self.settings.biometrics
        users = FaceUserMap(cfg.home)
        try:
            if not await asyncio.to_thread(self.client.device_present):
                self._set(stage="failed", error=FACE_ERRORS["Unavailable"])
                return
            uid = new_user_id(name)
            res = await asyncio.to_thread(self.client.enroll, uid, 30.0)
            if not res.matched:
                self._set(stage="failed", error=FACE_ERRORS.get(res.status, f"Face enrollment failed ({res.status})."))
                return
            old = users.get(cfg.driver_id)
            users.set(cfg.driver_id, uid)
            users.set_name(cfg.driver_id, name)
            if old and old != uid:
                try:
                    await asyncio.to_thread(self.client.remove_user, old)
                except Exception:
                    log.warning("could not remove the previous face template", exc_info=True)
            self._set(stage="done", error="")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("face enrollment failed")
            self._set(stage="failed", error=str(e) or type(e).__name__)

    def cancel(self) -> None:
        self.client.cancel()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        self._state = None
        self.on_change()
