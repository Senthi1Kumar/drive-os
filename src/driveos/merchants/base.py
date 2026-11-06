"""Merchant provider contract. Every source of orderable merchants implements this."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.models import (
    Cart,
    CartLine,
    GeoPoint,
    Menu,
    MenuItem,
    OrderUpdate,
    PaymentAuthorization,
    Place,
    PlacedOrder,
    Quote,
    Route,
)
from ..domain.money import Money


@runtime_checkable
class MerchantProvider(Protocol):
    source: str
    # True when the merchant's own platform requires payment capture before it will show the order to staff
    capture_before_accept: bool

    async def search(
        self, *, near: GeoPoint, route: Route | None, category: str, query: str | None, limit: int
    ) -> list[Place]: ...

    async def get_menu(self, merchant_id: str) -> Menu: ...

    async def quote(self, cart: Cart, *, merchant_name: str = "") -> Quote: ...

    async def place_order(
        self,
        quote: Quote,
        *,
        payment: PaymentAuthorization,
        customer_name: str,
        pickup_eta: datetime | None,
        idempotency_key: str,
    ) -> PlacedOrder: ...

    def watch(self, order: PlacedOrder) -> AsyncIterator[OrderUpdate]: ...

    async def notify_arrival(self, order: PlacedOrder) -> None: ...

    async def cancel(self, order: PlacedOrder) -> bool: ...

    async def aclose(self) -> None: ...


def price_line(item: MenuItem, selections: dict[str, list[str]], quantity: int = 1) -> Money:
    unit = item.base_price
    for gid, opts in selections.items():
        g = item.group(gid)
        if not g:
            continue
        for oid in opts:
            o = g.option(oid)
            if o:
                unit = unit + o.price_delta
    return unit


def normalise_selections(item: MenuItem, selections: dict[str, list[str]]) -> dict[str, list[str]]:
    """Fill defaults, drop unknown options, enforce max_select (last mention wins)."""
    out: dict[str, list[str]] = {}
    for g in item.modifier_groups:
        chosen = [o for o in selections.get(g.id, []) if g.option(o)]
        if not chosen:
            chosen = g.default_ids()
        if g.max_select and len(chosen) > g.max_select:
            chosen = chosen[-g.max_select:]
        if chosen:
            out[g.id] = chosen
    return out


def describe_selections(item: MenuItem, selections: dict[str, list[str]], *, skip_defaults: bool = False) -> str:
    parts: list[str] = []
    for g in item.modifier_groups:
        for oid in selections.get(g.id, []):
            o = g.option(oid)
            if o and not (skip_defaults and o.default):
                parts.append(o.name if g.id != "sweet" or oid != "none" else "no sugar")
    return " · ".join(parts)


def make_line(line_id: str, item: MenuItem, selections: dict[str, list[str]], quantity: int = 1) -> CartLine:
    sel = normalise_selections(item, selections)
    return CartLine(line_id=line_id, item_id=item.id, name=item.name, quantity=quantity, selections=sel,
                    unit_price=price_line(item, sel))
