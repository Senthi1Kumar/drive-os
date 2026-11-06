"""In-process merchant source: the DriveOS catalogue with simulated fulfilment.

Behaves like a real merchant integration — binding quotes with expiry, idempotent placement, asynchronous
status updates, cancellation windows — so the wallet, saga and UI run exactly as they would against a
live platform. Fulfilment differs by kind of venue:

* restaurants / stores — accept, prepare (timed to the car's arrival), ready, handed over at pickup
* fuel — the pump is unlocked as soon as the payment hold is accepted; "picked up" once the car arrives
* EV — a stall is reserved; the session starts when the car arrives

``ordering="off"`` (live mode without an ordering backend) keeps menus browsable but refuses quotes, so
the UI offers directions instead of checkout.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from ..core.ids import new_id, pickup_code, utcnow
from ..domain.categories import matches
from ..domain.errors import ItemUnavailable, MerchantRejected, OrderingUnavailable
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
    QuoteLine,
    Route,
)
from ..domain.money import Money
from ..nav.geo import project
from .base import describe_selections, normalise_selections, price_line
from .local_catalog import LocalCatalog

QUOTE_TTL = timedelta(minutes=5)


@dataclass
class _Ticket:
    order: PlacedOrder
    kind: str
    prep_s: float
    placed_mono: float
    status: OrderStatus = OrderStatus.PLACED
    arrived_mono: float | None = None
    ready_at: datetime | None = None
    events: asyncio.Queue = field(default_factory=asyncio.Queue)


class LocalMerchantProvider:
    source = "local"
    capture_before_accept = False

    def __init__(self, catalog: LocalCatalog, *, time_scale: float = 1.0, accept_delay_s: float = 1.5,
                 ordering: str = "simulated", radius_m: float = 12_000, reject_merchants: set[str] | None = None,
                 demo_venues: bool = True):
        self.catalog = catalog
        self.time_scale = max(0.05, time_scale)
        self.accept_delay_s = accept_delay_s
        self.ordering = ordering
        self.radius_m = radius_m
        self.demo_venues = demo_venues
        self.reject_merchants = reject_merchants or set()
        self._by_key: dict[str, PlacedOrder] = {}
        self._tickets: dict[str, _Ticket] = {}
        self._seq = random.randint(12, 40)
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- discovery
    async def search(self, *, near: GeoPoint, route: Route | None, category: str, query: str | None,
                     limit: int) -> list[Place]:
        if self.ordering == "simulated" and self.demo_venues:
            self.catalog.ensure_venues(near)
        q = (query or "").lower().strip()
        out: list[Place] = []
        for m in self.catalog.near(near, self.radius_m):
            if self.ordering != "simulated" and m["source"] in ("venue", "mapbox"):
                continue
            hit_cat = matches(m["tags"], category)
            hit_q = bool(q) and (q in m["name"].lower() or self._menu_mentions(m, q))
            if not (hit_cat or hit_q):
                continue
            p = self.catalog.place(m, provider=self.source)
            p.extra["distance_m"] = m["distance_m"]
            p.extra["orderable"] = self.ordering == "simulated" and self._has_menu(m)
            p.extra["has_menu"] = self._has_menu(m)
            p.extra["matched_item"] = q if hit_q and not hit_cat else ""
            if route:
                pr = project(route, p.location)
                p.distance_along_route_m = pr.along_m
                p.extra["offset_m"] = pr.offset_m
            out.append(p)
        return out[: max(limit, 1) * 3]

    @staticmethod
    def _has_menu(m: dict) -> bool:
        return any(c["items"] for c in m["categories"])

    @staticmethod
    def _menu_mentions(m: dict, q: str) -> bool:
        return any(q in i["name"].lower() for c in m["categories"] for i in c["items"])

    def place(self, merchant_id: str) -> Place:
        p = self.catalog.place(self.catalog.merchant(merchant_id), provider=self.source)
        p.extra["orderable"] = self.ordering == "simulated"
        return p

    async def get_menu(self, merchant_id: str) -> Menu:
        return self.catalog.menu(merchant_id)

    # ---------------------------------------------------------------- pricing
    async def quote(self, cart: Cart, *, merchant_name: str = "") -> Quote:
        if self.ordering != "simulated":
            raise OrderingUnavailable(cart.merchant_id)
        m = self.catalog.merchant(cart.merchant_id)
        menu = self.catalog.menu(cart.merchant_id)
        lines: list[QuoteLine] = []
        subtotal = Money.zero(menu.currency)
        for ln in cart.lines:
            item = menu.item(ln.item_id)
            if not item or not item.available:
                raise ItemUnavailable(ln.name, spoken=f"{ln.name} isn't available right now.")
            sel = normalise_selections(item, ln.selections)
            total = price_line(item, sel) * ln.quantity
            subtotal = subtotal + total
            lines.append(QuoteLine(item_id=item.id, name=item.name, quantity=ln.quantity,
                                   description=describe_selections(item, sel), selections=sel, total=total))
        # fuel and EV prepay are not taxed at the till (tax is in the pump price)
        taxable = Money.zero(menu.currency)
        for ln, ql in zip(cart.lines, lines, strict=True):
            if not ln.item_id.startswith(("fuel-", "ev-")):
                taxable = taxable + ql.total
        tax = taxable.apply_rate(Decimal(m.get("tax_rate", "0")))
        fees = Money.zero(menu.currency)
        return Quote(quote_id=new_id("q"), merchant_id=cart.merchant_id, merchant_name=m["name"], provider=self.source,
                     lines=lines, subtotal=subtotal, tax=tax, fees=fees, total=subtotal + tax + fees,
                     prep_minutes=int(m.get("prep_minutes", 8)), expires_at=utcnow() + QUOTE_TTL)

    # ---------------------------------------------------------------- orders
    async def place_order(self, quote: Quote, *, payment: PaymentAuthorization, customer_name: str,
                          pickup_eta: datetime | None, idempotency_key: str) -> PlacedOrder:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        if quote.merchant_id in self.reject_merchants:
            raise MerchantRejected("merchant is not accepting orders")
        if payment.status not in ("authorized", "captured") or payment.amount != quote.total:
            raise MerchantRejected("payment does not cover the quote")
        await asyncio.sleep(0.2)
        m = self.catalog.merchant(quote.merchant_id)
        kind = m.get("kind", "restaurant")
        self._seq += 1
        prep_s = quote.prep_minutes * 60.0
        if pickup_eta and kind not in ("fuel", "ev"):
            lead = (pickup_eta - utcnow()).total_seconds()
            prep_s = max(prep_s, lead - 30)  # timed to the car's arrival
        ready_at = utcnow() + timedelta(seconds=prep_s / self.time_scale)
        order = PlacedOrder(order_id=new_id("ord"), merchant_id=quote.merchant_id, provider=self.source,
                            pickup_code=pickup_code(self._seq), ready_at=ready_at,
                            pickup_spot=m.get("pickup_spot", ""), pickup_instructions=_instructions(m))
        loop = asyncio.get_running_loop()
        t = _Ticket(order=order, kind=kind, prep_s=prep_s, placed_mono=loop.time(), ready_at=ready_at)
        self._tickets[order.order_id] = t
        self._by_key[idempotency_key] = order
        task = asyncio.create_task(self._fulfil(t))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return order

    async def _fulfil(self, t: _Ticket) -> None:
        loop = asyncio.get_running_loop()
        s = self.time_scale

        async def at(seconds: float) -> None:
            delay = t.placed_mono + seconds / s - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)

        await asyncio.sleep(self.accept_delay_s)
        if t.status == OrderStatus.CANCELED:
            return
        words = {"fuel": ("Pump reserved", "Pump unlocked", "Fueling complete"),
                 "ev": ("Stall reserved", "Charger ready", "Session started"),
                 "store": ("Order received", "Being picked", "Handed over"),
                 "pharmacy": ("Order received", "Being picked", "Handed over")}.get(
            t.kind, ("Order received", "Kitchen started", "Handed over"))
        self._set(t, OrderStatus.ACCEPTED, words[0])
        if t.kind in ("fuel", "ev"):
            self._set(t, OrderStatus.READY, words[1])
        else:
            await at(min(60.0, t.prep_s * 0.25))
            if t.status == OrderStatus.CANCELED:
                return
            self._set(t, OrderStatus.PREPARING, words[1])
            await at(t.prep_s)
            if t.status == OrderStatus.CANCELED:
                return
            self._set(t, OrderStatus.READY, "Ready for pickup")
        while t.arrived_mono is None:
            await asyncio.sleep(0.25)
            if t.status == OrderStatus.CANCELED:
                return
        await asyncio.sleep(max(2.0, 20.0 / s))
        self._set(t, OrderStatus.PICKED_UP, words[2])

    def _set(self, t: _Ticket, status: OrderStatus, message: str) -> None:
        t.status = status
        t.events.put_nowait(OrderUpdate(order_id=t.order.order_id, status=status, ready_at=t.ready_at,
                                        message=message, pickup_spot=t.order.pickup_spot, at=utcnow()))

    async def watch(self, order: PlacedOrder) -> AsyncIterator[OrderUpdate]:
        t = self._tickets.get(order.order_id)
        if not t:
            return
        while True:
            upd: OrderUpdate = await t.events.get()
            yield upd
            if upd.status in (OrderStatus.PICKED_UP, OrderStatus.CANCELED, OrderStatus.REJECTED):
                return

    async def notify_arrival(self, order: PlacedOrder) -> None:
        t = self._tickets.get(order.order_id)
        if t and t.arrived_mono is None:
            t.arrived_mono = asyncio.get_running_loop().time()

    async def update_eta(self, order: PlacedOrder, eta: datetime) -> None:
        """The driver is running late / early: retime the kitchen (no-op once ready)."""
        t = self._tickets.get(order.order_id)
        if not t or t.status in (OrderStatus.READY, OrderStatus.PICKED_UP, OrderStatus.CANCELED):
            return
        t.ready_at = eta
        self._set(t, t.status, f"New pickup time {eta.astimezone().strftime('%-I:%M %p')}")

    async def cancel(self, order: PlacedOrder) -> bool:
        t = self._tickets.get(order.order_id)
        if not t or t.status in (OrderStatus.PICKED_UP,) or (t.status == OrderStatus.READY and t.kind not in (
                "fuel", "ev")):
            return False
        self._set(t, OrderStatus.CANCELED, "Canceled by customer")
        return True

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()


def _instructions(m: dict) -> str:
    modes = m.get("pickup_modes") or ["curbside"]
    spot = m.get("pickup_spot", "")
    if "pump" in modes:
        return f"Pull up to {spot or 'any pump'} — it's unlocked for your prepaid amount."
    if "charger" in modes:
        return f"Park at {spot or 'the reserved stall'} and plug in — the session starts automatically."
    if "curbside" in modes:
        return f"Pull into {spot or 'a curbside spot'} — they'll bring it to your window."
    if "in_store" in modes:
        return f"Pick up at the {spot.lower() or 'counter'} — show your code."
    return f"Pick up at the {spot.lower() or 'counter'} — your code is on the bag."
