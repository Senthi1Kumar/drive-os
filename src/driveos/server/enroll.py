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
FACE_LINK_TTL_S = 60.0


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
        self.face = FaceEnrollService(settings, nova, on_change=on_change, voice_active=lambda: self.active)

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
        if self.active or self.face.active:
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
    """F455 enrollment and deletion; linked camera entries are never deleted."""

    def __init__(self, settings: Settings, nova: Nova, *, on_change: Callable[[], None] = lambda: None,
                 client: F455Client | None = None, voice_active: Callable[[], bool] | None = None):
        self.settings, self.nova, self.on_change = settings, nova, on_change
        self.client = client or F455Client(settings.biometrics.f455_lib)
        self.voice_active = voice_active or (lambda: False)
        self._state: dict[str, Any] | None = None
        self._task: asyncio.Task | None = None
        self._candidate: str | None = None
        self._link_timer: asyncio.TimerHandle | None = None
        self._profile_id = ""
        self._role = "driver"

    def state(self) -> dict[str, Any] | None:
        return self._state

    def status(self) -> dict[str, Any]:
        cfg = self.settings.biometrics
        users = FaceUserMap(cfg.home)
        selected = users.selected_id(cfg.driver_id)
        uid = users.get(selected) if selected else None
        return {"enrolled": uid is not None, "name": users.get_name(selected) or "" if selected else "",
                "linked": bool(uid and users.is_linked(uid))}

    def profiles_state(self) -> dict[str, Any]:
        users = FaceUserMap(self.settings.biometrics.home)
        return {"items": users.profiles(), "selected_id": users.selected_id(self.settings.biometrics.driver_id),
                "max": 5}

    @property
    def active(self) -> bool:
        return self._state is not None and self._state["stage"] in ("capturing", "link_confirm", "deleting")

    def _set(self, **kw: Any) -> None:
        if self._state is not None:
            self._state.update(kw)
            stage = kw.get("stage")
            if stage in ("done", "failed") and self._state["operation"] == "enroll":
                hub = getattr(self.client, "hub", None)
                if hub is not None:
                    hub.finish(stage == "done", self._state["name"] if stage == "done" else None)
            self.on_change()

    def _clear_link_timer(self) -> None:
        if self._link_timer is not None:
            self._link_timer.cancel()
            self._link_timer = None

    def _check_available(self) -> None:
        if self.active or self.voice_active():
            raise EnrollError("enrollment already in progress")
        if self.nova.bio_capture or self.nova.shop.checkout is not None:
            raise EnrollError("busy with a checkout")

    @staticmethod
    def _name(value: Any) -> str:
        name = str(value or "").strip()
        if not name or len(name) > 32 or len(name.encode()) > 47 or not name.isprintable():
            raise EnrollError("name must be 1-32 printable characters")
        return name

    @staticmethod
    def _valid_role(value: Any) -> str:
        role = str(value or "driver")
        if role not in ("driver", "passenger"):
            raise EnrollError("role must be driver or passenger")
        return role

    def _check_name_unique(self, users: FaceUserMap, profile_id: str, name: str) -> None:
        if any(p["id"] != profile_id and p["name"].casefold() == name.casefold() for p in users.profiles()):
            raise EnrollError("another profile already uses that name")

    def _target(self, users: FaceUserMap, profile_id: Any) -> str:
        if profile_id == "new":
            if len(users.profiles()) >= 5:
                raise EnrollError("at most five face profiles are allowed")
            import uuid
            return f"profile_{uuid.uuid4().hex[:12]}"
        if profile_id is not None and str(profile_id):
            pid = str(profile_id)
            if users.get(pid) is None:
                raise EnrollError("unknown face profile")
            return pid
        return users.selected_id(self.settings.biometrics.driver_id) or self.settings.biometrics.driver_id

    def start(self, name: str, consent: Any, profile_id: Any = None, role: Any = None) -> asyncio.Future:
        if consent is not True:
            raise EnrollError("face enrollment consent is required")
        self._check_available()
        users = FaceUserMap(self.settings.biometrics.home)
        pid = self._target(users, profile_id)
        name = self._name(name)
        self._check_name_unique(users, pid, name)
        self._profile_id = pid
        self._role = self._valid_role(role if role is not None else users.role(pid))
        self._clear_link_timer()
        self._candidate = None
        self._state = {"operation": "enroll", "name": name, "profile_id": pid, "stage": "capturing", "error": ""}
        self.on_change()
        self._task = asyncio.ensure_future(self._enroll(name))
        return self._task

    async def _remove_replaced(self, users: FaceUserMap, old: str | None, new: str) -> None:
        if not old or old == new or users.is_linked(old) or users.profile_for_user(old) is not None:
            return
        try:
            await asyncio.to_thread(self.client.remove_user, old)
        except Exception:
            log.warning("could not remove the previous face template", exc_info=True)

    async def _enroll(self, name: str) -> None:
        cfg = self.settings.biometrics
        users = FaceUserMap(cfg.home)
        pid = self._profile_id
        try:
            if not await asyncio.to_thread(self.client.device_present):
                self._set(stage="failed", error=FACE_ERRORS["Unavailable"])
                return
            uid = new_user_id(name)
            res = await asyncio.to_thread(self.client.enroll, uid, 30.0)
            if res.status == "DuplicateFaceprints":
                found = await asyncio.to_thread(self.client.authenticate, 10.0)
                if not found.matched or not found.user_id:
                    self._set(stage="failed", error="I couldn't identify the existing face. Try again.")
                    return
                owner = users.profile_for_user(found.user_id)
                if owner and owner != pid:
                    self._set(stage="failed", error="This face belongs to another DriveOS profile.")
                    return
                if found.user_id == users.get(pid):
                    users.set_name(pid, name)
                    users.set_role(pid, self._role)
                    users.select(pid)
                    self._set(stage="done", error="")
                    return
                self._candidate = found.user_id
                self._set(stage="link_confirm", error="")
                candidate = self._candidate
                self._link_timer = asyncio.get_running_loop().call_later(
                    FACE_LINK_TTL_S, self._expire_link, candidate)
                return
            if not res.matched:
                self._set(stage="failed", error=FACE_ERRORS.get(res.status, f"Face enrollment failed ({res.status})."))
                return
            old = users.get(pid)
            users.set(pid, uid)
            users.set_name(pid, name)
            users.set_role(pid, self._role)
            users.select(pid)
            await self._remove_replaced(users, old, uid)
            self._set(stage="done", error="")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("face enrollment failed")
            self._set(stage="failed", error=str(e) or type(e).__name__)

    def _expire_link(self, candidate: str) -> None:
        self._link_timer = None
        if self._state is None or self._state["stage"] != "link_confirm" or self._candidate != candidate:
            return
        self._candidate = None
        self._set(stage="failed", error="Link confirmation timed out. Try again.")

    def link(self, confirm: Any) -> asyncio.Future:
        if confirm is not True:
            raise EnrollError("face link confirmation is required")
        if self._state is None or self._state["stage"] != "link_confirm" or self._candidate is None:
            raise EnrollError("no face link to confirm")
        self._clear_link_timer()
        self._task = asyncio.ensure_future(self._link())
        return self._task

    async def _link(self) -> None:
        cfg = self.settings.biometrics
        users = FaceUserMap(cfg.home)
        candidate = self._candidate
        pid = self._profile_id
        try:
            assert candidate is not None and self._state is not None
            owner = users.profile_for_user(candidate)
            if owner and owner != pid:
                self._set(stage="failed", error="This face belongs to another DriveOS profile.")
                return
            old = users.get(pid)
            users.link(pid, candidate)
            users.set_name(pid, self._state["name"])
            users.set_role(pid, self._role)
            users.select(pid)
            await self._remove_replaced(users, old, candidate)
            self._candidate = None
            self._set(stage="done", error="")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("face linking failed")
            self._set(stage="failed", error=str(e) or type(e).__name__)

    def select(self, profile_id: Any) -> None:
        self._check_available()
        users = FaceUserMap(self.settings.biometrics.home)
        try:
            users.select(str(profile_id or ""))
        except ValueError as e:
            raise EnrollError(str(e)) from e
        self.on_change()

    def edit(self, profile_id: Any, name: Any, role: Any) -> None:
        self._check_available()
        users = FaceUserMap(self.settings.biometrics.home)
        pid = str(profile_id or "")
        if users.get(pid) is None:
            raise EnrollError("unknown face profile")
        clean = self._name(name)
        self._check_name_unique(users, pid, clean)
        users.set_name(pid, clean)
        users.set_role(pid, self._valid_role(role) if role is not None else users.role(pid))
        self.on_change()

    def delete(self, confirm: Any, profile_id: Any = None) -> asyncio.Future:
        if confirm is not True:
            raise EnrollError("face deletion confirmation is required")
        self._check_available()
        users = FaceUserMap(self.settings.biometrics.home)
        pid = self._target(users, profile_id)
        self._profile_id = pid
        self._clear_link_timer()
        self._candidate = None
        self._state = {"operation": "delete", "name": users.get_name(pid) or "", "profile_id": pid,
                       "stage": "deleting", "error": ""}
        self.on_change()
        self._task = asyncio.ensure_future(self._delete())
        return self._task

    async def _delete(self) -> None:
        cfg = self.settings.biometrics
        users = FaceUserMap(cfg.home)
        pid = self._profile_id
        try:
            uid = users.get(pid)
            if uid and users.is_linked(uid):
                users.drop(pid)
            elif uid:
                removed = await asyncio.to_thread(self.client.remove_user, uid)
                if not removed:
                    self._set(stage="failed", error="The camera could not delete this face. Try again.")
                    return
                users.drop(pid)
            self._set(stage="done", error="")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("face deletion failed")
            self._set(stage="failed", error=str(e) or type(e).__name__)

    def cancel(self) -> None:
        self.client.cancel()
        self._clear_link_timer()
        self._candidate = None
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        self._state = None
        hub = getattr(self.client, "hub", None)
        if hub is not None:
            hub.reset()
        self.on_change()
