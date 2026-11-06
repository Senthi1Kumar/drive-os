"""Regressions for issues found in an independent review of the ordering, approval and voice flow."""

from __future__ import annotations

import asyncio

import pytest

from driveos.domain.models import GeoPoint
from driveos.voice.audio import Endpointer


async def _wait(cond, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


class _SlowPayments:
    """Wraps the simulated PSP and counts authorisations."""

    def __init__(self, inner, delay=0.3):
        self.inner, self.delay, self.auths = inner, delay, 0
        self.name = inner.name

    async def authorize(self, *a, **k):
        self.auths += 1
        await asyncio.sleep(self.delay)
        return await self.inner.authorize(*a, **k)

    def __getattr__(self, n):
        return getattr(self.inner, n)


@pytest.fixture
async def nova(settings):
    from driveos.container import build_services
    from driveos.payments.simulated import SimulatedPayments

    pay = _SlowPayments(SimulatedPayments(latency_s=0))
    s = build_services(settings, payments=pay)
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 120, 0.05
    s.face.scan_s = 0.05
    await s.start()
    n = s.nova
    n._svc, n._pay = s, pay
    yield n
    await s.aclose()


async def _small_cart(n):
    await n.handle_text("find me a coffee")
    await n.open_place(place_id="local:r3-plur-coffee", speak=False)
    await n.handle_text("add a caramel latte")


async def test_one_approval_one_payment_even_with_tap_and_voice(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    tap = asyncio.ensure_future(nova.choose_auth("voice"))
    await asyncio.sleep(0.01)
    await nova.handle_text("confirm")
    await tap
    assert nova._pay.auths == 1
    assert len(nova.orch.transactions) == 1 and nova.shop.view == "order"


async def test_cancel_is_ignored_once_payment_is_under_way(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    t = asyncio.ensure_future(nova.choose_auth("voice"))
    await asyncio.sleep(0.05)
    await nova.cancel_checkout(quiet=True)
    await t
    assert nova.shop.view == "order"        # placed, and shown — not silently charged behind a closed sheet
    assert nova.shop.tx is not None


async def test_editing_the_cart_voids_the_open_approval(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    await nova.handle_text("make it three")
    assert nova.shop.checkout is None and nova.shop.cart.item_count == 3
    await nova.handle_text("confirm")
    assert nova._pay.auths == 0              # the old $6.83 quote can't be approved any more
    await nova.handle_text("check out")
    assert nova.shop.checkout.req.quote.total.amount == 2048   # 3 x $6.25 + 9.25% tax


async def test_new_search_drops_the_drive_offer(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    await nova.handle_text("confirm")
    assert nova.shop.awaiting == "drive_offer"
    await nova.handle_text("I need gas")
    await nova.handle_text("yes")
    assert nova.nav.mode == "idle" and nova.shop.view == "menu"


async def test_order_completes_without_navigation(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    await nova.handle_text("confirm")
    await nova.handle_text("no")
    await nova.handle_text("I'm here")
    assert nova.shop.arrived
    assert await _wait(lambda: nova.shop.tx.state == "completed", 15)


async def test_parking_at_the_pickup_counts_as_arrival(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    await nova.handle_text("confirm")
    plur = nova.shop.order_place.location
    nova.vehicle.place_at(GeoPoint(lat=plur.lat, lon=plur.lon))
    assert await _wait(lambda: nova.shop.arrived, 5)


async def test_two_orders_are_both_tracked(nova):
    await _small_cart(nova)
    await nova.handle_text("check out")
    await nova.handle_text("confirm")
    first = nova.shop.tx.tx_id
    await nova.handle_text("I need gas")
    await nova.handle_text("the first one")
    await nova.handle_text("add premium 91")
    await nova.handle_text("check out")
    await nova.handle_text("use face id")
    assert nova.shop.view == "order" and first in nova.active and len(nova.active) == 2
    await nova.order_arrived(first)
    assert await _wait(lambda: nova.orch.transactions[first].state == "completed", 15)


async def test_remove_matches_case_insensitively(nova):
    await _small_cart(nova)
    await nova.handle_text("add a chai latte")
    await nova.handle_text("remove the chai latte")
    assert [ln.name for ln in nova.shop.cart.lines] == ["Caramel Latte"]


async def test_overlapping_speech_keeps_the_mic_closed(nova):
    class SlowSpeaker:
        async def say(self, text):
            await asyncio.sleep(0.4 if len(text) > 20 else 0.05)

        def stop(self):
            pass

    nova.speaker = SlowSpeaker()
    long = asyncio.ensure_future(nova.say("This is a long sentence that takes a while to say."))
    await asyncio.sleep(0.02)
    await asyncio.wait_for(asyncio.ensure_future(nova.say("Ready.")), 2)
    assert nova.voice.mode == "idle"
    await long


async def test_overlapping_speech_mode_during_first_line(nova):
    class SlowSpeaker:
        async def say(self, text):
            await asyncio.sleep(0.3)

        def stop(self):
            pass

    nova.speaker = SlowSpeaker()
    a = asyncio.ensure_future(nova.say("First line that is long enough."))
    await asyncio.sleep(0.01)
    b = asyncio.ensure_future(nova.say("Short."))
    await asyncio.sleep(0.35)          # first finished, second speaking
    assert nova.voice.mode == "speaking"
    await a
    await b
    assert nova.voice.mode == "idle"


def test_endpointer_cap_counts_from_speech_onset():
    from tests.test_voice_loop import SILENCE, _voiced

    ep = Endpointer(2, 450, 3600, 4.0)
    for _ in range(250):
        assert ep.feed(SILENCE) == "wait"
    results = [ep.feed(f) for f in _voiced(30)]
    assert "end" not in results


def test_single_word_after_wake_phrase_is_kept():
    from driveos.voice.wake import split_wake

    assert split_wake("Hey Nova, gas.", "hey nova", []) == (True, "gas")
