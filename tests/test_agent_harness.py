"""Agent harness: loop, budgets, fallback, degradation, narration suppression, grounding."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
import respx

from driveos.agent.harness.brain import (
    NEUTRAL_CLAIM,
    NOTICE_NO_KEY,
    NOTICE_OFFLINE,
    SAY_SLOW,
    SAY_STAGNANT,
    SAY_STEPS,
)
from driveos.agent.harness.router import decide
from driveos.agent.harness.tools import SPECS, ToolRegistry
from driveos.container import build_services
from driveos.server.snapshot import build_state
from driveos.viewmodel import build_view

from .agent_util import FALLBACK, KEY, PRIMARY, URL, RecSpeaker, bodies, make_settings, script, sse

PLUR = "local:r3-plur-coffee"


@pytest.fixture
async def rig(settings, tmp_path):
    make_settings(settings, tmp_path)
    spk = RecSpeaker()
    s = build_services(settings, speaker=spk)
    s.face.scan_s = 0.05
    await s.start()
    s.nova._spk = spk
    yield s
    await s.aclose()


def nova_of(rig):
    return rig.nova


# ------------------------------------------------------------------ registry and schemas
def test_schema_has_request_checkout_and_no_payment_tools():
    names = {s.name for s in ToolRegistry().visible()}
    assert "request_checkout" in names and len(names) == 18
    for banned in ("checkout", "approve_payment", "approve", "confirm_payment", "pay", "place_order", "choose_auth",
                   "submit_code", "set_payment_limit", "enable_auto_approve"):
        assert banned not in names
    for sch in ToolRegistry().schemas():
        assert sch["function"]["parameters"]["additionalProperties"] is False
    assert all(s.effect != "payment_gated" for s in SPECS)


async def test_payment_gated_tools_have_no_handler_and_are_refused(rig):
    brain = rig.brain
    reg = brain.registry
    for name in ("checkout", "approve_payment", "choose_auth", "submit_code"):
        spec = reg.get(name)
        assert spec is not None and spec.effect == "payment_gated" and spec.handler is None and not spec.llm_visible


@pytest.mark.parametrize("args,err", [
    ('{"category":"coffee","bogus":1}', "invalid_args"),
    ('{"category":"spaceship"}', "invalid_args"),
    ('{"query":"x"}', "invalid_args"),
    ("{not json", "invalid_args"),
    ('["coffee"]', "invalid_args"),
])
async def test_tool_arguments_are_validated(rig, args, err):
    res = await rig.brain.dispatcher.run("find_places", args, _env(), 0)
    assert res.result["error"] == err and not res.ok


def _env():
    from driveos.agent.harness.tools import ToolEnv
    return ToolEnv("t_test")


# ------------------------------------------------------------------ router decisions
async def test_router_decisions(rig):
    n = rig.nova
    ok = dict(llm_available=True, over_cap=False)
    assert decide("something odd", "unknown", n, **ok).route == "agent"
    assert decide("something odd", "unknown", n, **ok, router_enabled=True).route == "router"
    assert decide("find coffee", "find", n, **ok).route == "rules"
    assert decide("yes", None, n, **ok).route == "rules"            # approval phrase never goes to a model
    assert decide("approve the payment", None, n, **ok).reason == "approval_phrase"
    assert decide("odd", "unknown", n, llm_available=False, over_cap=False).route == "offline"
    assert decide("odd", "unknown", n, llm_available=True, over_cap=True).reason == "daily_cap"
    n.shop.awaiting = "replace_cart"
    assert decide("odd", "unknown", n, **ok).reason == "consent_open"
    n.shop.awaiting = "clear_cart"
    assert decide("odd", "unknown", n, **ok).reason == "consent_open"


# ------------------------------------------------------------------ rules fast path
@respx.mock
async def test_rules_fast_path_makes_no_llm_call(rig):
    route = respx.post(URL).mock(side_effect=script(sse("should not be used")))
    n = rig.nova
    await n.handle_text("find me a coffee")
    await n.handle_text("show the cart")
    assert n.shop.places and not route.called


# ------------------------------------------------------------------ agent turn
@respx.mock
async def test_agent_turn_runs_tools_then_speaks_streamed_final_text(rig):
    n = rig.nova
    route = respx.post(URL).mock(side_effect=script(
        sse(tools=[("find_places", '{"category":"coffee","query":"oat milk"}')]),
        sse("Plur Coffee is the closest, about half a mile.")))
    assert await rig.brain.turn("what about somewhere with oat milk on the way")
    assert n.shop.places and n._spk.said[-1].startswith("Plur Coffee")
    first, second = bodies(route)
    assert first["model"] == PRIMARY and first["models"] == [PRIMARY, FALLBACK] and first["stream"] is True
    assert first["reasoning"] == {"effort": "none"}
    assert first["provider"]["allow_fallbacks"] is True and first["provider"]["require_parameters"] is True
    assert {t["function"]["name"] for t in first["tools"]} >= {"find_places", "request_checkout"}
    assert route.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    # step 2 carries the tool result fenced as data
    tool_msg = [m for m in second["messages"] if m["role"] == "tool"][0]
    assert tool_msg["content"].startswith('<data source="tool:find_places">')
    t = rig.brain.last_turn
    assert t and t.steps == 2 and t.tok_cached == 1200 and t.cost > 0


@respx.mock
async def test_narration_with_a_tool_call_is_never_spoken_or_replayed(rig):
    n = rig.nova
    route = respx.post(URL).mock(side_effect=script(
        sse("The driver wants coffee. Let me search for that.", tools=[("find_places", '{"category":"coffee"}')]),
        sse("Plur Coffee, half a mile.")))
    await rig.brain.turn("hmm coffee maybe")
    assert n._spk.said == ["Plur Coffee, half a mile."]
    assert all("Let me search" not in s for s in n._spk.said)
    second = bodies(route)[1]
    asst = [m for m in second["messages"] if m["role"] == "assistant"][-1]
    assert asst["content"] is None and asst["tool_calls"][0]["function"]["name"] == "find_places"
    assert "Let me search" not in json.dumps(second)


@respx.mock
async def test_clarifying_question_is_spoken_with_follow_up(rig):
    respx.post(URL).mock(side_effect=script(sse("Which one do you mean?")))
    await rig.brain.turn("add it to my cart")
    assert rig.nova._spk.said == ["Which one do you mean?"]


# ------------------------------------------------------------------ budgets and termination
@respx.mock
async def test_step_budget_terminates_the_turn(rig):
    n = rig.nova
    calls = [sse(tools=[("show_tab", json.dumps({"tab": t}))]) for t in ("nav", "shop", "home", "agent", "nav")]
    route = respx.post(URL).mock(side_effect=script(*calls))
    await rig.brain.turn("odd request")
    assert route.call_count == rig.brain.cfg.budget.max_steps == 4
    assert n._spk.said == [SAY_STEPS] and rig.brain.last_turn.status == "escalated"


@respx.mock
async def test_repeated_identical_call_is_no_progress(rig):
    n = rig.nova
    same = '{"category":"coffee"}'
    route = respx.post(URL).mock(side_effect=script(*[sse(tools=[("find_places", same)]) for _ in range(4)]))
    await rig.brain.turn("odd")
    assert route.call_count == 2 and n._spk.said == [SAY_STAGNANT]


@respx.mock
async def test_whole_turn_deadline(rig):
    rig.brain.cfg.budget.deadline_s = 0.8
    n = rig.nova

    async def slow_tool(request):
        await asyncio.sleep(0.45)
        return sse(tools=[("show_tab", json.dumps({"tab": "nav" if not n._spk.said else "shop"}))])

    respx.post(URL).mock(side_effect=script(slow_tool, slow_tool, slow_tool, slow_tool))
    t0 = time.monotonic()
    await rig.brain.turn("odd")
    assert time.monotonic() - t0 < 2.0 and n._spk.said == [SAY_SLOW]


@respx.mock
async def test_malformed_tool_json_is_repaired_once_then_gives_up(rig):
    n = rig.nova
    route = respx.post(URL).mock(side_effect=script(
        sse(tools=[("find_places", "{oops")]),
        sse(tools=[("find_places", '{"category":"coffee"}')]),
        sse("Plur Coffee.")))
    await rig.brain.turn("odd")
    assert route.call_count == 3 and n._spk.said == ["Plur Coffee."]
    respx.post(URL).mock(side_effect=script(*[sse(tools=[("find_places", "{oops" + "x" * i)]) for i in range(4)]))
    n._spk.said.clear()
    await rig.brain.turn("odd again")
    assert n._spk.said == [SAY_STAGNANT]


@respx.mock
async def test_empty_response_retried_once(rig):
    n = rig.nova
    route = respx.post(URL).mock(side_effect=script(sse(), sse("Done.")))
    await rig.brain.turn("odd")
    assert route.call_count == 2 and n._spk.said == ["Done."]


# ------------------------------------------------------------------ fallback
@respx.mock
async def test_slow_primary_retries_on_fallback_alone(rig):
    rig.brain.client.call_timeout_s = 0.25
    rig.brain.cfg.budget.call_timeout_s = 0.25
    n = rig.nova

    seen = []

    async def hang(request):
        seen.append(json.loads(request.content))
        await asyncio.sleep(3)
        return sse("late")

    route = respx.post(URL).mock(side_effect=script(hang, sse("Plur is close.", model=FALLBACK)))
    t0 = time.monotonic()
    await rig.brain.turn("odd")
    assert time.monotonic() - t0 < 2.0
    first, second = seen[0], bodies(route)[-1]
    assert first["models"] == [PRIMARY, FALLBACK]
    assert second["model"] == FALLBACK and "models" not in second
    assert n._spk.said == ["Plur is close."] and rig.brain.last_turn.model == FALLBACK


@respx.mock
@pytest.mark.parametrize("status", [400, 404, 429, 503])
async def test_rejected_primary_retries_on_the_fallback_vendor(rig, status):
    route = respx.post(URL).mock(side_effect=script(
        httpx.Response(status, json={"error": {"message": "bad model"}}), sse("Plur is close.", model=FALLBACK)))
    await rig.brain.turn("odd")
    first, second = bodies(route)
    assert first["models"] == [PRIMARY, FALLBACK] and second["model"] == FALLBACK and "models" not in second
    assert rig.nova._spk.said == ["Plur is close."] and rig.brain.last_turn.model == FALLBACK


@respx.mock
async def test_server_side_fallback_is_recorded(rig):
    respx.post(URL).mock(side_effect=script(sse("Okay.", model=FALLBACK)))
    await rig.brain.turn("odd")
    assert rig.brain.last_turn.model == FALLBACK


# ------------------------------------------------------------------ degradation
@respx.mock
async def test_network_down_speaks_one_notice_then_rules_and_breaker(rig):
    n = rig.nova
    route = respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    assert await rig.brain.turn("odd one") is True
    assert n._spk.said == [NOTICE_OFFLINE] and n.voice.offline
    await rig.brain.turn("another odd one")
    await rig.brain.turn("third")
    assert route.call_count == 2                       # breaker opened after two failures: no more attempts
    assert n._spk.said.count(NOTICE_OFFLINE) == 1
    assert (await rig.brain.turn("fourth")) is False   # silent hand-back to rules, notice only once
    await n.handle_text("find me a coffee")            # core flows unaffected
    assert n.shop.places
    st = build_state(n, build_view(n), level=0.0, toast=None, update={})
    assert st["voice"]["agent"]["offline"] is True and st["voice"]["agent"]["route"] == "offline"


async def test_no_key_is_rules_only_with_one_notice(settings, tmp_path):
    make_settings(settings, tmp_path, key=None)
    spk = RecSpeaker()
    s = build_services(settings, speaker=spk)
    await s.start()
    try:
        with respx.mock(assert_all_called=False) as m:
            route = m.post(URL).mock(side_effect=script(sse("no")))
            assert await s.brain.turn("odd") is True
            assert spk.said == [NOTICE_NO_KEY]
            assert await s.brain.turn("odd") is False
            await s.nova.handle_text("find me a coffee")
            assert s.nova.shop.places and not route.called
    finally:
        await s.aclose()


@respx.mock
async def test_http_error_status_is_unavailable(rig):
    respx.post(URL).mock(return_value=httpx.Response(429, json={"error": {"message": "rate"}}))
    assert await rig.brain.turn("odd") is True
    assert rig.nova._spk.said == [NOTICE_OFFLINE]


@respx.mock
async def test_daily_cost_cap_means_rules_only(rig):
    rig.brain.day_cost = rig.brain.cfg.budget.daily_cap_usd + 0.01
    route = respx.post(URL).mock(side_effect=script(sse("x")))
    await rig.brain.turn("odd")
    assert not route.called and "simple commands" in rig.nova._spk.said[0]


@respx.mock
async def test_per_turn_cost_cap(rig):
    respx.post(URL).mock(side_effect=script(sse(tools=[("show_tab", '{"tab":"nav"}')], cost=0.5)))
    await rig.brain.turn("odd")
    assert rig.brain.last_turn.status == "escalated"


# ------------------------------------------------------------------ cancel and barge-in
@respx.mock
async def test_barge_in_aborts_the_stream_and_stays_silent(rig):
    n = rig.nova

    async def very_slow(request):
        await asyncio.sleep(10)
        return sse("never")

    respx.post(URL).mock(side_effect=script(very_slow))
    rig.brain.cfg.budget.call_timeout_s = 20
    rig.brain.client.call_timeout_s = 20
    rig.brain.cfg.budget.deadline_s = 30
    task = asyncio.ensure_future(rig.brain.turn("add a thing"))
    await asyncio.sleep(0.2)
    assert rig.brain.busy
    t0 = time.monotonic()
    n.barge_in()
    assert await asyncio.wait_for(task, 2.0) is True
    assert time.monotonic() - t0 < 1.0 and n._spk.said == [] and n._spk.stopped == 1
    assert rig.brain.last_turn.status == "cancelled"
    # the next turn knows it was interrupted
    route = respx.post(URL).mock(side_effect=script(sse("Sure.")))
    await rig.brain.turn("never mind")
    assert "interrupted" in json.dumps(bodies(route)[0]["messages"])


@respx.mock
async def test_new_utterance_cancels_the_inflight_turn(rig):
    n = rig.nova

    async def slow(request):
        await asyncio.sleep(10)
        return sse("never")

    respx.post(URL).mock(side_effect=script(slow))
    rig.brain.cfg.budget.call_timeout_s = rig.brain.client.call_timeout_s = 20
    rig.brain.cfg.budget.deadline_s = 30
    first = asyncio.ensure_future(n.handle_text("some odd request nobody parses"))
    await asyncio.sleep(0.2)
    await asyncio.wait_for(n.handle_text("find me a coffee"), 5.0)
    await asyncio.wait_for(first, 2.0)
    assert n.shop.places


# ------------------------------------------------------------------ guardrails on output and slots
@respx.mock
async def test_reply_claiming_an_unearned_order_is_replaced(rig):
    respx.post(URL).mock(side_effect=script(sse("Great, your order is placed and paid!")))
    await rig.brain.turn("odd")
    assert rig.nova._spk.said == [NEUTRAL_CLAIM]


async def test_unknown_ids_are_rejected_not_executed(rig):
    n = rig.nova
    d = rig.brain.dispatcher
    r = await d.run("open_place", '{"index": 3}', _env(), 0)
    assert r.result["error"] == "unknown_id"
    await n.handle_text("find me a coffee")
    await n.open_place(place_id=PLUR, speak=False)
    r = await d.run("add_to_cart", '{"items":[{"item_id":"nope","qty":1}]}', _env(), 1)
    assert r.result["error"] == "unknown_id" and not n.shop.cart
    r = await d.run("set_cart_qty", '{"line_id":"zz","qty":2}', _env(), 2)
    assert r.result["error"] == "unknown_id"
    big = json.dumps({"items": [{"item_id": n.shop.menu.items[0].id, "qty": 10}]})
    env = _env()
    for i in range(3):
        last = await d.run("add_to_cart", big, env, 10 + i)
    assert last.result["error"] == "quantity_cap"


async def test_add_to_cart_is_idempotent_on_request_id(rig):
    n = rig.nova
    await n.handle_text("find me a coffee")
    await n.open_place(place_id=PLUR, speak=False)
    item = n.shop.menu.items[0].id
    prior = {"ok": True, "cart": [], "subtotal": "$0.00"}
    from driveos.agent.harness.tools import ToolEnv
    env = ToolEnv("t_dup", lookup_request=lambda rid: prior if rid == "t_dup:0" else None)
    r = await rig.brain.dispatcher.run("add_to_cart", json.dumps({"items": [{"item_id": item}]}), env, 0)
    assert r.result == prior and not n.shop.cart       # replay of a recorded request does not add twice


@respx.mock
async def test_clear_cart_asks_the_driver_first(rig):
    n = rig.nova
    await n.handle_text("find me a coffee")
    await n.open_place(place_id=PLUR, speak=False)
    await n.handle_text("add a caramel latte")
    assert n.shop.cart
    respx.post(URL).mock(side_effect=script(sse(tools=[("clear_cart", "{}")])))
    await rig.brain.turn("get rid of everything")
    assert n.shop.cart and n.shop.awaiting == "clear_cart" and "Clear your cart?" in n._spk.said
    await n.handle_text("yes")
    assert not n.shop.cart
