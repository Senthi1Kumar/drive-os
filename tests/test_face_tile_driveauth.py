"""The Face ID tile goes through the same DriveAuth decision as the Voice ID tile: voice is reported unavailable,
the F455 rung supplies the face evidence, and only a DriveAuth ACCEPT approves the payment."""

from __future__ import annotations

import asyncio

import pytest

from driveos.biometrics.f455 import F455Client, F455Rung
from driveos.biometrics.rung import FACE_UNAVAILABLE, NoFaceRung
from driveos.biometrics.verifier import FaceFirstVerifier
from driveos.config import BiometricsConfig
from tests.test_biometrics import FakeHost, FakeRung
from tests.test_f455 import FakeAuth, FakeSdk, St
from tests.test_f455 import rung as f455_rung


def audit_lines(cfg: BiometricsConfig) -> list[str]:
    p = cfg.store_dir / "audit" / "driveauth_events.jsonl"
    return p.read_text().splitlines() if p.exists() else []


@pytest.fixture(autouse=True)
def _reset_f455():
    FakeAuth.script, FakeAuth.block = (St.Success, "alice_1"), None
    FakeSdk.devices = [type("D", (), {"device_type": 1, "serial_port": "/dev/ttyACM0"})()]


@pytest.fixture
def rig(tmp_path):
    cfg = BiometricsConfig(home=tmp_path / "drive-os")
    host = FakeHost()

    def build(kind="match", *, mature=True, rung=None):
        v = FaceFirstVerifier(cfg, rung=rung or FakeRung(kind))
        if mature:
            v.seam.auth._profile.seed_mature()
        v.bind(host)
        return v

    return type("Rig", (), {"build": staticmethod(build), "host": host, "cfg": cfg})


async def test_face_match_is_decided_by_driveauth_and_audited(rig):
    v = rig.build("match")
    before = len(audit_lines(rig.cfg))
    r = await v.verify(10)
    assert r.success and r.method == "face-f455"
    assert len(audit_lines(rig.cfg)) > before                  # DriveAuth wrote an audit entry
    d = v.seam.auth._last_result
    assert d.decision.value == "ACCEPT"
    assert d.modality_scores["voice"]["score"] is None         # voice was reported unavailable, not attempted
    assert d.modality_scores["face"]["conf"]
    assert v.last_risk is not None and "score" in v.last_risk
    assert rig.host.otp_calls == 0
    assert "face" in [s for s, _, _ in rig.host.statuses]


async def test_step_up_to_otp_for_new_payee_approves_only_when_code_ok(rig):
    rig.host.ctx["known"] = False
    v = rig.build("match")
    r = await v.verify(10)
    assert r.success and rig.host.otp_calls == 1
    assert "otp" in [s for s, _, _ in rig.host.statuses]

    rig.host.otp_calls, rig.host.otp_ok = 0, False
    v = rig.build("match")
    r = await v.verify(10)
    assert not r.success and rig.host.otp_calls == 1


async def test_new_driver_with_face_only_needs_the_code(rig):
    v = rig.build("match", mature=False)                       # bootstrap rigor: first transactions need stage 3
    r = await v.verify(10)
    assert r.success and rig.host.otp_calls == 1

    rig.host.otp_calls, rig.host.otp_ok = 0, False
    r = await rig.build("match", mature=False).verify(10)
    assert not r.success and rig.host.otp_calls == 1


async def test_no_voice_enrollment_is_not_required(rig):
    assert not (rig.cfg.home / "voice").exists()
    r = await rig.build("match").verify(10)
    assert r.success


@pytest.mark.parametrize("kind", ["nomatch", "unavailable"])
async def test_other_user_or_spoof_fails_closed(rig, kind):
    v = rig.build(kind)
    r = await v.verify(10)
    assert not r.success and rig.host.otp_calls == 0
    assert v.seam.auth._last_result.decision.value == "REJECT"


async def test_other_user_with_new_payee_still_needs_a_valid_code(rig):
    rig.host.ctx["known"] = False
    rig.host.otp_ok = False
    r = await rig.build("nomatch").verify(10)
    assert not r.success and rig.host.otp_calls == 1


async def test_missing_device_reports_unavailable_and_never_decides(rig):
    for rung in (NoFaceRung(), None):
        before = len(audit_lines(rig.cfg))
        v = rig.build(rung=rung or NoFaceRung())
        r = await v.verify(10)
        assert not r.success and r.detail == FACE_UNAVAILABLE
        assert len(audit_lines(rig.cfg)) == before and rig.host.otp_calls == 0


async def test_f455_without_device_is_unavailable(rig):
    FakeSdk.devices = []
    v = rig.build(rung=F455Rung(F455Client(sdk=FakeSdk()), None))
    assert not v.available
    r = await v.verify(10)
    assert r.detail == FACE_UNAVAILABLE and not r.success


async def test_f455_match_through_driveauth(rig):
    v = rig.build(rung=f455_rung())
    assert v.available and (await v.verify(10)).success
    assert v.seam.auth._last_result.decision.value == "ACCEPT"


@pytest.mark.parametrize("script,expected", [
    ((St.Success, "bob_2"), "alice_1"),            # OtherUser
    ((St.Success, "alice_1"), None),               # NoDriverFace
    ((St.Spoof_2D, ""), "alice_1"),                # spoof
    ((St.Forbidden, ""), "alice_1"),
])
async def test_f455_other_user_no_driver_face_spoof_fail_closed(rig, script, expected):
    FakeAuth.script = script
    v = rig.build(rung=f455_rung(expected=expected))
    r = await v.verify(10)
    assert not r.success and rig.host.otp_calls == 0


async def test_f455_device_error_mid_scan_reports_unavailable(rig):
    FakeAuth.script = (St.DeviceError, "")
    r = await rig.build(rung=f455_rung()).verify(10)
    assert not r.success and r.detail == FACE_UNAVAILABLE


async def test_face_tile_never_approves_without_a_driveauth_accept(rig, monkeypatch):
    v = rig.build("match")
    real = v.seam.authenticate

    def rejecting(*a, **kw):
        d = real(*a, **kw)
        d.verdict = "REJECT"
        return d

    monkeypatch.setattr(v.seam, "authenticate", rejecting)
    assert not (await v.verify(10)).success


async def test_cancel_returns_cancelled(rig):
    class Slow(FakeRung):
        def probe(self):
            import time
            time.sleep(0.5)
            return super().probe()

    v = rig.build(rung=Slow("match"))
    t = asyncio.ensure_future(v.verify(10))
    await asyncio.sleep(0.1)
    v.cancel()
    r = await t
    assert not r.success and r.detail == "Cancelled"


async def test_shares_seam_with_voice_verifier(tmp_path):
    from driveos.biometrics.sim import SimulatedVoiceFirstVerifier

    cfg = BiometricsConfig(home=tmp_path / "drive-os", mode="simulated")
    voice = SimulatedVoiceFirstVerifier(cfg, step_s=0.0)
    voice.bind(FakeHost())
    f = FaceFirstVerifier.sharing(voice)
    assert f.seam is voice.seam and f.rung is voice.rung and f.available
    assert (await f.verify(10)).success
    voice.set_outcome("face", "impostor")
    assert not (await FaceFirstVerifier.sharing(voice).verify(10)).success
