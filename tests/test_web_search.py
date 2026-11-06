"""Exa web search: request shape, privacy scrub, notices vs warnings, key hygiene, visibility, degradation."""

from __future__ import annotations

import json
import logging
import re

import httpx
import pytest
import respx
from pydantic import SecretStr

from driveos.agent.harness.tools import Dispatcher, ToolEnv, ToolRegistry
from driveos.agent.harness.web_search import WebSearch, scrub_query
from driveos.container import build_services
from driveos.core.http import Http

from .agent_util import RecSpeaker, make_settings

EXA_KEY = "exa-TESTKEY-0123456789abcdef"
URL = "https://api.exa.ai/search"
OBJ = "Find the closing time today for the named cafe; prefer its own site or Google/Yelp listings."


def payload(**over):
    body = {"requestId": "req-1", "searchTime": 412.0, "costDollars": {"total": 0.007},
            "results": [
                {"title": "Philz Coffee - Palo Alto", "url": "https://www.philzcoffee.com/palo-alto",
                 "publishedDate": "2026-10-01", "highlights": ["Open daily 6am to 8pm. " * 40]},
                {"title": "Philz on Yelp", "url": "https://www.yelp.com/biz/philz", "highlights": ["Great mocha."]}]}
    body.update(over)
    return body


@pytest.fixture
async def rig(settings, tmp_path):
    make_settings(settings, tmp_path)
    settings.nlu.exa.api_key = SecretStr(EXA_KEY)
    spk = RecSpeaker()
    s = build_services(settings, speaker=spk)
    s.face.scan_s = 0.05
    await s.start()
    s.nova._spk = spk
    yield s, spk
    await s.aclose()


async def call(s, args: dict):
    env = ToolEnv("t1", web_search=s.brain.web, redact_terms=("Alex",))
    return await s.brain.dispatcher.run("web_search", json.dumps(args), env, 0)


# ------------------------------------------------------------------ visibility
def test_hidden_without_web_search_enabled():
    assert "web_search" not in {s.name for s in ToolRegistry().visible()}


def test_visible_when_enabled_and_schema_requires_objective():
    reg = ToolRegistry(web_search=True)
    names = {s.name for s in reg.visible()}
    assert "web_search" in names and len(names) == 19
    sch = next(x for x in reg.schemas() if x["function"]["name"] == "web_search")["function"]["parameters"]
    assert set(sch["required"]) == {"query", "objective"} and sch["additionalProperties"] is False
    assert sch["properties"]["objective"]["description"].startswith("Goal for this search turn; say which documents")
    for banned in ("checkout", "approve_payment", "pay", "place_order", "submit_code"):
        assert banned not in names


async def test_brain_hides_tool_without_key(settings, tmp_path):
    make_settings(settings, tmp_path)
    s = build_services(settings, speaker=RecSpeaker())
    try:
        assert s.brain.web is None
        assert "web_search" not in {t.name for t in s.brain.registry.visible()}
    finally:
        await s.aclose()


async def test_brain_hides_tool_when_disabled(settings, tmp_path):
    make_settings(settings, tmp_path)
    settings.nlu.exa.api_key = SecretStr(EXA_KEY)
    settings.nlu.exa.enabled = False
    s = build_services(settings, speaker=RecSpeaker())
    try:
        assert "web_search" not in {t.name for t in s.brain.registry.visible()}
    finally:
        await s.aclose()


async def test_brain_shows_tool_with_key(rig):
    s, _ = rig
    assert "web_search" in {t.name for t in s.brain.registry.visible()}
    assert s.brain.registry.get("approve_payment").llm_visible is False


# ------------------------------------------------------------------ request shape
@respx.mock
async def test_request_body_and_headers(rig):
    s, _ = rig
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    res = await call(s, {"query": "philz coffee closing time", "objective": OBJ})
    assert res.ok
    req = route.calls[0].request
    body = json.loads(req.content)
    assert body["query"] == "philz coffee closing time" and body["objective"] == OBJ
    assert body["type"] == "lite" and body["numResults"] == 5
    assert body["contents"] == {"highlights": True}
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body["userIdentifier"])
    assert req.headers["x-api-key"] == EXA_KEY
    for banned in ("useAutoprompt", "text", "highlights", "numSentences", "livecrawl"):
        assert banned not in body


@respx.mock
async def test_type_and_count_follow_config(rig):
    s, _ = rig
    s.brain.web.cfg.type, s.brain.web.cfg.num_results = "auto", 3
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    await call(s, {"query": "q one", "objective": OBJ})
    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "auto" and body["numResults"] == 3


@respx.mock
async def test_user_identifier_is_opaque_and_stable(rig):
    s, _ = rig
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    await call(s, {"query": "q one", "objective": OBJ})
    await call(s, {"query": "q two", "objective": OBJ})
    ids = {json.loads(c.request.content)["userIdentifier"] for c in route.calls}
    assert len(ids) == 1
    uid = ids.pop()
    assert "Alex" not in uid and "driver" not in uid.lower()


# ------------------------------------------------------------------ privacy
def test_scrub_query_removes_pii():
    q = ("call me on (408) 555-0199 or +1 408 555 0199, mail alex@example.com, card 4111 1111 1111 1111, "
         "I live at 123 Main Street, Los Gatos")
    out = scrub_query(q)
    for leaked in ("555", "0199", "example.com", "4111", "123 Main"):
        assert leaked not in out


def test_scrub_keeps_ordinary_queries():
    assert scrub_query("philz coffee palo alto closing time") == "philz coffee palo alto closing time"


@respx.mock
async def test_outgoing_query_has_no_pii_and_no_gps(rig):
    s, _ = rig
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    await call(s, {"query": "is Alex's cafe at 42 Oak Avenue ok, phone 408-555-0123, a@b.io",
                   "objective": "reviews for 42 Oak Avenue cafe, call 408-555-0123"})
    raw = route.calls[0].request.content.decode()
    for leaked in ("Alex", "42 Oak", "555", "a@b.io"):
        assert leaked not in raw
    assert not re.search(r"-?\d+\.\d{3,}", raw)     # no coordinates


@respx.mock
async def test_near_me_gets_coarse_city_only(rig):
    s, _ = rig
    s.nova.location_label = "Palo Alto, California"
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    await call(s, {"query": "best ramen near me", "objective": OBJ})
    body = json.loads(route.calls[0].request.content)
    assert "Palo Alto" in body["query"] and "California" not in body["query"]


@respx.mock
async def test_near_me_skips_raw_coordinate_label(rig):
    s, _ = rig
    s.nova.location_label = "37.2358, -121.9624"
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    await call(s, {"query": "best ramen near me", "objective": OBJ})
    assert "37." not in route.calls[0].request.content.decode()


# ------------------------------------------------------------------ response handling
@respx.mock
async def test_result_is_compact_and_budgeted(rig):
    s, _ = rig
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    res = await call(s, {"query": "philz hours", "objective": OBJ})
    r = res.result
    assert r["ok"] and r["count"] == 2
    first = r["results"][0]
    assert first["title"].startswith("Philz") and first["url"].startswith("https://") and first["published"] == "2026-10-01"
    assert sum(len(h) for x in r["results"] for h in x["highlights"]) <= s.brain.web.cfg.max_chars
    assert "requestId" not in json.dumps(r) and "costDollars" not in json.dumps(r)


@respx.mock
async def test_notices_reach_model_warnings_do_not(rig, caplog):
    s, _ = rig
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload(
        notices=["Results are from cache."], warnings=["WARN-ONLY-SECRET-TEXT: deprecated field"])))
    with caplog.at_level(logging.DEBUG):
        res = await call(s, {"query": "philz hours", "objective": OBJ})
    assert res.result["notices"] == ["Results are from cache."]
    assert "WARN-ONLY-SECRET-TEXT" not in json.dumps(res.result)
    msg = Dispatcher.message_content(s.brain.registry.get("web_search"), res)
    assert "WARN-ONLY" not in msg and "Results are from cache." in msg
    assert "WARN-ONLY-SECRET-TEXT" in caplog.text


@respx.mock
async def test_logs_metrics_not_query_or_key(rig, caplog):
    s, _ = rig
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
    with caplog.at_level(logging.DEBUG):
        await call(s, {"query": "very distinctive query text", "objective": OBJ})
    assert "req-1" in caplog.text and "0.007" in caplog.text
    assert "very distinctive query text" not in caplog.text
    assert EXA_KEY not in caplog.text


@respx.mock
async def test_key_not_logged_on_failure(rig, caplog):
    s, _ = rig
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": f"bad key {EXA_KEY}"}))
    with caplog.at_level(logging.DEBUG):
        res = await call(s, {"query": "philz hours", "objective": OBJ})
    assert not res.ok
    assert EXA_KEY not in caplog.text


# ------------------------------------------------------------------ degradation
@respx.mock
@pytest.mark.parametrize("effect", [httpx.ReadTimeout("slow"), httpx.Response(503), httpx.Response(401),
                                    httpx.Response(200, text="not json")])
async def test_failure_speaks_short_notice(rig, effect):
    s, spk = rig
    respx.post(URL).mock(side_effect=effect) if isinstance(effect, Exception) else \
        respx.post(URL).mock(return_value=effect)
    res = await call(s, {"query": "philz hours", "objective": OBJ})
    assert not res.ok and res.result["error"] == "search_unavailable" and res.result["spoken"] is True
    assert spk.said[-1] == "I couldn't search the web right now."


async def test_invalid_args_rejected(rig):
    s, _ = rig
    res = await call(s, {"query": "philz hours"})
    assert res.result["error"] == "invalid_args"


async def test_web_search_client_uses_http_wrapper():
    from driveos.config import ExaConfig

    ws = WebSearch(ExaConfig(api_key=SecretStr(EXA_KEY)), Http(), identity="dev:driver")
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(200, json=payload()))
        out = await ws.search("q one", OBJ)
    assert out["ok"]
    await ws.http.aclose()
