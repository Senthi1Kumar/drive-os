"""F455 client against a fake rsid_py: status mapping, DriveAuth evidence, never crashing."""

from __future__ import annotations

import enum
import threading

import pytest

from driveos.biometrics.f455 import F455Client, F455Rung, FaceUserMap, new_user_id, validate_user_id
from driveos.biometrics.rung import FaceRungVerifier, build_rung


class St(enum.Enum):
    Success = 1
    Forbidden = 2
    Spoof_2D = 3
    TooManySpoofs = 4
    DeviceError = 5
    DuplicateUserId = 6


class FakeAuth:
    script = (St.Success, "alice_1")
    block = None
    enroll_raises: str | None = None
    cancelled = threading.Event()

    def __init__(self, *a):
        self.users = ["alice_1", "bob_2"]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query_user_ids(self):
        return list(self.users)

    def remove_user(self, u):
        self.users = [x for x in self.users if x != u]

    def cancel(self):
        FakeAuth.cancelled.set()

    def authenticate(self, on_result=None, on_hint=None, **kw):
        if FakeAuth.block:
            FakeAuth.cancelled.wait(5)
            return
        on_hint(St.Success, 0.5)
        on_result(*self.script_args())

    def enroll(self, user_id, on_result=None, on_hint=None, **kw):
        if FakeAuth.enroll_raises:
            raise RuntimeError(FakeAuth.enroll_raises)
        on_result(FakeAuth.script[0])

    def script_args(self):
        return FakeAuth.script


class FakeSdk:
    devices = [type("D", (), {"device_type": 1, "serial_port": "/dev/ttyACM0"})()]
    FaceAuthenticator = FakeAuth

    def discover_devices(self):
        return list(self.devices)


@pytest.fixture(autouse=True)
def _reset():
    FakeAuth.script, FakeAuth.block, FakeAuth.enroll_raises = (St.Success, "alice_1"), None, None
    FakeAuth.cancelled.clear()
    FakeSdk.devices = [type("D", (), {"device_type": 1, "serial_port": "/dev/ttyACM0"})()]


def rung(expected="alice_1", timeout=1.0):
    return F455Rung(F455Client(sdk=FakeSdk()), expected, timeout)


@pytest.mark.parametrize("expected", [None, ""])
def test_no_enrolled_driver_face_fails_closed(expected):
    # the camera may hold other people's faces (e.g. AI Box users); with no face mapped to this driver,
    # a match against *anyone* must never count as the driver
    r = rung(expected=expected)
    res = r.authenticate()
    assert not res.matched and res.status == "NoDriverFace"
    p = r.probe()
    assert p.available and not p.confident


def test_match_is_confident_095():
    r = rung().probe()
    assert r.available and r.confident and r.score == pytest.approx(0.95)


def test_other_enrolled_user_is_not_the_driver():
    FakeAuth.script = (St.Success, "bob_2")
    res = rung().authenticate()
    assert not res.matched and res.status == "OtherUser"
    p = rung().probe()
    assert p.available and not p.confident and p.score is None


@pytest.mark.parametrize("status", [St.Spoof_2D, St.TooManySpoofs])
def test_spoof_is_not_confident_never_a_low_score(status):
    FakeAuth.script = (status, "")
    rg = rung()
    p = rg.probe()
    assert p.available and not p.confident and p.score is None
    assert rg.last.spoof_detected and not rg.last.matched


def test_forbidden_no_match():
    FakeAuth.script = (St.Forbidden, "")
    p = rung().probe()
    assert p.available and not p.confident


def test_device_error_and_absent_device_are_unavailable():
    FakeAuth.script = (St.DeviceError, "")
    assert not rung().probe().available
    FakeSdk.devices = []
    assert not rung().probe().available
    assert not F455Client(sdk=FakeSdk()).device_present()


def test_sdk_missing_never_crashes(monkeypatch, tmp_path):
    monkeypatch.setenv("DRIVEOS_F455_LIB", str(tmp_path))
    monkeypatch.setattr("driveos.biometrics.f455.DEFAULT_LIB_DIRS", ())
    monkeypatch.setitem(__import__("sys").modules, "rsid_py", None)
    c = F455Client()
    assert not c.device_present()
    assert not F455Rung(c, None).probe().available


def test_timeout_cancels_and_is_not_a_match():
    FakeAuth.block = True
    res = rung(timeout=0.2).authenticate()
    assert res.status == "Timeout" and not res.matched and res.available


def test_busy_device_is_unavailable():
    c = F455Client(sdk=FakeSdk())
    c._lock.acquire()
    try:
        assert not F455Rung(c, None).probe().available
    finally:
        c._lock.release()


def test_list_remove_enroll():
    c = F455Client(sdk=FakeSdk())
    assert c.list_users() == ["alice_1", "bob_2"]
    assert c.remove_user("bob_2") is True
    FakeAuth.script = (St.Success, "")
    assert c.enroll("carol_3").matched
    FakeAuth.script = (St.DuplicateUserId, "")
    r = c.enroll("carol_3")
    assert not r.matched and r.status == "DuplicateUserId"
    with pytest.raises(ValueError):
        validate_user_id("1bad id")
    assert validate_user_id(new_user_id("Asha Rao"))


def test_user_map_roundtrip(tmp_path):
    m = FaceUserMap(tmp_path)
    assert m.get("d1") is None
    m.set("d1", "asha_ab12cd")
    assert FaceUserMap(tmp_path).get("d1") == "asha_ab12cd"
    m.drop("d1")
    assert m.get("d1") is None


async def test_face_tile_uses_f455_rung_and_reports_missing_device():
    v = FaceRungVerifier(rung())
    assert v.available and (await v.verify(5)).success
    FakeSdk.devices = []
    v2 = FaceRungVerifier(F455Rung(F455Client(sdk=FakeSdk()), None))
    assert not v2.available
    assert not (await v2.verify(5)).success


def test_build_rung_backends(tmp_path):
    from driveos.config import BiometricsConfig

    cfg = BiometricsConfig(home=tmp_path, face_backend="f455", f455_lib=str(tmp_path))
    assert isinstance(build_rung(cfg, "x"), F455Rung)
    cfg.face_backend = "aibox"
    assert build_rung(cfg, "x").name == "aibox"
    cfg.face_backend = "sim"
    assert build_rung(cfg, "x").name == "sim"
    cfg.face_backend = "none"
    assert build_rung(cfg, "x").name == "none"


def test_duplicate_faceprint_is_its_own_status_not_a_device_error():
    FakeAuth.enroll_raises = "DuplicateFaceprints"
    res = F455Client(sdk=FakeSdk()).enroll("alex_1", 1.0)
    assert res.status == "DuplicateFaceprints" and res.available and not res.matched


def test_enroll_face_links_existing_faceprint_on_duplicate(settings):
    # the face is already stored on the camera (e.g. enrolled from the AI Box): link it, never delete it
    import argparse

    from driveos.biometrics.enroll_cli import enroll_face

    c = F455Client(sdk=FakeSdk())
    FakeAuth.enroll_raises = "DuplicateFaceprints"
    FakeAuth.script = (St.Success, "alice_1")          # the live face authenticates as the stored entry
    a = argparse.Namespace(name="Alice", driver_id=None, delete=False)
    answers = iter(["yes", "yes"])                     # consent, then confirm the link
    assert enroll_face(a, settings, client=c, ask=lambda _: next(answers)) == 0
    assert FaceUserMap(settings.biometrics.home).get(settings.biometrics.driver_id) == "alice_1"
    assert "alice_1" in c.list_users()                 # the AI Box entry is untouched

    a.delete = True                                    # deleting a *linked* face only unlinks it
    assert enroll_face(a, settings, client=c) == 0
    assert FaceUserMap(settings.biometrics.home).get(settings.biometrics.driver_id) is None
    assert "alice_1" in c.list_users()
    a.delete = False
    answers = iter(["yes", "no"])                      # declining the link stores nothing
    assert enroll_face(a, settings, client=c, ask=lambda _: next(answers)) == 1
    assert FaceUserMap(settings.biometrics.home).get(settings.biometrics.driver_id) is None


def test_enroll_face_cli_consent_and_delete(settings, capsys):
    import argparse

    from driveos.biometrics.enroll_cli import enroll_face

    c = F455Client(sdk=FakeSdk())
    FakeAuth.script = (St.Success, "")
    a = argparse.Namespace(name="Asha", driver_id=None, delete=False)
    assert enroll_face(a, settings, client=c, ask=lambda _: "no") == 2
    assert enroll_face(a, settings, client=c, ask=lambda _: "yes") == 0
    uid = FaceUserMap(settings.biometrics.home).get(settings.biometrics.driver_id)
    assert uid and uid.startswith("asha_")
    a.delete = True
    assert enroll_face(a, settings, client=c) == 0
    assert FaceUserMap(settings.biometrics.home).get(settings.biometrics.driver_id) is None
    FakeSdk.devices = []
    assert enroll_face(a, settings, client=F455Client(sdk=FakeSdk())) == 1


def test_face_profile_metadata_selection_and_voice_rules(tmp_path):
    users = FaceUserMap(tmp_path)
    assert users.voice_approval_allowed("driver1")
    users.set("driver1", "alice_1")
    users.set_name("driver1", "Alice")
    assert users.selected_id() == "driver1" and users.role("driver1") == "driver"
    assert users.profiles() == [{"id": "driver1", "name": "Alice", "role": "driver", "linked": False}]

    users.set("profile2", "bob_2")
    users.set_name("profile2", "Bob")
    users.set_role("profile2", "passenger")
    users.select("profile2")
    assert users.selected_id() == "profile2" and users.role("profile2") == "passenger"
    assert users.profile_for_user("bob_2") == "profile2"
    assert not users.voice_approval_allowed("driver1")
    users.drop("profile2")
    assert users.selected_id() == "driver1" and users.voice_approval_allowed("driver1")


def test_f455_expected_user_follows_selected_profile(tmp_path):
    users = FaceUserMap(tmp_path)
    users.set("driver1", "alice_1")
    users.set("profile2", "bob_2")
    users.select("profile2")
    r = F455Rung(F455Client(sdk=FakeSdk()), lambda: users.get(users.selected_id()), 1.0)
    FakeAuth.script = (St.Success, "bob_2")
    assert r.authenticate().matched
    users.select("driver1")
    assert not r.authenticate().matched and r.last.status == "OtherUser"
