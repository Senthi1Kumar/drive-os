"""Shared fixtures and helpers for the agent harness tests: a scripted OpenRouter and a recording speaker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import respx
from pydantic import SecretStr

KEY = "sk-or-v1-TESTKEY-0123456789abcdef"
URL = "https://openrouter.ai/api/v1/chat/completions"
PRIMARY = "deepseek/deepseek-v4.1-flash"
FALLBACK = "anthropic/claude-haiku-5.5"


class RecSpeaker:
    def __init__(self):
        self.said: list[str] = []
        self.stopped = 0

    async def say(self, text: str) -> None:
        self.said.append(text)

    def stop(self) -> None:
        self.stopped += 1


def sse(text: str | None = None, tools: list[tuple[str, str]] | None = None, model: str = PRIMARY,
        cost: float = 0.0001) -> httpx.Response:
    chunks: list[dict] = [{"id": "x", "model": model, "provider": "Together", "choices": [{"delta": {"role": "assistant"}}]}]
    if text:
        mid = max(1, len(text) // 2)
        for part in (text[:mid], text[mid:]):
            chunks.append({"model": model, "choices": [{"delta": {"content": part}}]})
    for i, (name, args) in enumerate(tools or []):
        chunks.append({"choices": [{"delta": {"tool_calls": [{"index": i, "id": f"call_{i}",
                                                              "function": {"name": name, "arguments": args[:5]}}]}}]})
        chunks.append({"choices": [{"delta": {"tool_calls": [{"index": i, "function": {"arguments": args[5:]}}]}}]})
    chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls" if tools else "stop"}],
                   "usage": {"prompt_tokens": 900, "completion_tokens": 30, "cost": cost,
                             "prompt_tokens_details": {"cached_tokens": 600}}})
    body = ": OPENROUTER PROCESSING\n\n" + "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def script(*responses) -> Callable:
    """A respx side effect that answers successive requests from `responses` (Response, Exception, or callable)."""
    it = iter(responses)

    async def handler(request: httpx.Request):
        try:
            r = next(it)
        except StopIteration:
            return sse("(script exhausted)")
        if callable(r):
            r = r(request)
            if asyncio.iscoroutine(r):
                r = await r
        if isinstance(r, Exception):
            raise r
        return r

    return handler


def bodies(route) -> list[dict]:
    return [json.loads(c.request.content) for c in route.calls]


def make_settings(settings, tmp_path, *, key: str | None = KEY, **budget):
    settings.nlu.engine = "harness"
    settings.nlu.openrouter.api_key = SecretStr(key) if key else None
    settings.nlu.session.store_path = tmp_path / "sessions.db"
    for k, v in budget.items():
        setattr(settings.nlu.budget, k, v)
    return settings


def mock_router():
    return respx.mock(assert_all_called=False)
