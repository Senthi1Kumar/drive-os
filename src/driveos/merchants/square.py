"""Square merchant integration: Catalog (menus, prices, images), Orders (pickup), Payments (delayed capture).

Square only shows an API order to staff once it is paid, so this provider:
  * creates the order in `prepare_order` (before payment),
  * exposes `payment_rail` (Square Payments, autocomplete=false, linked by order_id),
  * declares `capture_before_accept` so the saga completes the payment right after placement.
Contracts: Square Catalog, Orders and Payments APIs (Square-Version 2026-09-16).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from ..config import SquareConfig
from ..core.http import Http
from ..core.ids import new_id, utcnow
from ..domain.errors import ConfigError, ItemUnavailable, MerchantRejected, ProviderUnavailable
from ..domain.models import (
    Cart,
    GeoPoint,
    Menu,
    MenuItem,
    ModifierGroup,
    ModifierOption,
    OrderStatus,
    OrderUpdate,
    Payer,
    PaymentAuthorization,
    Place,
    PlacedOrder,
    Quote,
    QuoteLine,
    Route,
)
from ..domain.money import Money
from ..nav.geo import haversine, project
from .base import describe_selections, normalise_selections

log = logging.getLogger("driveos.square")
QUOTE_TTL = timedelta(minutes=5)
_FULFILMENT = {"PROPOSED": OrderStatus.PLACED, "RESERVED": OrderStatus.PREPARING, "PREPARED": OrderStatus.READY,
               "COMPLETED": OrderStatus.PICKED_UP, "CANCELED": OrderStatus.CANCELED, "FAILED": OrderStatus.REJECTED}


class SquareApi:
    def __init__(self, cfg: SquareConfig, http: Http):
        if not cfg.access_token:
            raise ConfigError("Square access token not configured")
        self.cfg = cfg
        self.http = http
        self.base = ("https://connect.squareupsandbox.com" if cfg.environment == "sandbox"
                     else "https://connect.squareup.com")
        self._headers = {"Authorization": f"Bearer {cfg.access_token.get_secret_value()}",
                         "Square-Version": cfg.api_version, "Content-Type": "application/json"}

    async def call(self, method: str, path: str, body: dict | None = None, *, idempotent: bool | None = None) -> dict:
        r = await self.http.request(method, self.base + path, json=body, headers=self._headers,
                                    service="square", idempotent=idempotent)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            errs = data.get("errors") or [{}]
            code = errs[0].get("code", str(r.status_code))
            detail = errs[0].get("detail", "")
            if r.status_code in (401, 403):
                raise ConfigError(f"square auth: {code}")
            raise MerchantRejected(f"square {code}: {detail}")
        return data


class SquareMerchantProvider:
    source = "square"
    capture_before_accept = True

    def __init__(self, cfg: SquareConfig, http: Http):
        self.cfg = cfg
        self.api = SquareApi(cfg, http)
        self.payment_rail = SquarePayments(self.api, cfg)
        self._locations: dict[str, dict] = {}
        self._menus: dict[str, Menu] = {}
        self._orders: dict[str, dict] = {}

    # ---------------------------------------------------------------- locations
    async def _location(self, location_id: str) -> dict:
        if location_id not in self._locations:
            data = await self.api.call("GET", f"/v2/locations/{location_id}")
            self._locations[location_id] = data.get("location", {})
        return self._locations[location_id]

    async def search(self, *, near: GeoPoint, route: Route | None, category: str, query: str | None,
                     limit: int) -> list[Place]:
        out: list[Place] = []
        for lid in self.cfg.location_ids:
            try:
                loc = await self._location(lid)
            except ProviderUnavailable:
                continue
            c = loc.get("coordinates") or {}
            if "latitude" not in c:
                continue
            gp = GeoPoint(lat=c["latitude"], lon=c["longitude"])
            p = Place(id=f"square:{lid}", merchant_id=lid, provider=self.source,
                      name=loc.get("business_name") or loc.get("name", "Square merchant"), location=gp,
                      address=(loc.get("address") or {}).get("address_line_1", ""), category=category or "food",
                      logo_url=loc.get("logo_url"), photo_url=loc.get("full_format_logo_url"), prep_minutes=10)
            if route:
                pr = project(route, gp)
                if pr.offset_m > 1200:
                    continue
                p.distance_along_route_m = pr.along_m
                p.extra["offset_m"] = pr.offset_m
            elif haversine(near, gp) > 10000:
                continue
            out.append(p)
        return out[:limit * 3]

    # ---------------------------------------------------------------- menu
    async def get_menu(self, merchant_id: str) -> Menu:
        if merchant_id in self._menus:
            return self._menus[merchant_id]
        items_raw: list[dict] = []
        cursor = None
        while True:
            body: dict[str, Any] = {"enabled_location_ids": [merchant_id], "limit": 100}
            if cursor:
                body["cursor"] = cursor
            data = await self.api.call("POST", "/v2/catalog/search-catalog-items", body, idempotent=True)
            items_raw.extend(data.get("items", []))
            cursor = data.get("cursor")
            if not cursor or len(items_raw) >= 300:
                break
        related: dict[str, dict] = {}
        ids = [i["id"] for i in items_raw]
        for k in range(0, len(ids), 200):
            data = await self.api.call("POST", "/v2/catalog/batch-retrieve",
                                       {"object_ids": ids[k:k + 200], "include_related_objects": True},
                                       idempotent=True)
            for o in data.get("related_objects", []):
                related[o["id"]] = o
        menu = parse_square_menu(merchant_id, items_raw, related)
        self._menus[merchant_id] = menu
        return menu

    # ---------------------------------------------------------------- orders
    def _order_body(self, cart_lines: list[tuple[MenuItem, dict[str, list[str]], int]], location_id: str,
                    *, customer_name: str = "", pickup_eta: datetime | None = None) -> dict:
        line_items = []
        for item, sel, qty in cart_lines:
            variation = item.refs.get("variation:" + (sel.get("variation", [""])[0])) or item.refs.get("variation")
            mods = []
            for gid, opts in sel.items():
                if gid == "variation":
                    continue
                for oid in opts:
                    ref = item.refs.get(f"modifier:{oid}")
                    if ref:
                        mods.append({"catalog_object_id": ref})
            li: dict[str, Any] = {"catalog_object_id": variation, "quantity": str(qty)}
            if mods:
                li["modifiers"] = mods
            line_items.append(li)
        order: dict[str, Any] = {"location_id": location_id, "line_items": line_items}
        if customer_name or pickup_eta:
            at = pickup_eta or (utcnow() + timedelta(minutes=10))
            order["fulfillments"] = [{"type": "PICKUP", "pickup_details": {
                "recipient": {"display_name": customer_name or "DriveOS customer"},
                "schedule_type": "SCHEDULED", "pickup_at": at.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "note": "Ordered by voice from the car — curbside if available",
            }}]
        return order

    async def _cart_lines(self, cart: Cart) -> list[tuple[MenuItem, dict[str, list[str]], int]]:
        menu = await self.get_menu(cart.merchant_id)
        out = []
        for ln in cart.lines:
            item = menu.item(ln.item_id)
            if not item or not item.available:
                raise ItemUnavailable(ln.name, spoken=f"{ln.name} isn't available right now.")
            out.append((item, normalise_selections(item, ln.selections), ln.quantity))
        return out

    async def quote(self, cart: Cart, *, merchant_name: str = "") -> Quote:
        lines = await self._cart_lines(cart)
        data = await self.api.call("POST", "/v2/orders/calculate",
                                   {"order": self._order_body(lines, cart.merchant_id)}, idempotent=True)
        o = data.get("order", {})
        cur = o.get("total_money", {}).get("currency", "USD")
        total = Money(amount=int(o.get("total_money", {}).get("amount", 0)), currency=cur)
        tax = Money(amount=int(o.get("total_tax_money", {}).get("amount", 0)), currency=cur)
        qlines = []
        for (item, sel, qty), li in zip(lines, o.get("line_items", []), strict=False):
            qlines.append(QuoteLine(item_id=item.id, name=item.name, quantity=qty, selections=sel,
                                    description=describe_selections(item, sel),
                                    total=Money(amount=int(li.get("total_money", {}).get("amount", 0)) -
                                                int(li.get("total_tax_money", {}).get("amount", 0)), currency=cur)))
        sub = total - tax
        loc = await self._location(cart.merchant_id)
        return Quote(quote_id=new_id("q"), merchant_id=cart.merchant_id,
                     merchant_name=merchant_name or loc.get("business_name") or loc.get("name", ""),
                     provider=self.source, lines=qlines, subtotal=sub, tax=tax, fees=Money.zero(cur), total=total,
                     prep_minutes=10, expires_at=utcnow() + QUOTE_TTL,
                     provider_ref={"square_lines": [[i.id, s, q] for i, s, q in lines]})

    async def prepare_order(self, quote: Quote, *, customer_name: str, pickup_eta: datetime | None,
                            idempotency_key: str) -> dict:
        menu = await self.get_menu(quote.merchant_id)
        lines = [(menu.item(ln.item_id), ln.selections, ln.quantity) for ln in quote.lines]
        body = {"idempotency_key": idempotency_key,
                "order": self._order_body(lines, quote.merchant_id, customer_name=customer_name,
                                          pickup_eta=pickup_eta)}
        body["order"]["reference_id"] = idempotency_key[:40]
        data = await self.api.call("POST", "/v2/orders", body, idempotent=True)
        o = data.get("order", {})
        total = int(o.get("total_money", {}).get("amount", -1))
        if total != quote.total.amount:
            raise MerchantRejected(f"square order total {total} != quote {quote.total.amount}")
        self._orders[o["id"]] = o
        return {"square_order_id": o["id"], "location_id": quote.merchant_id}

    async def place_order(self, quote: Quote, *, payment: PaymentAuthorization, customer_name: str,
                          pickup_eta: datetime | None, idempotency_key: str) -> PlacedOrder:
        oid = quote.provider_ref.get("square_order_id") or payment.provider_ref.get("square_order_id")
        if not oid:
            raise MerchantRejected("square order was not prepared")
        if payment.status not in ("authorized", "captured"):
            raise MerchantRejected("payment not authorised")
        return PlacedOrder(order_id=oid, merchant_id=quote.merchant_id, provider=self.source,
                           pickup_code=oid[-3:].upper(), ready_at=utcnow() + timedelta(minutes=quote.prep_minutes),
                           pickup_instructions="Give your name and pickup code at the counter or curb.",
                           provider_ref={"location_id": quote.merchant_id})

    async def watch(self, order: PlacedOrder) -> AsyncIterator[OrderUpdate]:
        last = None
        while True:
            try:
                data = await self.api.call("GET", f"/v2/orders/{order.order_id}")
            except ProviderUnavailable:
                await asyncio.sleep(10)
                continue
            o = data.get("order", {})
            self._orders[order.order_id] = o
            f = (o.get("fulfillments") or [{}])[0]
            status = _FULFILMENT.get(f.get("state", "PROPOSED"), OrderStatus.PLACED)
            if o.get("state") == "CANCELED":
                status = OrderStatus.CANCELED
            if status != last:
                last = status
                yield OrderUpdate(order_id=order.order_id, status=status, at=utcnow(), message=f.get("state", ""))
                if status in (OrderStatus.PICKED_UP, OrderStatus.CANCELED, OrderStatus.REJECTED):
                    return
            await asyncio.sleep(8)

    async def notify_arrival(self, order: PlacedOrder) -> None:
        log.info("arrival for square order %s (staff notified via pickup note/ETA)", order.order_id)

    async def cancel(self, order: PlacedOrder) -> bool:
        o = self._orders.get(order.order_id)
        if not o:
            return False
        f = (o.get("fulfillments") or [{}])[0]
        if f.get("state") in ("PREPARED", "COMPLETED"):
            return False
        # UpdateOrder: mark the fulfillment canceled. Verify against your Square-Version before production use.
        body = {"idempotency_key": new_id("cx")[:40], "order": {
            "location_id": o.get("location_id"), "version": o.get("version"),
            "fulfillments": [{"uid": f.get("uid"), "state": "CANCELED"}]}}
        try:
            await self.api.call("PUT", f"/v2/orders/{order.order_id}", body, idempotent=True)
            return True
        except Exception as e:
            log.warning("square cancel failed: %s", e)
            return False

    async def aclose(self) -> None:
        pass


class SquarePayments:
    name = "square"

    def __init__(self, api: SquareApi, cfg: SquareConfig):
        self.api = api
        self.cfg = cfg

    def _status(self, p: dict) -> str:
        return {"APPROVED": "authorized", "COMPLETED": "captured", "CANCELED": "voided",
                "FAILED": "declined", "PENDING": "authorized"}.get(p.get("status", ""), "declined")

    def _auth(self, p: dict, amount: Money | None = None) -> PaymentAuthorization:
        am = p.get("amount_money") or {}
        return PaymentAuthorization(
            auth_id=p.get("id", ""), provider=self.name,
            amount=amount or Money(amount=int(am.get("amount", 0)), currency=am.get("currency", "USD")),
            status=self._status(p), provider_ref={"square_order_id": p.get("order_id"),
                                                  "receipt_url": p.get("receipt_url")},
            decline_reason="" if p.get("status") != "FAILED" else (p.get("card_details") or {}).get("status", "failed"))

    async def authorize(self, amount: Money, payer: Payer, *, idempotency_key: str, description: str,
                        metadata: dict[str, Any]) -> PaymentAuthorization:
        source = payer.token if payer.token.startswith(("ccof:", "cnon:")) else self.cfg.card_source_id
        body: dict[str, Any] = {
            "source_id": source, "idempotency_key": idempotency_key[:45],
            "amount_money": {"amount": amount.amount, "currency": amount.currency},
            "autocomplete": False, "note": description[:500],
            "reference_id": str(metadata.get("tx_id", ""))[:40],
        }
        if metadata.get("square_order_id"):
            body["order_id"] = metadata["square_order_id"]
            body["location_id"] = metadata.get("location_id")
        customer = payer.customer_ref or self.cfg.customer_id
        if source.startswith("ccof:") and customer:
            body["customer_id"] = customer
        try:
            data = await self.api.call("POST", "/v2/payments", body, idempotent=True)
        except MerchantRejected as e:
            return PaymentAuthorization(auth_id="", provider=self.name, amount=amount, status="declined",
                                        decline_reason=str(e))
        return self._auth(data.get("payment", {}), amount)

    async def capture(self, auth: PaymentAuthorization, *, idempotency_key: str,
                      amount: Money | None = None) -> PaymentAuthorization:
        data = await self.api.call("POST", f"/v2/payments/{auth.auth_id}/complete", {}, idempotent=True)
        return self._auth(data.get("payment", {}))

    async def void(self, auth: PaymentAuthorization, *, idempotency_key: str) -> PaymentAuthorization:
        data = await self.api.call("POST", f"/v2/payments/{auth.auth_id}/cancel", None, idempotent=True)
        return self._auth(data.get("payment", {}))

    async def refund(self, auth: PaymentAuthorization, *, idempotency_key: str,
                     amount: Money | None = None) -> PaymentAuthorization:
        amt = amount or auth.amount
        await self.api.call("POST", "/v2/refunds", {
            "idempotency_key": idempotency_key[:45], "payment_id": auth.auth_id,
            "amount_money": {"amount": amt.amount, "currency": amt.currency}, "reason": "Canceled from vehicle"},
            idempotent=True)
        return auth.model_copy(update={"status": "refunded"})

    async def aclose(self) -> None:
        pass


def parse_square_menu(location_id: str, items: list[dict], related: dict[str, dict]) -> Menu:
    """Square ITEM objects (+ related IMAGE / MODIFIER_LIST / CATEGORY) -> Menu."""
    out: list[MenuItem] = []
    currency = "USD"
    for obj in items:
        d = obj.get("item_data", {})
        variations = [v for v in d.get("variations", []) if v.get("item_variation_data", {}).get("price_money")]
        if not variations:
            continue
        prices = [int(v["item_variation_data"]["price_money"]["amount"]) for v in variations]
        currency = variations[0]["item_variation_data"]["price_money"].get("currency", currency)
        base = min(prices)
        refs: dict[str, str] = {"variation": variations[prices.index(base)]["id"]}
        groups: list[ModifierGroup] = []
        if len(variations) > 1:
            opts = []
            for v, p in zip(variations, prices, strict=False):
                vid = v["id"]
                refs[f"variation:{vid}"] = vid
                opts.append(ModifierOption(id=vid, name=v["item_variation_data"].get("name", "Regular"),
                                           price_delta=Money(amount=p - base, currency=currency),
                                           default=p == base,
                                           aliases=[v["item_variation_data"].get("name", "").lower()]))
            groups.append(ModifierGroup(id="variation", name="Size", min_select=1, max_select=1, options=opts,
                                        aliases=["size"]))
        for info in d.get("modifier_list_info", []):
            if info.get("enabled") is False:
                continue
            ml = related.get(info.get("modifier_list_id", ""), {})
            mld = ml.get("modifier_list_data", {})
            mopts = []
            for mo in mld.get("modifiers", []):
                md = mo.get("modifier_data", {})
                refs[f"modifier:{mo['id']}"] = mo["id"]
                pm = md.get("price_money") or {"amount": 0, "currency": currency}
                mopts.append(ModifierOption(id=mo["id"], name=md.get("name", ""),
                                            price_delta=Money(amount=int(pm.get("amount", 0)),
                                                              currency=pm.get("currency", currency)),
                                            default=bool(md.get("on_by_default")),
                                            aliases=[md.get("name", "").lower()]))
            if mopts:
                mx = info.get("max_selected_modifiers", mld.get("max_selected_modifiers", -1))
                mn = info.get("min_selected_modifiers", mld.get("min_selected_modifiers", -1))
                groups.append(ModifierGroup(id=ml["id"], name=mld.get("name", "Options"),
                                            min_select=max(0, mn if mn is not None else 0),
                                            max_select=mx if mx and mx > 0 else len(mopts), options=mopts,
                                            aliases=[mld.get("name", "").lower()]))
        image_url = None
        for iid in d.get("image_ids") or []:
            img = related.get(iid, {})
            if img.get("image_data", {}).get("url"):
                image_url = img["image_data"]["url"]
                break
        cat_name = ""
        for c in d.get("categories") or []:
            cat = related.get(c.get("id", ""), {})
            cat_name = cat.get("category_data", {}).get("name", "") or cat_name
        name = d.get("name", "Item")
        out.append(MenuItem(id=obj["id"], name=name, description=d.get("description_plaintext", "") or "",
                            category=cat_name, base_price=Money(amount=base, currency=currency), image_url=image_url,
                            modifier_groups=groups, aliases=[name.lower()], refs=refs))
    cats: list[str] = []
    for i in out:
        if i.category and i.category not in cats:
            cats.append(i.category)
    return Menu(merchant_id=location_id, currency=currency, categories=cats, items=out)
