"""OpenRouter chat client: streaming SSE, tool-call assembly, fallback models, per-call timeout, circuit breaker.

The API key is read from a SecretStr only when the request headers are built; nothing here logs headers or bodies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ...config import OpenRouterConfig

log = logging.getLogger("driveos.openrouter")


class LlmUnavailable(Exception):
    """The model path is down, slow, or has no key. The caller drops to on-device rules."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


RETRY_ON_FALLBACK = {"http_400", "http_404", "http_422", "http_429", "http_502", "http_503", "stream_error"}


class LlmCancelled(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args_json: str


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish: str | None = None
    model: str | None = None
    provider: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    ttft_ms: int | None = None
    fallback_used: bool = False


class Breaker:
    """Opens after `threshold` consecutive failures; half-opens after `open_s`."""

    def __init__(self, threshold: int, open_s: float, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.open_s, self.clock = threshold, open_s, clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self.clock() - self.opened_at >= self.open_s:
            return False                       # half-open: let one request probe
        return True

    def ok(self) -> None:
        self.failures, self.opened_at = 0, None

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()


class OpenRouterClient:
    def __init__(self, cfg: OpenRouterConfig, http: httpx.AsyncClient, *, breaker: Breaker,
                 call_timeout_s: float = 3.5):
        self.cfg = cfg
        self.http = http
        self.breaker = breaker
        self.call_timeout_s = call_timeout_s

    @property
    def configured(self) -> bool:
        return self.cfg.api_key is not None and bool(self.cfg.api_key.get_secret_value())

    @property
    def available(self) -> bool:
        return self.configured and not self.breaker.is_open

    def _headers(self) -> dict[str, str]:
        assert self.cfg.api_key is not None
        return {"Authorization": "Bearer " + self.cfg.api_key.get_secret_value(),
                "Content-Type": "application/json", "X-Title": self.cfg.app_title,
                "HTTP-Referer": self.cfg.app_referer}

    def _provider_prefs(self) -> dict[str, Any]:
        c = self.cfg
        p: dict[str, Any] = {"allow_fallbacks": c.allow_fallbacks, "require_parameters": c.require_parameters,
                             "data_collection": c.data_collection}
        if c.zdr:
            p["zdr"] = True
        if c.provider_order:
            p["order"] = list(c.provider_order)
        elif c.provider_sort:
            p["sort"] = c.provider_sort
        return p

    def build_body(self, messages: list[dict[str, Any]], *, models: list[str], tools: list[dict[str, Any]] | None,
                   max_tokens: int | None = None, json_mode: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"messages": messages, "stream": True,
                                "max_tokens": max_tokens or self.cfg.max_tokens, "usage": {"include": True},
                                "provider": self._provider_prefs()}
        if len(models) == 1:
            body["model"] = models[0]
        else:
            body["model"], body["models"] = models[0], models
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        if self.cfg.reasoning_effort:
            body["reasoning"] = {"effort": self.cfg.reasoning_effort}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
                   models: list[str] | None = None, max_tokens: int | None = None, json_mode: bool = False,
                   timeout_s: float | None = None) -> ChatResult:
        """One streamed completion. Raises LlmUnavailable on any failure the caller should route around.

        A first-byte timeout on the `models` array retries once on the fallback model alone, because the
        server-side fallback only fires on errors, not on a slow primary.
        """
        if not self.configured:
            raise LlmUnavailable("no_key")
        if self.breaker.is_open:
            raise LlmUnavailable("breaker_open")
        chain = models or [self.cfg.primary_model, self.cfg.fallback_model]
        t_budget = timeout_s if timeout_s is not None else self.call_timeout_s
        try:
            res = await self._once(messages, chain, tools, max_tokens, json_mode, t_budget)
        except TimeoutError:
            if len(chain) > 1:
                log.warning("primary model slow (> %.1fs); retrying on %s", t_budget, chain[1])
                try:
                    res = await self._once(messages, chain[1:], tools, max_tokens, json_mode, t_budget)
                    res.fallback_used = True
                except (TimeoutError, LlmUnavailable) as e:
                    self.breaker.fail()
                    raise LlmUnavailable("timeout") from e
            else:
                self.breaker.fail()
                raise LlmUnavailable("timeout") from None
        except LlmUnavailable as e:
            # a rejected primary (bad id, rate limit, upstream error) is retried on the fallback vendor alone
            if len(chain) > 1 and e.reason in RETRY_ON_FALLBACK:
                log.warning("primary rejected (%s); retrying on %s", e.reason, chain[1])
                try:
                    res = await self._once(messages, chain[1:], tools, max_tokens, json_mode, t_budget)
                    res.fallback_used = True
                except (TimeoutError, LlmUnavailable) as e2:
                    self.breaker.fail()
                    raise LlmUnavailable(getattr(e2, "reason", "timeout")) from e2
            else:
                self.breaker.fail()
                raise
        self.breaker.ok()
        if res.model and res.model != chain[0]:
            res.fallback_used = True
        return res

    async def _once(self, messages, chain, tools, max_tokens, json_mode, first_byte_s) -> ChatResult:
        body = self.build_body(messages, models=chain, tools=tools, max_tokens=max_tokens, json_mode=json_mode)
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        t0 = time.perf_counter()
        res = ChatResult()
        slots: dict[int, ToolCall] = {}
        cm = self.http.stream("POST", url, headers=self._headers(), json=body,
                              timeout=httpx.Timeout(20.0, connect=min(first_byte_s, 4.0)))
        try:
            resp = await asyncio.wait_for(cm.__aenter__(), first_byte_s)
        except httpx.TransportError as e:
            log.warning("openrouter transport error: %s", type(e).__name__)
            raise LlmUnavailable(type(e).__name__) from e
        try:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:160]
                log.warning("openrouter HTTP %s %s", resp.status_code, _scrub(detail))
                raise LlmUnavailable(f"http_{resp.status_code}")
            lines = resp.aiter_lines().__aiter__()
            got_data = False
            while True:
                try:
                    if got_data:
                        line = await lines.__anext__()
                    else:
                        left = first_byte_s - (time.perf_counter() - t0)
                        line = await asyncio.wait_for(lines.__anext__(), max(0.05, left))
                except StopAsyncIteration:
                    break
                if not line.startswith("data:"):
                    continue
                got_data = True
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ch = json.loads(data)
                except ValueError:
                    continue
                if ch.get("error"):
                    raise LlmUnavailable("stream_error")
                _apply_chunk(res, slots, ch, t0)
        except httpx.TransportError as e:
            log.warning("openrouter transport error: %s", type(e).__name__)
            raise LlmUnavailable(type(e).__name__) from e
        finally:
            await cm.__aexit__(None, None, None)
        res.tool_calls = [slots[i] for i in sorted(slots)]
        return res

    async def warm(self) -> None:
        """Open the pooled connection ahead of the first turn."""
        if not self.configured:
            return
        try:
            await self.http.get(self.cfg.base_url.rstrip("/") + "/models/" + self.cfg.primary_model + "/endpoints",
                                headers=self._headers(), timeout=4.0)
        except Exception:                     # best effort: a failed warm-up changes nothing
            pass


def _apply_chunk(res: ChatResult, slots: dict[int, ToolCall], ch: dict[str, Any], t0: float) -> None:
    res.model = ch.get("model") or res.model
    res.provider = ch.get("provider") or res.provider
    u = ch.get("usage")
    if u:
        res.prompt_tokens = u.get("prompt_tokens") or 0
        res.completion_tokens = u.get("completion_tokens") or 0
        res.cached_tokens = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        res.cost = float(u.get("cost") or 0.0)
    for c in ch.get("choices") or []:
        d = c.get("delta") or {}
        if d.get("content"):
            if res.ttft_ms is None:
                res.ttft_ms = int((time.perf_counter() - t0) * 1000)
            res.text += d["content"]
        for tc in d.get("tool_calls") or []:
            if res.ttft_ms is None:
                res.ttft_ms = int((time.perf_counter() - t0) * 1000)
            s = slots.setdefault(tc.get("index", 0), ToolCall("", "", ""))
            s.id = tc.get("id") or s.id
            f = tc.get("function") or {}
            s.name += f.get("name") or ""
            s.args_json += f.get("arguments") or ""
        if c.get("finish_reason"):
            res.finish = c["finish_reason"]


def _scrub(s: str) -> str:
    import re
    return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-…redacted", s)
