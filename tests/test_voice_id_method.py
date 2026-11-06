"""The Voice ID tile runs the voice biometric; the Face tile runs the face rung; plain consent is the fallback."""

from __future__ import annotations

import asyncio

from driveos.biometrics.driveauth_adapter import confident_score, not_confident, unavailable
from driveos.domain.models import AuthTier
from driveos.server.snapshot import build_state
from driveos.viewmodel import build_view
from driveos.wallet.auth import AuthResult
from tests.test_e2e import _Sink, _wait


class FakeRung:
    name = "fake"

    def __init__(self, result=None):
        self.calls = 0
        self.result = result or confident_score(0.95)

    def probe(self):
        self.calls += 1
        return self.result


class BioVerifier:
    """Stands in for VoiceFirstVerifier: challenge shown, then waits to be released."""

    name = "voice_id"
    human_errors = True

    def __init__(self, rung=None, accept=True, cfg=None):
        self.host, self.rung, self.accept = None, rung or FakeRung(), accept
        self.cfg = cfg
        self.seam = None
        if cfg is not None:
            from driveos.biometrics.driveauth_adapter import DriveAuthSeam

            self.seam = DriveAuthSeam(cfg.store_dir, cfg.driver_id, face_probe=self.rung.probe if self.rung else None)
            self.seam.auth._profile.seed_mature()
        self.calls, self.cancelled = 0, False
        self.release = asyncio.Event()

    def bind(self, host):
        self.host = host

    def cancel(self):
        self.cancelled = True
        self.release.set()

    async def verify(self, timeout):
        self.calls += 1
        self.host.set_status("challenge", "4729", "Say these digits: 4 7 2 9")
        await self.release.wait()
        if self.cancelled:
            return AuthResult(AuthTier.T2_STRONG, "voice_id", False, detail="Cancelled")
        return AuthResult(AuthTier.T2_STRONG, "voice_id", self.accept, detail="ok" if self.accept else "no")


async def _to_checkout(settings, verifier, order="can I get two pastrami sandwiches and a cookie"):
    from driveos.container import build_services

    settings.vehicle.simulator_time_scale = 25
    sink = _Sink()
    s = build_services(settings, sink=sink, face=verifier) if verifier else build_services(settings, sink=sink)
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 120, 0.05
    if not verifier:
        s.face.scan_s = 0.05
    await s.start()
    n = s.nova
    await n.wake("")
    await n.handle_text("find me a sandwich place nearby")
    await n.handle_text("the first one")
    await n.handle_text(order)
    await n.handle_text("check out")
    return s, n


def _co(n):
    return build_state(n, build_view(n), level=0.0, toast=None, update={})["shop"]["checkout"]


async def test_voice_tile_runs_biometric_and_places_order(settings):
    v = BioVerifier()
    s, n = await _to_checkout(settings, v)
    try:
        co = _co(n)
        assert co["amount"] > 25 and "voice" in co["methods"]          # biometric voice is T2, no plain cap
        assert co["method_info"]["voice"] == {"mode": "biometric", "subtitle": "Say 4 digits"}
        t = asyncio.ensure_future(n.choose_auth("voice"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
        a = _co(n)["auth"]
        assert a["stage"] == "challenge" and a["challenge"] == "4729" and "4 7 2 9" in a["detail"]
        v.release.set()
        await asyncio.wait_for(t, 5)
        assert v.calls == 1 and n.shop.view == "order" and n.shop.tx is not None
    finally:
        await s.aclose()


async def test_voice_tile_failed_biometric_does_not_place_order(settings):
    v = BioVerifier(accept=False)
    s, n = await _to_checkout(settings, v)
    try:
        v.release.set()
        await asyncio.wait_for(n.choose_auth("voice"), 5)
        assert n.shop.view == "checkout" and n.shop.checkout.stage == "failed"
    finally:
        await s.aclose()


async def test_spoken_use_voice_and_confirm_start_biometric(settings):
    v = BioVerifier()
    s, n = await _to_checkout(settings, v)
    try:
        t = asyncio.ensure_future(n.handle_text("confirm"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
        assert v.calls == 1
        v.release.set()
        await asyncio.wait_for(t, 5)
        assert n.shop.view == "order"
    finally:
        await s.aclose()


async def test_voice_tile_without_enrollment_is_plain_consent(settings):
    s, n = await _to_checkout(settings, None, order="add a turkey sandwich")
    try:
        co = _co(n)
        assert co["amount"] <= 25 and "voice" in co["methods"]
        assert co["method_info"]["voice"] == {"mode": "consent", "subtitle": 'Say "confirm"'}
        await n.choose_auth("voice")
        assert n.shop.view == "order"
    finally:
        await s.aclose()


async def test_voice_over_cap_without_enrollment_refused(settings):
    s, n = await _to_checkout(settings, None)
    try:
        co = _co(n)
        assert "voice" not in co["methods"]
        await n.choose_auth("voice")
        assert n.shop.view == "checkout"
    finally:
        await s.aclose()


async def test_face_tile_uses_face_rung_through_driveauth_not_voice_verifier(settings, monkeypatch):
    v = BioVerifier(cfg=settings.biometrics)
    s, n = await _to_checkout(settings, v)
    try:
        monkeypatch.setattr(n.ledger, "has_completed_with", lambda *a: True)     # known payee: no code needed
        assert _co(n)["method_info"]["face"]["available"] is True
        await asyncio.wait_for(n.choose_auth("face"), 5)
        assert v.rung.calls == 1 and v.calls == 0 and n.shop.view == "order"
        assert v.seam.auth._last_result.decision.value == "ACCEPT"
        assert n.shop.checkout is None or n.shop.checkout.risk is not None
    finally:
        await s.aclose()


async def test_face_rung_no_match_fails_without_voice_fallback(settings, monkeypatch):
    v = BioVerifier(rung=FakeRung(not_confident()), cfg=settings.biometrics)
    s, n = await _to_checkout(settings, v)
    try:
        monkeypatch.setattr(n.ledger, "has_completed_with", lambda *a: True)
        await asyncio.wait_for(n.choose_auth("face"), 5)
        assert v.calls == 0 and n.shop.view == "checkout" and n.shop.checkout.stage == "failed"
    finally:
        await s.aclose()


async def test_face_rung_unavailable_says_so(settings, monkeypatch):
    v = BioVerifier(rung=FakeRung(unavailable()), cfg=settings.biometrics)
    s, n = await _to_checkout(settings, v)
    try:
        monkeypatch.setattr(n.ledger, "has_completed_with", lambda *a: True)
        await asyncio.wait_for(n.choose_auth("face"), 5)
        co = _co(n)
        assert v.calls == 0 and n.shop.view == "checkout"
        assert co["auth"]["detail"] == "Face ID unavailable — use Voice ID or code"
    finally:
        await s.aclose()


async def test_face_tile_new_payee_asks_for_the_code_before_approving(settings):
    v = BioVerifier(cfg=settings.biometrics)
    s, n = await _to_checkout(settings, v)
    try:
        t = asyncio.ensure_future(n.choose_auth("face"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.stage == "otp", 5)
        assert n.shop.view == "checkout"                        # not approved on the face alone
        n.shop.checkout.otp_waiter.set_result(True)
        await asyncio.wait_for(t, 5)
        assert n.shop.view == "order"
    finally:
        await s.aclose()


async def test_face_rung_missing_marks_face_unavailable(settings):
    v = BioVerifier()
    v.rung = None
    s, n = await _to_checkout(settings, v)
    try:
        assert _co(n)["method_info"]["face"]["available"] is False
    finally:
        await s.aclose()


async def test_spoken_cancel_during_voice_capture(settings):
    v = BioVerifier()
    s, n = await _to_checkout(settings, v)
    try:
        t = asyncio.ensure_future(n.handle_text("use voice"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
        await asyncio.wait_for(n.handle_text("cancel"), 3)
        assert n.shop.checkout is None and v.cancelled
        await asyncio.wait_for(t, 3)
    finally:
        await s.aclose()
