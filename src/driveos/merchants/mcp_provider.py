"""Merchant provider backed by any MCP server that implements the DriveOS merchant tool contract
(see driveos.mcp_server.server for the reference implementation).

One long-lived MCP session per server, opened lazily, reconnected on failure. Results are read from
`structured_content` (MCP SDK v2), falling back to JSON in the first text block.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from ..config import McpServerConfig
from ..core.ids import utcnow
from ..domain.errors import (
    DriveOSError,
    ItemUnavailable,
    MerchantRejected,
    ProviderUnavailable,
    QuoteMismatch,
)
from ..domain.models import (
    Cart,
    GeoPoint,
    Menu,
    OrderStatus,
    OrderUpdate,
    PaymentAuthorization,
    Place,
    PlacedOrder,
    Quote,
    Route,
)
from ..nav.geo import encode_polyline

log = logging.getLogger("driveos.mcp.client")
CALL_TIMEOUT_S = 12.0


class McpMerchantProvider:
    capture_before_accept = False

    def __init__(self, cfg: McpServerConfig, *, extra_env: dict[str, str] | None = None):
        self.cfg = cfg
        self.source = f"mcp:{cfg.name}"
        self._extra_env = extra_env or {}
        self._queue: asyncio.Queue | None = None
        self._runner: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._menus: dict[str, Menu] = {}
        self._non_orderable: set[str] = set()

    def _target(self) -> Any:
        if self.cfg.url:
            return self.cfg.url
        import os
        env = {**os.environ, **self.cfg.env, **self._extra_env}
        return StdioServerParameters(command=self.cfg.command or "driveos-merchant-mcp", args=self.cfg.args, env=env)

    async def _run(self, queue: asyncio.Queue, ready: asyncio.Future) -> None:
        """Owns the MCP session for its whole life (anyio scopes must open and close in one task)."""
        try:
            async with Client(self._target(), read_timeout_seconds=CALL_TIMEOUT_S) as client:
                log.info("connected to MCP merchant server %s", self.cfg.name)
                if not ready.done():
                    ready.set_result(True)
                while True:
                    tool, args, fut = await queue.get()
                    if tool is None:
                        return
                    try:
                        res = await asyncio.wait_for(client.call_tool(tool, args), CALL_TIMEOUT_S)
                        if not fut.done():
                            fut.set_result(res)
                    except Exception as e:  # session is suspect: report and restart on next call
                        if not fut.done():
                            fut.set_exception(e)
                        return
        except Exception as e:
            if not ready.done():
                ready.set_exception(ProviderUnavailable(f"mcp server {self.cfg.name} failed to start: {e}"))
            log.warning("mcp session %s ended: %s", self.cfg.name, type(e).__name__)
        finally:
            while queue is not None and not queue.empty():
                _, _, fut = queue.get_nowait()
                if fut and not fut.done():
                    fut.set_exception(ProviderUnavailable("mcp session closed"))

    async def _ensure(self) -> asyncio.Queue:
        if self._runner is None or self._runner.done():
            loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue()
            self._ready = loop.create_future()
            self._runner = asyncio.create_task(self._run(self._queue, self._ready), name=f"mcp-{self.cfg.name}")
        assert self._ready is not None and self._queue is not None
        await asyncio.wait_for(asyncio.shield(self._ready), 30.0)
        return self._queue

    async def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        res = None
        for attempt in (1, 2):
            try:
                queue = await self._ensure()
                fut = asyncio.get_running_loop().create_future()
                queue.put_nowait((tool, args, fut))
                res = await asyncio.wait_for(fut, CALL_TIMEOUT_S + 2)
                break
            except Exception as e:
                log.warning("mcp %s.%s failed (%s), attempt %d", self.cfg.name, tool, type(e).__name__, attempt)
                if attempt == 2:
                    raise ProviderUnavailable(f"mcp {tool}: {e}") from e
        assert res is not None
        if res.is_error:
            raise ProviderUnavailable(f"mcp {tool} error: {_text(res)[:200]}")
        data = res.structured_content if isinstance(res.structured_content, dict) else None
        if data is None:
            try:
                data = json.loads(_text(res) or "{}")
            except json.JSONDecodeError as e:
                raise ProviderUnavailable(f"mcp {tool}: non-JSON result") from e
        if "result" in data and len(data) == 1 and isinstance(data["result"], dict):
            data = data["result"]
        if "error" in data:
            err = data["error"] or {}
            code = err.get("code", "error")
            msg = err.get("message", "")
            if code == "quote_mismatch":
                raise QuoteMismatch(msg)
            if code == "item_unavailable":
                raise ItemUnavailable(msg, spoken=err.get("spoken") or None)
            raise MerchantRejected(f"{code}: {msg}", spoken=err.get("spoken") or None)
        return data

    async def _reset(self) -> None:
        if self._runner and not self._runner.done() and self._queue is not None:
            self._queue.put_nowait((None, None, None))
            try:
                await asyncio.wait_for(self._runner, 5.0)
            except Exception:
                self._runner.cancel()
        self._runner = None

    # ---------------------------------------------------------------- contract
    async def search(self, *, near: GeoPoint, route: Route | None, category: str, query: str | None,
                     limit: int) -> list[Place]:
        args: dict[str, Any] = {"latitude": near.lat, "longitude": near.lon, "category": category, "limit": limit}
        if query:
            args["query"] = query
        if route and len(route.geometry) >= 2:
            args["route_polyline6"] = encode_polyline(route.geometry[:: max(1, len(route.geometry) // 300)] +
                                                      [route.geometry[-1]], 6)
        data = await self.call("search_restaurants", args)
        out: list[Place] = []
        for d in data.get("places", []):
            orderable = bool(d.pop("orderable", True))
            attribution = d.pop("photo_attribution", "")
            d["provider"] = self.source
            d.setdefault("id", f"{self.source}:{d.get('merchant_id')}")
            p = Place.model_validate(d)
            p.extra.update({"orderable": orderable, "photo_attribution": attribution})
            if not orderable:
                self._non_orderable.add(p.merchant_id)
            out.append(p)
        return out

    async def get_menu(self, merchant_id: str) -> Menu:
        if merchant_id not in self._menus:
            data = await self.call("get_menu", {"merchant_id": merchant_id})
            self._menus[merchant_id] = Menu.model_validate(data["menu"])
        return self._menus[merchant_id]

    @staticmethod
    def _lines(cart_or_quote) -> list[dict[str, Any]]:
        return [{"item_id": ln.item_id, "quantity": ln.quantity, "selections": ln.selections}
                for ln in cart_or_quote.lines]

    async def quote(self, cart: Cart, *, merchant_name: str = "") -> Quote:
        data = await self.call("quote_order", {"merchant_id": cart.merchant_id, "lines": self._lines(cart)})
        q = Quote.model_validate(data["quote"])
        return q.model_copy(update={"provider": self.source})

    async def place_order(self, quote: Quote, *, payment: PaymentAuthorization, customer_name: str,
                          pickup_eta: datetime | None, idempotency_key: str) -> PlacedOrder:
        data = await self.call("place_order", {
            "merchant_id": quote.merchant_id, "lines": self._lines(quote), "expected_total": quote.total.amount,
            "currency": quote.total.currency, "payment_reference": payment.auth_id, "payment_status": payment.status,
            "customer_name": customer_name, "idempotency_key": idempotency_key,
            "pickup_eta": pickup_eta.isoformat() if pickup_eta else None,
        })
        o = PlacedOrder.model_validate(data["order"])
        return o.model_copy(update={"provider": self.source})

    async def watch(self, order: PlacedOrder) -> AsyncIterator[OrderUpdate]:
        cursor = 0
        while True:
            try:
                data = await self.call("get_order_updates", {"order_id": order.order_id, "cursor": cursor})
            except ProviderUnavailable:
                await asyncio.sleep(3.0)
                continue
            cursor = data.get("cursor", cursor)
            for u in data.get("updates", []):
                upd = OrderUpdate.model_validate(u)
                yield upd
                if upd.status in (OrderStatus.PICKED_UP, OrderStatus.CANCELED, OrderStatus.REJECTED):
                    return
            await asyncio.sleep(1.0)

    async def notify_arrival(self, order: PlacedOrder) -> None:
        try:
            await self.call("notify_arrival", {"order_id": order.order_id})
        except DriveOSError as e:
            log.warning("notify_arrival failed: %s", e)

    async def cancel(self, order: PlacedOrder) -> bool:
        data = await self.call("cancel_order", {"order_id": order.order_id})
        return bool(data.get("ok"))

    async def aclose(self) -> None:
        await self._reset()


def _text(res) -> str:
    for block in res.content or []:
        if isinstance(block, TextContent):
            return block.text
    return ""


__all__ = ["McpMerchantProvider", "utcnow"]
