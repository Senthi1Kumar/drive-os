"""Direct Intel RealSense ID F455 client (SDK ``rsid_py``), the same route the AI Box uses but without the box.

The F455 owns enrollment, encrypted templates, matching and depth anti-spoofing; DriveOS only gets a status code
and the matched user id, never a faceprint. ``F455Rung`` turns that into the DriveAuth evidence:

========================================  ===========================================  ==========
SDK result                                 F455Result                                   DriveAuth
========================================  ===========================================  ==========
``Success`` for the expected user          matched                                      P = 0.95
``Spoof*`` / ``TooManySpoofs``             spoof_detected                               not confident
``Forbidden`` / timeout / other user       not matched                                  not confident
no device, SDK missing, port busy          unavailable                                  unavailable
========================================  ===========================================  ==========

The F455 reports a match decision, not a similarity, so 0.95 is a fixed calibrated value (``F455_MATCH_SCORE``).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.private_fs import ensure_private_dir, write_private_text
from .driveauth_adapter import confident_score, not_confident, unavailable
from .preview import HUB, PreviewHub, SdkPreview

log = logging.getLogger("driveos.biometrics.f455")

F455_MATCH_SCORE = 0.95
PRESENT_TTL_S = 3.0
_USER_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,29}$")
DEFAULT_LIB_DIRS = ("~/repos/RealSenseID/build-preview/lib", "~/repos/RealSenseID/build-device/lib",
                    "~/RealSenseID/build-device/lib")
_ERROR_STATUSES = {"DeviceError", "SerialError", "SecurityError", "VersionMismatch", "CrcError", "Error", "Failure"}


class F455Unavailable(RuntimeError):
    pass


def status_name(status: Any) -> str:
    return str(status).rsplit(".", 1)[-1]


def is_spoof(status: str) -> bool:
    return status.startswith("Spoof") or status == "TooManySpoofs"


def validate_user_id(user_id: str) -> str:
    value = (user_id or "").strip()
    if not _USER_ID.fullmatch(value):
        raise ValueError("user_id must start with a letter and use letters, digits, _ or - (max 30)")
    return value


def new_user_id(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", name or "")[:20] or "driver"
    if not slug[0].isalpha():
        slug = "d" + slug
    return f"{slug.lower()}_{secrets.token_hex(3)}"


@dataclass
class F455Result:
    matched: bool = False
    user_id: str | None = None
    spoof_detected: bool = False
    status: str = "Unavailable"
    available: bool = True
    latency_ms: float = 0.0
    hints: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"matched": self.matched, "user_id": self.user_id, "spoof_detected": self.spoof_detected,
                "status": self.status, "available": self.available, "latency_ms": round(self.latency_ms, 1)}


def _candidate_dirs(lib_dir: str | None) -> list[Path]:
    raw = [lib_dir, os.environ.get("DRIVEOS_F455_LIB"), *DEFAULT_LIB_DIRS]
    return [Path(os.path.expanduser(p)) for p in raw if p]


def load_sdk(lib_dir: str | None = None):
    """Imports ``rsid_py`` from the venv, or from the SDK build directory (``biometrics.f455_lib`` / env
    ``DRIVEOS_F455_LIB`` / ``~/repos/RealSenseID/build-device/lib``). Raises F455Unavailable."""
    try:
        import rsid_py
        return rsid_py
    except ImportError:
        pass
    for d in _candidate_dirs(lib_dir):
        if d.is_dir() and any(d.glob("rsid_py*.so")):
            if str(d) not in sys.path:
                sys.path.append(str(d))
            try:
                import rsid_py
            except ImportError as e:
                raise F455Unavailable(f"rsid_py in {d} does not match this Python: {e}") from e
            return rsid_py
    raise F455Unavailable("RealSense ID SDK (rsid_py) not found; see docs/f455.md")


class F455Client:
    """Thin, serialised wrapper over ``rsid_py.FaceAuthenticator``. Every call is blocking."""

    def __init__(self, lib_dir: str | None = None, sdk: Any = None, hub: PreviewHub | None = None):
        self._lib_dir, self._sdk = lib_dir, sdk
        self.hub = hub if hub is not None else HUB
        self._device: Any = None
        self._lock = threading.Lock()
        self._active = None
        self._done: threading.Event | None = None
        self._done_lock: threading.Lock | None = None
        self._present: tuple[float, bool] | None = None

    def sdk(self):
        if self._sdk is None:
            self._sdk = load_sdk(self._lib_dir)
        return self._sdk

    def device_present(self) -> bool:
        now = time.monotonic()
        if self._present is not None and now - self._present[0] < PRESENT_TTL_S:
            return self._present[1]
        try:
            ok = bool(self.sdk().discover_devices())
        except Exception as e:
            log.debug("F455 not present: %s", e)
            ok = False
        self._present = (now, ok)
        return ok

    def _connect(self):
        sdk = self.sdk()
        devices = sdk.discover_devices()
        if not devices:
            raise F455Unavailable("no F455 found")
        self._device = devices[0]
        return sdk.FaceAuthenticator(devices[0].device_type, devices[0].serial_port)

    def _busy(self):
        raise F455Unavailable("F455 busy")

    def list_users(self) -> list[str]:
        if not self._lock.acquire(timeout=2.0):
            self._busy()
        try:
            with self._connect() as auth:
                return list(auth.query_user_ids())
        finally:
            self._lock.release()

    def remove_user(self, user_id: str) -> bool:
        user_id = validate_user_id(user_id)
        if not self._lock.acquire(timeout=2.0):
            self._busy()
        try:
            with self._connect() as auth:
                auth.remove_user(user_id)
                return user_id not in list(auth.query_user_ids())
        finally:
            self._lock.release()

    def _run(self, op: str, timeout_s: float, user_id: str | None = None) -> F455Result:
        res = F455Result(status="NoResult")
        t0 = time.perf_counter()
        if not self._lock.acquire(blocking=False):
            res.status, res.available = "Busy", False
            return res
        timer = None
        try:
            done = threading.Event()
            res_lock = threading.Lock()
            self._done_lock = res_lock
            self._done = done

            def on_result(status, *rest):
                with res_lock:
                    if done.is_set():
                        return                # too late: the scan already ended (timeout or cancel)
                    res.status = status_name(status)
                    if rest:
                        res.user_id = rest[0] or None
                    done.set()

            def on_hint(status, _score=0.0):
                res.hints = (res.hints + [status_name(status)])[-10:]

            def on_faces(faces, _ts=0):
                self.hub.set_faces(list(faces))

            with self._connect() as auth:
                self._active = auth

                def expire():
                    with res_lock:
                        if done.is_set():
                            return
                        res.status = "Timeout"
                        done.set()
                    try:
                        auth.cancel()
                    except Exception:
                        log.debug("F455 cancel failed", exc_info=True)

                timer = threading.Timer(timeout_s, expire)
                timer.daemon = True
                timer.start()
                if op == "enroll":
                    auth.enroll(user_id=user_id, on_result=on_result, on_hint=on_hint)
                else:
                    self.hub.begin_scan()
                    view = SdkPreview(self.sdk(), self._device, self.hub) if self.hub.enabled else None
                    if view is not None and not view.start():
                        view = None
                    try:
                        auth.authenticate(on_result=on_result, on_hint=on_hint, on_faces=on_faces)
                    finally:
                        if view is not None:
                            view.stop()
        except F455Unavailable as e:
            log.info("F455 unavailable: %s", e)
            res.status, res.available = "Unavailable", False
        except Exception as e:
            if op == "enroll" and "duplicatefaceprint" in str(e).replace(" ", "").lower():
                # the SDK raises when this face is already stored (e.g. enrolled by the AI Box); not a fault
                log.info("F455 enroll: face already enrolled on the camera")
                res.status = "DuplicateFaceprints"
            else:
                log.warning("F455 %s failed: %s", op, e)
                res.status, res.available = "DeviceError", False
        finally:
            if timer:
                timer.cancel()
            self._active = None
            self._done = None
            self._done_lock = None
            self._lock.release()
        if res.status in _ERROR_STATUSES:
            res.available = False
        res.latency_ms = (time.perf_counter() - t0) * 1000
        return res

    def authenticate(self, timeout_s: float) -> F455Result:
        res = self._run("auth", timeout_s)
        res.spoof_detected = is_spoof(res.status)
        res.matched = res.status == "Success" and bool(res.user_id) and not res.spoof_detected
        if not res.matched:
            res.user_id = None
        return res

    def enroll(self, user_id: str, timeout_s: float = 30.0) -> F455Result:
        res = self._run("enroll", timeout_s, validate_user_id(user_id))
        res.spoof_detected = is_spoof(res.status)
        res.matched = res.status == "Success"
        res.user_id = user_id if res.matched else None
        return res

    def cancel(self) -> None:
        done, lock = self._done, self._done_lock
        if done is not None and lock is not None:
            with lock:
                done.set()                      # same decision lock as on_result/expire: cancel waits for an
        auth = self._active                     # in-flight result, and once cancel returns the outcome is final
        if auth is not None:
            try:
                auth.cancel()
            except Exception:
                log.debug("F455 cancel failed", exc_info=True)


class F455Rung:
    """FaceRung backed by the F455 over its serial port. Never raises; absent or busy hardware is unavailable."""

    name = "f455"

    def __init__(self, client: F455Client, expected_user: str | Callable[[], str | None] | None = None,
                 timeout_s: float = 8.0, display_name: Callable[[], str | None] | None = None):
        self.client, self.timeout_s = client, timeout_s
        self._expected = expected_user
        self._display_name = display_name
        self.last: F455Result | None = None

    @property
    def expected_user(self) -> str | None:
        return self._expected() if callable(self._expected) else self._expected

    @property
    def device_present(self) -> bool:
        return self.client.device_present()

    def authenticate(self, timeout: float | None = None) -> F455Result:
        r = self.client.authenticate(self.timeout_s if timeout is None else timeout)
        want = self.expected_user
        if r.matched and not want:
            # the camera can hold other people's faces; without a face mapped to this driver, fail closed
            log.info("F455 matched a user, but no face is enrolled for this driver (driveos enroll --face)")
            r.matched = False
            r.status = "NoDriverFace"
        elif r.matched and r.user_id != want:
            log.info("F455 matched a different enrolled user than the driver")
            r.matched = False
            r.status = "OtherUser"
        self.last = r
        self._report(r)
        return r

    def _report(self, r: F455Result) -> None:
        """Tell the sheet how the scan went (the colour of the brackets and the line under the face)."""
        hub = getattr(self.client, "hub", None)
        if hub is None or r.status == "Busy":
            return
        if not r.available:
            hub.reset()
            return
        verified = r.matched and not r.spoof_detected
        name = None
        if verified and self._display_name is not None:
            try:
                name = self._display_name()
            except Exception:
                log.debug("driver display name unavailable", exc_info=True)
        hub.finish(verified, name)

    def probe(self):
        r = self.authenticate()
        if not r.available:
            return unavailable()
        if r.matched and not r.spoof_detected:
            return confident_score(F455_MATCH_SCORE)
        return not_confident()

    def enroll(self, user_id: str, timeout: float = 30.0) -> F455Result:
        return self.client.enroll(user_id, timeout)

    def list_users(self) -> list[str]:
        return self.client.list_users()

    def remove_user(self, user_id: str) -> bool:
        return self.client.remove_user(user_id)


class FaceUserMap:
    """Which F455 user id belongs to which DriveOS driver (the F455 keeps the template; this is only the id)."""

    def __init__(self, home: Path):
        self.path = Path(home) / "f455_users.json"

    def _read(self) -> dict[str, str]:
        import json
        try:
            return dict(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return {}

    _NAMES = "_names"

    def get(self, driver_id: str) -> str | None:
        if driver_id.startswith("_"):
            return None
        v = self._read().get(driver_id)
        return v if isinstance(v, str) else None

    def get_name(self, driver_id: str) -> str | None:
        """The display name given at enrollment (the F455 user id is not one)."""
        names = self._read().get(self._NAMES)
        v = names.get(driver_id) if isinstance(names, dict) else None
        return v if isinstance(v, str) and v.strip() else None

    def set_name(self, driver_id: str, name: str) -> None:
        import json
        m = self._read()
        names = m.get(self._NAMES)
        m[self._NAMES] = {**(names if isinstance(names, dict) else {}), driver_id: name.strip()}
        ensure_private_dir(self.path.parent)
        write_private_text(self.path, json.dumps(m))

    def set(self, driver_id: str, user_id: str) -> None:
        import json
        m = self._read()
        m[driver_id] = user_id
        ensure_private_dir(self.path.parent)
        write_private_text(self.path, json.dumps(m))

    def drop(self, driver_id: str) -> None:
        import json
        m = self._read()
        names = m.get(self._NAMES)
        dropped_name = isinstance(names, dict) and names.pop(driver_id, None) is not None
        if m.pop(driver_id, None) is not None or dropped_name:
            write_private_text(self.path, json.dumps(m))

    # Linked ids were enrolled by someone else (e.g. the AI Box) and adopted here; DriveOS never deletes them
    # from the camera, it only unlinks them.
    _LINKED = "_linked"

    def _linked(self) -> list[str]:
        import json
        try:
            return list(json.loads(self.path.read_text()).get(self._LINKED, []))
        except (OSError, ValueError, AttributeError):
            return []

    def link(self, driver_id: str, user_id: str) -> None:
        import json
        self.set(driver_id, user_id)
        m: dict = json.loads(self.path.read_text())
        m[self._LINKED] = sorted(set(self._linked()) | {user_id})
        write_private_text(self.path, json.dumps(m))

    def is_linked(self, user_id: str) -> bool:
        return user_id in self._linked()


_ID_STEM = re.compile(r"^(?P<stem>[A-Za-z][A-Za-z0-9_-]*?)_[0-9a-f]{6}$")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def driver_display_name(home: Path, driver_id: str, face_user_id: str | None) -> str | None:
    """What to call the driver on screen: the name given at face enrollment, else the name the face was enrolled
    under, recovered from the id DriveOS generated for it (``alex_1af812`` -> ``alex``), shown with the capitalisation of
    the Voice ID enrollment name when that is the same name; else the Voice ID enrollment name. Never the raw F455
    id; None when unknown."""
    name = FaceUserMap(home).get_name(driver_id)
    if name:
        return name
    voice = None
    try:
        from .store import TemplateStore

        tpl = TemplateStore(Path(home)).load(driver_id)
        voice = tpl.name.strip() if tpl is not None and tpl.name.strip() else None
    except Exception:
        log.debug("voice template name unavailable", exc_info=True)
    m = _ID_STEM.match(face_user_id or "")
    stem = m.group("stem") if m else None
    if stem and voice and _slug(voice) == _slug(stem):
        return voice
    return stem or voice
