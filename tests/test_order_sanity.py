"""A number used as a money amount or option must never also become the quantity; absurd quantities are
confirmed, and a policy denial is explained once and never retried into a traceback."""

from __future__ import annotations

import asyncio
import logging

import pytest

from driveos.agent.intents import IntentParser
from driveos.domain.errors import PolicyDenied

p = IntentParser()


async def _wait(cond, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


@pytest.fixture
async def nova(settings):
    from driveos.container import build_services

    s = build_services(settings)
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 120, 0.05
    await s.start()
    yield s.nova
    await s.aclose()


async def _fuel_item_open(n):
    await n.handle_text("I need gas")
    await n.handle_text("the first one")
    await n.open_item("fuel-premium", speak=False)
    assert n.shop.view == "item"


# ---------------------------------------------------------------- parser
@pytest.mark.parametrize("text", ["$80", "80 dollars", "eighty bucks", "80 bucks", "add $80", "$80 of premium",
                                  "fill it up", "eighty", "80"])
async def test_amount_is_never_the_quantity_on_an_open_fuel_item(nova, text):
    await _fuel_item_open(nova)
    ctx = nova._ctx()
    i = p.parse(text, ctx)
    if i.name in ("add_item", "set_item_qty"):
        assert i.slots.get("qty", 1) == 1, (text, i)


async def test_prepay_item_quantity_is_always_one(nova):
    await _fuel_item_open(nova)
    i = p.parse("add 3 premium", nova._ctx())
    assert i.slots.get("qty", 1) == 1


def test_qty_ignores_money_phrases():
    assert IntentParser._qty_only("80 dollars") is None
    assert IntentParser._qty_only("$80") is None
    assert IntentParser._qty_only("ten bucks") is None
    assert IntentParser._qty_only("add two") == 2


async def test_menu_money_amount_before_item_is_not_a_quantity(nova):
    await nova.handle_text("I need gas")
    await nova.handle_text("the first one")
    ctx = nova._ctx()
    i = p.parse("add 80 dollars of premium", ctx)
    assert i.name in ("add_items", "open_item")
    assert all(it["qty"] == 1 for it in i.slots["items"])
    assert i.slots["items"][0]["selections"].get("amount") == ["a80"]


# ---------------------------------------------------------------- cart guard
async def test_absurd_quantity_asks_before_adding(nova):
    await nova.handle_text("I need gas")
    await nova.handle_text("the first one")
    await nova.add_item("fuel-premium", 80, {"amount": ["a80"]}, speak=False)
    assert not nova.shop.cart or not nova.shop.cart.lines
    assert nova.shop.awaiting == "confirm_qty"
    assert "80" in nova.last_spoken
    await nova.handle_text("no")
    assert nova.shop.awaiting == "" and not (nova.shop.cart and nova.shop.cart.lines)


async def test_confirmed_quantity_is_added(nova):
    await nova.handle_text("I need gas")
    await nova.handle_text("the first one")
    await nova.add_item("fuel-premium", 3, {"amount": ["a20"]}, speak=False)
    await nova.handle_text("yes")
    assert nova.shop.cart.lines[0].quantity == 3
    assert nova.shop.awaiting == ""


async def test_sane_quantity_is_added_without_asking(nova):
    await nova.handle_text("find me a coffee")
    await nova.open_place(place_id="local:r3-plur-coffee", speak=False)
    await nova.add_item("caramel-latte" if nova.shop.menu.item("caramel-latte") else nova.shop.menu.items[0].id,
                        2, speak=False)
    assert nova.shop.awaiting == "" and nova.shop.cart.lines[0].quantity == 2


async def test_tool_path_hits_the_same_guard(nova):
    from driveos.agent.harness.tools import AddToCart, ToolEnv, _add_to_cart

    await nova.handle_text("I need gas")
    await nova.handle_text("the first one")
    res = await _add_to_cart(nova, AddToCart(items=[{"item_id": "fuel-premium", "qty": 2}]), ToolEnv(turn_id="t1"))
    assert res["ok"] is False and res.get("needs_confirm")
    assert nova.shop.awaiting == "confirm_qty"


# ---------------------------------------------------------------- denial
async def _overlimit_cart(nova):
    await nova.handle_text("I need gas")
    await nova.handle_text("the first one")
    await nova.add_item("fuel-premium", 1, {"amount": ["a80"]}, speak=False)
    # bypass the guard the way a stale client could: put 80 straight into the line
    nova.shop.cart.lines[0].quantity = 80


async def test_denial_is_one_clear_sentence_with_amount_and_limit(nova):
    await _overlimit_cart(nova)
    await nova.checkout()
    spoken = nova.last_spoken
    assert "$6,400" in spoken and "$150" in spoken and "limit" in spoken
    assert spoken.count(".") <= 1


async def test_screen_checkout_retries_are_idempotent_and_quiet(nova, caplog):
    await _overlimit_cart(nova)
    calls = []
    orig = nova.authority.review

    async def counting(*a, **k):
        calls.append(1)
        return await orig(*a, **k)

    nova.authority.review = counting
    caplog.set_level(logging.INFO)
    for _ in range(6):
        await nova.checkout(source="screen")             # must not raise
    assert len(calls) == 1
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [r for r in caplog.records if r.exc_info]


async def test_denial_clears_when_the_cart_changes(nova):
    await _overlimit_cart(nova)
    await nova.checkout(source="screen")
    nova.shop.cart.lines[0].quantity = 1
    await nova.checkout(source="screen")
    assert nova.shop.checkout is not None


async def test_commands_checkout_does_not_raise_on_denial(nova):
    from driveos.server.commands import Commands
    from driveos.ui.actions import UiActions

    actions = UiActions()
    actions.nova = nova
    cmds = Commands(actions, nova)
    await _overlimit_cart(nova)
    await cmds.run("checkout", {})
    await asyncio.sleep(0.1)


def test_policy_denied_keeps_spoken():
    e = PolicyDenied("over_transaction_max", spoken="x")
    assert e.spoken == "x"


async def test_expected_refusals_log_without_a_traceback(caplog):
    from driveos.ui.actions import UiActions

    actions = UiActions()

    async def deny():
        raise PolicyDenied("over_transaction_max")

    caplog.set_level(logging.INFO)
    actions.spawn(deny())
    await asyncio.sleep(0.05)
    recs = [r for r in caplog.records if r.name == "driveos.ui"]
    assert recs and recs[0].levelno == logging.WARNING and not recs[0].exc_info
