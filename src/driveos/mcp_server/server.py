"""driveos-merchant-mcp — an MCP server that gives vehicles restaurant data and ordering.

Tools (structured JSON results):
  search_restaurants   orderable merchants + real restaurants along a route (photos, ratings, price level)
  get_menu             items, prices, images, modifiers
  quote_order          binding quote (subtotal, tax, total, expiry)
  place_order          idempotent order placement against an authorised payment
  get_order_updates    status timeline since a cursor
  notify_arrival       customer is at the pickup spot
  cancel_order         cancel inside the merchant's window

Backends:
  * the bundled merchant network (demo catalog + simulated kitchen) — orderable;
  * Google Places (GOOGLE_PLACES_API_KEY) — real nearby restaurants with photos, rating and price level,
    returned as `orderable: false` (navigate and pay in store) until the merchant is onboarded.
Run:  driveos-merchant-mcp            (stdio)
      driveos-merchant-mcp --http 8765 (streamable HTTP at /mcp)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime
from typing import Any

from mcp.server import MCPServer

from ..core.http import Http
from ..core.logging_setup import setup_logging
from ..domain.errors import DriveOSError
from ..domain.models import Cart, CartLine, GeoPoint, PaymentAuthorization, Quote, Route
from ..domain.money import Money
from ..enrichment.google_places import GooglePlaces
from ..merchants.base import make_line
from ..merchants.local import LocalMerchantProvider
from ..merchants.local_catalog import LocalCatalog
from ..nav.geo import decode_polyline, with_cumulative

log = logging.getLogger("driveos.mcp")

INSTRUCTIONS = (
    "Restaurant discovery and pickup ordering for vehicles. Money amounts are integer minor units. "
    "Only merchants with orderable=true accept place_order; the caller must hold an authorised payment "
    "for exactly the quoted total before calling place_order."
)


class _State:
    def __init__(self) -> None:
        scale = float(os.environ.get("DRIVEOS_TIME_SCALE", "1.0"))
        self.demo = (LocalMerchantProvider(LocalCatalog(), time_scale=scale)
                     if os.environ.get("DRIVEOS_MCP_DEMO", "1") != "0" else None)
        self.http = Http()
        key = os.environ.get("GOOGLE_PLACES_API_KEY") or os.environ.get("DRIVEOS_GOOGLE_PLACES__API_KEY")
        self.places = GooglePlaces(key, self.http) if key else None
        self.quotes: dict[str, Quote] = {}
        self.updates: dict[str, list[dict[str, Any]]] = {}
        self.orders: dict[str, Any] = {}
        self.tasks: set[asyncio.Task] = set()


_state: _State | None = None


def state() -> _State:
    global _state
    if _state is None:
        _state = _State()
    return _state


server = MCPServer("driveos-merchants", instructions=INSTRUCTIONS, version="1.0.0")


def _err(e: Exception) -> dict[str, Any]:
    code = e.code if isinstance(e, DriveOSError) else "error"
    return {"error": {"code": code, "message": str(e), "spoken": getattr(e, "spoken", "")}}


@server.tool()
async def search_restaurants(latitude: float, longitude: float, category: str = "coffee",
                             query: str | None = None, route_polyline6: str | None = None,
                             limit: int = 6) -> dict[str, Any]:
    """Find places to stop along the vehicle's route (or near a point).

    category: coffee | food | bakery | restaurant. route_polyline6: remaining route, polyline precision 6.
    Returns places with name, location, rating, price_level (1-4), photo_url, orderable and, for orderable
    merchants, prep_minutes and pickup modes.
    """
    s = state()
    near = GeoPoint(lat=latitude, lon=longitude)
    route = None
    if route_polyline6:
        geom = decode_polyline(route_polyline6, 6)
        if len(geom) >= 2:
            route = with_cumulative(Route(geometry=geom, distance_m=0, duration_s=1))
    out: list[dict[str, Any]] = []
    if s.demo:
        for p in await s.demo.search(near=near, route=route, category=category, query=query, limit=limit):
            d = p.model_dump(mode="json")
            d["orderable"] = True
            d["photo_url"] = d.get("photo_url")
            out.append(d)
    if s.places and route:
        try:
            for g in await s.places.along_route(category, route.geometry, origin=near, query=query, limit=limit):
                if not g["location"]["lat"]:
                    continue
                photo = await s.places.photo_uri(g["photo_name"]) if g.get("photo_name") else None
                out.append({
                    "id": f"places:{g['id']}", "merchant_id": g["id"], "provider": "places", "name": g["name"],
                    "location": g["location"], "address": g["address"], "category": category,
                    "cuisine": g.get("cuisine", ""), "rating": g.get("rating"), "rating_count": g.get("rating_count"),
                    "price_level": g.get("price_level"), "photo_url": photo,
                    "photo_attribution": g.get("photo_attribution", ""), "open_now": g.get("open_now"),
                    "orderable": False, "pickup_modes": ["walk_in"], "prep_minutes": 0,
                })
        except DriveOSError as e:
            log.warning("google places unavailable: %s", e)
    return {"places": out[: max(1, limit) * 3]}


@server.tool()
async def get_menu(merchant_id: str) -> dict[str, Any]:
    """Full menu for an orderable merchant: items with base_price, image_url and modifier_groups."""
    s = state()
    try:
        if not s.demo:
            raise DriveOSError("no orderable backend")
        return {"menu": (await s.demo.get_menu(merchant_id)).model_dump(mode="json")}
    except KeyError:
        return {"error": {"code": "unknown_merchant", "message": merchant_id}}


def _cart(merchant_id: str, lines: list[dict[str, Any]], menu) -> Cart:
    out: list[CartLine] = []
    for i, ln in enumerate(lines):
        item = menu.item(ln["item_id"])
        if not item:
            raise DriveOSError(f"unknown item {ln['item_id']}")
        out.append(make_line(f"l{i}", item, ln.get("selections") or {}, int(ln.get("quantity", 1))))
    return Cart(merchant_id=merchant_id, lines=out)


@server.tool()
async def quote_order(merchant_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Binding quote. lines: [{item_id, quantity, selections: {group_id: [option_id]}}]."""
    s = state()
    try:
        menu = await s.demo.get_menu(merchant_id)
        q = await s.demo.quote(_cart(merchant_id, lines, menu))
        s.quotes[q.quote_id] = q
        return {"quote": q.model_dump(mode="json")}
    except Exception as e:
        return _err(e)


@server.tool()
async def place_order(merchant_id: str, lines: list[dict[str, Any]], expected_total: int, currency: str,
                      payment_reference: str, payment_status: str, customer_name: str,
                      idempotency_key: str, pickup_eta: str | None = None) -> dict[str, Any]:
    """Place a pickup order. expected_total (minor units) must equal a fresh quote and the authorised payment."""
    s = state()
    try:
        menu = await s.demo.get_menu(merchant_id)
        q = await s.demo.quote(_cart(merchant_id, lines, menu))
        if q.total.amount != expected_total or q.total.currency != currency:
            return {"error": {"code": "quote_mismatch", "message": f"total is {q.total.amount}"}}
        pay = PaymentAuthorization(auth_id=payment_reference, provider="external",
                                   amount=Money(amount=expected_total, currency=currency), status=payment_status)
        eta = datetime.fromisoformat(pickup_eta) if pickup_eta else None
        order = await s.demo.place_order(q, payment=pay, customer_name=customer_name, pickup_eta=eta,
                                         idempotency_key=idempotency_key)
        s.orders[order.order_id] = order
        if order.order_id not in s.updates:
            s.updates[order.order_id] = []
            t = asyncio.create_task(_pump(order))
            s.tasks.add(t)
            t.add_done_callback(s.tasks.discard)
        return {"order": order.model_dump(mode="json")}
    except Exception as e:
        return _err(e)


async def _pump(order) -> None:
    s = state()
    async for upd in s.demo.watch(order):
        s.updates[order.order_id].append(upd.model_dump(mode="json"))


@server.tool()
async def get_order_updates(order_id: str, cursor: int = 0) -> dict[str, Any]:
    """Status updates for an order after `cursor` (index). Statuses: placed, accepted, preparing, ready,
    picked_up, rejected, canceled."""
    ups = state().updates.get(order_id)
    if ups is None:
        return {"error": {"code": "unknown_order", "message": order_id}}
    return {"updates": ups[cursor:], "cursor": len(ups)}


@server.tool()
async def notify_arrival(order_id: str) -> dict[str, Any]:
    """Tell the merchant the customer has arrived at the pickup spot."""
    s = state()
    o = s.orders.get(order_id)
    if not o:
        return {"error": {"code": "unknown_order", "message": order_id}}
    await s.demo.notify_arrival(o)
    return {"ok": True}


@server.tool()
async def cancel_order(order_id: str) -> dict[str, Any]:
    """Cancel an order that is not yet ready. Returns ok=false if the kitchen can no longer cancel."""
    s = state()
    o = s.orders.get(order_id)
    if not o:
        return {"error": {"code": "unknown_order", "message": order_id}}
    return {"ok": await s.demo.cancel(o)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="driveos-merchant-mcp")
    ap.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on this port instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    setup_logging(os.environ.get("DRIVEOS_LOG_LEVEL", "WARNING"))
    if a.http:
        server.run(transport="streamable-http", host=a.host, port=a.http)
    else:
        server.run()


if __name__ == "__main__":
    main()
