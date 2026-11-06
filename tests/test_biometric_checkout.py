"""Nova + a biometric verifier: challenge digits in the WS state, spoken cancel during the scan, OTP step-up."""

from __future__ import annotations

import asyncio

from driveos.domain.models import AuthTier
from driveos.server.snapshot import build_state
from driveos.viewmodel import build_view
from driveos.wallet.auth import AuthResult
from tests.test_e2e import _Sink, _wait


class ScriptedVerifier:
    name = "voice_id"
    human_errors = True

    def __init__(self, with_otp=False):
        self.host = None
        self.with_otp = with_otp
        self.release = asyncio.Event()
        self.cancelled = False
        self.otp_ok = None
        self.calls = 0

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
        if self.with_otp:
            self.host.set_status("otp", None, "code")
            self.otp_ok = await self.host.otp_roundtrip(10)
            if not self.otp_ok:
                return AuthResult(AuthTier.T2_STRONG, "voice_id", False, detail="No code")
        return AuthResult(AuthTier.T2_STRONG, "voice_id", True, detail="ok")


async def _checkout(settings, verifier):
    from driveos.container import build_services

    settings.vehicle.simulator_time_scale = 25
    sink = _Sink()
    s = build_services(settings, sink=sink, face=verifier)
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 120, 0.05
    await s.start()
    s.sink = sink
    n = s.nova
    await n.wake("")
    await n.handle_text("find me a sandwich place nearby")
    await n.handle_text("the first one")
    await n.handle_text("can I get two pastrami sandwiches and a cookie")
    await n.handle_text("check out")
    return s, n, sink


def _auth(n):
    return build_state(n, build_view(n), level=0.0, toast=None, update={})["shop"]["checkout"]["auth"]


async def test_challenge_digits_and_spoken_cancel_during_scan(settings):
    v = ScriptedVerifier()
    s, n, sink = await _checkout(settings, v)
    try:
        t = asyncio.ensure_future(n.handle_text("use voice"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
        a = _auth(n)
        assert a["stage"] == "challenge" and a["challenge"] == "4729" and "4 7 2 9" in a["detail"]
        await asyncio.wait_for(n.handle_text("cancel"), 3)       # the scan must not hold the turn lock
        assert n.shop.checkout is None and v.cancelled
        await asyncio.wait_for(t, 3)
    finally:
        await s.aclose()


async def test_biometric_otp_step_up_uses_phone_note_then_pays(settings):
    v = ScriptedVerifier(with_otp=True)
    s, n, sink = await _checkout(settings, v)
    try:
        t = asyncio.ensure_future(n.handle_text("use voice"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
        v.release.set()
        assert await _wait(lambda: n.phone_note is not None, 5)
        state = build_state(n, build_view(n), level=0.0, toast=None, update={})
        assert state["phone"]["code"] == n.phone_note["code"] and state["shop"]["checkout"]["auth"]["stage"] == "otp"
        await n.submit_otp(n.phone_note["code"])
        await asyncio.wait_for(t, 10)
        assert v.otp_ok is True and n.shop.view == "order" and n.shop.tx is not None
    finally:
        await s.aclose()


async def test_wrong_code_during_step_up_fails_the_verification(settings):
    v = ScriptedVerifier(with_otp=True)
    s, n, sink = await _checkout(settings, v)
    try:
        t = asyncio.ensure_future(n.handle_text("use voice"))
        assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
        v.release.set()
        assert await _wait(lambda: n.phone_note is not None, 5)
        for _ in range(3):
            await n.submit_otp("000000" if n.phone_note["code"] != "000000" else "111111")
        await asyncio.wait_for(t, 10)
        assert v.otp_ok is False and n.shop.view == "checkout" and _auth(n)["stage"] == "rejected"
    finally:
        await s.aclose()
