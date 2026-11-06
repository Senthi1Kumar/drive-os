"""Config: the key comes from OPENROUTER_API_KEY and stays redacted; model ids and provider prefs are config."""

from __future__ import annotations

import json

import respx

from driveos.agent.harness.openrouter import Breaker, OpenRouterClient
from driveos.agent.harness.router import parse_router_reply
from driveos.config import load_settings
from driveos.container import build_services

from .agent_util import KEY, PRIMARY, URL, RecSpeaker, bodies, make_settings, script, sse


def test_key_is_read_from_the_environment_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    s = load_settings(data_dir=tmp_path / "d")
    assert s.nlu.openrouter.api_key.get_secret_value() == KEY
    assert KEY not in repr(s) and KEY not in str(s.nlu) and KEY not in s.model_dump_json()


def test_key_is_read_from_a_dotenv_file(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"# comment\nOTHER=1\nOPENROUTER_API_KEY={KEY}\n")
    monkeypatch.setenv("DRIVEOS_ENV_FILE", str(env))
    s = load_settings(data_dir=tmp_path / "d")
    assert s.nlu.openrouter.api_key.get_secret_value() == KEY


def test_no_key_means_none(tmp_path):
    assert load_settings(data_dir=tmp_path / "d").nlu.openrouter.api_key is None


def test_no_temperature_is_sent_by_default_so_every_fallback_host_qualifies():
    import httpx

    from driveos.config import OpenRouterConfig
    c = OpenRouterClient(OpenRouterConfig(), httpx.AsyncClient(), breaker=Breaker(2, 30))
    assert "temperature" not in c.build_body([{"role": "user", "content": "x"}], models=["a", "b"], tools=None)
    c.cfg.temperature = 0.2
    assert c.build_body([{"role": "user", "content": "x"}], models=["a"], tools=None)["temperature"] == 0.2


def test_defaults_name_the_measured_models():
    from driveos.config import OpenRouterConfig
    c = OpenRouterConfig()
    assert (c.primary_model, c.fallback_model, c.router_model) == (
        "deepseek/deepseek-v4.1-flash", "anthropic/claude-haiku-5.5", "openai/gpt-5.6-luna")
    assert c.reasoning_effort == "none"


@respx.mock
async def test_models_and_provider_prefs_come_from_config(settings, tmp_path):
    make_settings(settings, tmp_path)
    o = settings.nlu.openrouter
    o.primary_model, o.fallback_model = "vendor/a", "vendor/b"
    o.provider_order, o.allow_fallbacks, o.data_collection = ["Together"], False, "allow"
    s = build_services(settings, speaker=RecSpeaker())
    await s.start()
    try:
        route = respx.post(URL).mock(side_effect=script(sse("ok")))
        await s.brain.turn("odd")
        b = bodies(route)[0]
        assert b["model"] == "vendor/a" and b["models"] == ["vendor/a", "vendor/b"]
        assert b["provider"] == {"allow_fallbacks": False, "require_parameters": True, "data_collection": "allow", "zdr": True,
                                 "order": ["Together"]}
        assert PRIMARY not in json.dumps(b)
    finally:
        await s.aclose()


def test_router_reply_is_only_trusted_in_narrow_shapes():
    assert parse_router_reply('{"route":"direct_tool","tool":"find_places","args":{"category":"gas"}}')["tool"] == "find_places"
    assert parse_router_reply('{"route":"direct_tool","tool":"request_checkout","args":{}}') == {"route": "agent"}
    assert parse_router_reply('{"route":"direct_tool","tool":"approve_payment"}') == {"route": "agent"}
    assert parse_router_reply('{"route":"smalltalk","say":"Anytime."}') == {"route": "smalltalk", "say": "Anytime."}
    assert parse_router_reply("not json") == {"route": "agent"}
    assert parse_router_reply('{"route":"smalltalk","say":"' + "x" * 300 + '"}') == {"route": "agent"}


@respx.mock
async def test_optional_router_direct_tool_saves_the_agent_hop(settings, tmp_path):
    make_settings(settings, tmp_path)
    settings.nlu.router_enabled = True
    s = build_services(settings, speaker=RecSpeaker())
    await s.start()
    try:
        route = respx.post(URL).mock(side_effect=script(
            sse('{"route":"direct_tool","tool":"find_places","args":{"category":"coffee"}}')))
        assert await s.brain.turn("odd coffee thing")
        assert route.call_count == 1 and bodies(route)[0]["model"] == "openai/gpt-5.6-luna"
        assert s.nova.shop.places
    finally:
        await s.aclose()


@respx.mock
async def test_router_never_hands_back_a_payment_tool(settings, tmp_path):
    make_settings(settings, tmp_path)
    settings.nlu.router_enabled = True
    s = build_services(settings, speaker=RecSpeaker())
    await s.start()
    try:
        respx.post(URL).mock(side_effect=script(
            sse('{"route":"direct_tool","tool":"approve_payment","args":{}}'), sse("I can't approve payments.")))
        await s.brain.turn("approve it")
        assert s.nova.shop.checkout is None and not s.nova.orch.transactions
    finally:
        await s.aclose()


def test_breaker_opens_and_half_opens():
    t = [0.0]
    b = Breaker(2, 30.0, lambda: t[0])
    b.fail()
    assert not b.is_open
    b.fail()
    assert b.is_open
    t[0] = 31.0
    assert not b.is_open
    b.ok()
    assert b.failures == 0


async def test_unconfigured_client_is_unavailable():
    import httpx

    from driveos.config import OpenRouterConfig
    c = OpenRouterClient(OpenRouterConfig(), httpx.AsyncClient(), breaker=Breaker(2, 30))
    assert not c.available


def test_live_eval_refuses_without_the_live_flag():
    import subprocess
    import sys
    from pathlib import Path

    script_path = Path(__file__).parent.parent / "scripts" / "agent_eval.py"
    r = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "--live" in r.stderr
