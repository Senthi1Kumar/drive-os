"""Simulated biometrics: scripted outcomes through the real DriveAuth ladder (ExtVoice / ExtFace adapters)."""

from __future__ import annotations

import asyncio

import pytest

from driveos.biometrics.sim import SimulatedVoiceFirstVerifier
from driveos.server.snapshot import build_state
from driveos.viewmodel import build_view
from driveos.wallet.auth import build_face
from tests.test_biometric_checkout import _checkout
from tests.test_biometrics import FakeHost
from tests.test_e2e import _wait


@pytest.fixture
def sim(settings):
    cfg = settings.biometrics
    cfg.mode = "simulated"
    v = SimulatedVoiceFirstVerifier(cfg, step_s=0.0)
    host = FakeHost()
    host.ctx["known"] = True
    v.bind(host)
    return v, host


def test_default_mode_resolves_to_simulated_when_not_enrolled(settings):
    settings.biometrics.mode = "auto"
    f = build_face(settings.auth, settings.biometrics)
    assert isinstance(f, SimulatedVoiceFirstVerifier) and f.simulated and f.enrolled
    settings.biometrics.mode = "live"
    assert not isinstance(build_face(settings.auth, settings.biometrics), SimulatedVoiceFirstVerifier)


async def test_accept_goes_through_driveauth(sim):
    v, host = sim
    r = await v.verify(10)
    assert r.success and r.method == "voice_id"
    d = v.seam.auth._last_result
    assert d.decision.value == "ACCEPT"
    assert d.modality_scores["voice"]["conf"] and host.otp_calls == 0
    assert v.last_risk and set(v.last_risk) == {"score", "rule"}


async def test_impostor_voice_and_face_rejected(sim):
    v, host = sim
    v.set_outcome("voice", "impostor")
    v.set_outcome("face", "impostor")
    r = await v.verify(10)
    assert not r.success
    d = v.seam.auth._last_result
    assert d.modality_scores["voice"]["score"] == pytest.approx(0.04)


async def test_impostor_voice_is_rescued_only_by_a_matching_face(sim):
    v, host = sim
    v.set_outcome("voice", "impostor")
    r = await v.verify(10)                      # the ladder escalated to the (scripted, matching) face
    d = v.seam.auth._last_result
    assert d.modality_scores["face"]["conf"] and r.success


async def test_spoof_voice_not_confident_steps_up_to_otp(sim):
    v, host = sim
    host.ctx["known"] = False
    host.otp_ok = False
    v.set_outcome("voice", "spoof")
    r = await v.verify(10)
    assert not r.success and host.otp_calls == 1
    d = v.seam.auth._last_result
    assert d.modality_scores["voice"]["score"] is None and not d.modality_scores["voice"]["conf"]


async def test_spoof_voice_with_good_otp_still_follows_ladder(sim):
    v, host = sim
    host.ctx["known"] = False
    v.set_outcome("voice", "spoof")
    r = await v.verify(10)
    assert host.otp_calls == 1
    assert r.success == (v.seam.auth._last_result.decision.value == "ACCEPT")


async def test_voice_unavailable_uses_face_then_code(sim):
    v, host = sim
    host.ctx["known"] = False
    v.set_outcome("voice", "unavailable")
    await v.verify(10)
    assert "face" in [s for s, _, _ in host.statuses] or host.otp_calls == 1


async def test_face_stepup_uses_scripted_face(sim):
    v, host = sim
    host.ctx["known"] = False             # new payee: ladder asks for more than voice
    r = await v.verify(10)
    assert r.success or host.otp_calls == 1
    v.set_outcome("face", "spoof")
    assert v.rung.probe().score is None and v.rung.probe().available
    v.set_outcome("face", "unavailable")
    assert not v.rung.probe().available
    v.set_outcome("face", "accept")
    assert v.rung.probe().score == pytest.approx(0.95)


def test_set_outcome_validates(sim):
    v, _ = sim
    with pytest.raises(ValueError):
        v.set_outcome("iris", "accept")
    with pytest.raises(ValueError):
        v.set_outcome("voice", "maybe")


async def test_checkout_state_is_labelled_sim_and_cmd_flips_outcome(settings):
    settings.biometrics.mode = "simulated"
    v = SimulatedVoiceFirstVerifier(settings.biometrics, step_s=0.05)
    s, n, sink = await _checkout(settings, v)
    try:
        def auth():
            return build_state(n, build_view(n), level=0.0, toast=None, update={})["shop"]["checkout"]["auth"]
        assert auth()["sim"] is True
        v.set_outcome("voice", "impostor")
        v.set_outcome("face", "impostor")
        scan = asyncio.ensure_future(n.handle_text("use voice"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.stage == "otp", 10)
        a = auth()
        assert a["stage"] == "otp" and a["sim"] is True and set(a["risk"]) == {"score", "rule"}
        assert "embedding" not in str(a)
        await n.submit_otp(n.phone_note["code"])        # stage-3 code delivered through the phone banner
        await asyncio.wait_for(scan, 10)
        assert await _wait(lambda: n.shop.view == "order", 10)       # DriveAuth accepts on the verified code
        assert auth()["stage"] == "accepted"
    finally:
        await s.aclose()
