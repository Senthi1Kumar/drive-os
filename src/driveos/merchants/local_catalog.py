"""Merchant catalogue: real restaurant menus from the DriveOS data set plus demo venues.

Sources, all normalised to one merchant record:

* ``places.json`` / ``menus.json`` — 11 Los Gatos restaurants with full menus and photos (DriveOS fixtures).
* ``dataset.json`` — 319 restaurants with menus across 20 US cities (DriveOS sample catalogue).
* demo venues (``venues.py``) — fuel, EV charging, convenience, pharmacy and a taproom, generated around
  the car so every kind of errand can be completed in demo mode.

Coordinates: the catalogue only carries addresses and a distance from the city search point. Records are
placed approximately (``approx=True``) until a geocoder (Mapbox) resolves the address; resolved
coordinates are cached on disk so each address is geocoded once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Awaitable, Callable
from importlib import resources
from pathlib import Path
from typing import Any

from ..domain.categories import classify
from ..domain.models import GeoPoint, Menu, MenuItem, ModifierGroup, ModifierOption, PickupMode, Place
from ..domain.money import Money
from ..nav.geo import haversine
from . import venues

log = logging.getLogger("driveos.catalog")

MI = 1609.344
# Los Gatos fixtures: hand-placed approximations of the street addresses (refined by geocoding)
_LOS_GATOS = {
    "r1-shangri-la-chinese-restaurant": (37.23630, -121.96360),
    "r2-purple-onion-cafe": (37.22230, -121.98110),
    "r3-plur-coffee": (37.25250, -121.94560),
    "r4-heritage-pub-&-chop-house": (37.22300, -121.98260),
    "r5-plated-by-chef-kiah": (37.27080, -121.95680),
    "r6-kenz-coffee-bar": (37.24200, -121.96200),
    "r7-crust-sourdough-deli-los-gatos": (37.26700, -121.97900),
    "r10-the-posh-bagel": (37.27020, -121.93450),
    "r11-orange-bowl": (37.27350, -121.94800),
}
LOS_GATOS_CENTER = GeoPoint(lat=37.2358, lon=-121.9746)
_TAX = {"CA": "0.0925", "NY": "0.08875", "IL": "0.1025", "WA": "0.1025", "TX": "0.0825", "MA": "0.0625",
        "PA": "0.08", "DC": "0.10", "NC": "0.0725", "GA": "0.089", "FL": "0.07", "MI": "0.06", "MN": "0.0803",
        "TN": "0.0975", "LA": "0.0945", "CO": "0.0881", "AZ": "0.086", "NV": "0.0838"}


def _read(name: str) -> Any:
    return json.loads(resources.files("driveos.data.catalog").joinpath(name).read_text())


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def _offset(center: GeoPoint, miles: float, bearing_deg: float) -> GeoPoint:
    d = max(miles, 0.05) * MI
    b = math.radians(bearing_deg)
    dlat = d * math.cos(b) / 111_320.0
    dlon = d * math.sin(b) / (111_320.0 * math.cos(math.radians(center.lat)))
    return GeoPoint(lat=center.lat + dlat, lon=center.lon + dlon)


def _bearing_for(key: str) -> float:
    return int(hashlib.sha1(key.encode()).hexdigest()[:6], 16) % 360


def _hours(h: Any) -> str:
    if isinstance(h, str):
        return h
    if isinstance(h, dict) and h.get("state", "").endswith("ALWAYS_AVAILABLE"):
        return "Open now"
    return ""


def _item(it: dict[str, Any], category: str, images: dict[str, str]) -> dict[str, Any]:
    """Fixture item (camelCase) or crawl item (snake_case) -> catalogue item."""
    iid = it.get("id") or it.get("item_id")
    price = it.get("priceCents", it.get("price_cents")) or 0
    cur = it.get("currency") or "USD"
    variations = it.get("all_variations") or []
    groups: list[dict[str, Any]] = []
    if len(variations) > 1:
        base = min(int(v.get("price_cents") or 0) for v in variations)
        price = base
        groups.append({
            "id": "variation", "name": "Option", "min_select": 1, "max_select": 1, "aliases": ["size", "option"],
            "options": [{"id": v["id"], "name": _clean(v.get("name") or "Regular"),
                         "price_delta": {"amount": int(v.get("price_cents") or 0) - base, "currency": cur},
                         "default": int(v.get("price_cents") or 0) == base and i == next(
                             j for j, w in enumerate(variations) if int(w.get("price_cents") or 0) == base),
                         "aliases": [_clean(v.get("name") or "").lower()]}
                        for i, v in enumerate(variations)],
        })
    img = it.get("imageUrl") or it.get("image_url") or images.get(iid, "")
    return {
        "id": iid, "name": _clean(it.get("name") or "Item"), "description": (it.get("description") or "").strip(),
        "category": category, "base_price": {"amount": int(price), "currency": cur}, "image_url": img or None,
        "modifier_groups": groups, "aliases": [], "available": not it.get("requires_modifiers", False),
        "refs": {"variation": it.get("variationId") or it.get("variation_id") or ""},
    }


def _clean(s: str) -> str:
    s = re.sub(r"\*+", "", s).strip()
    return re.sub(r"\s{2,}", " ", s)


def _cat_name(s: str | None) -> str:
    s = _clean(s or "Menu")
    return s.title() if s.isupper() or s.islower() else s


class LocalCatalog:
    """All merchants the in-process provider can show; positions refined by an optional geocoder."""

    def __init__(self, cache_dir: Path | None = None, *, include_dataset: bool = True):
        self._cache_path = (cache_dir / "geocode_cache.json") if cache_dir else None
        self._geo_cache: dict[str, list[float]] = {}
        if self._cache_path and self._cache_path.is_file():
            try:
                self._geo_cache = json.loads(self._cache_path.read_text())
            except Exception:
                self._geo_cache = {}
        self.merchants: dict[str, dict[str, Any]] = {}
        self._menus: dict[str, Menu] = {}
        self._load_fixtures()
        if include_dataset:
            self._load_dataset()
        self._venue_anchor: GeoPoint | None = None

    # ------------------------------------------------------------------ loading
    def _add(self, m: dict[str, Any]) -> None:
        key = m["address_key"]
        if key in self._geo_cache:
            lat, lon = self._geo_cache[key]
            m["location"], m["approx"] = {"lat": lat, "lon": lon}, False
        self.merchants[m["id"]] = m

    def _load_fixtures(self) -> None:
        places = _read("places.json")["places"][0]["restaurants"]
        menus = _read("menus.json")
        covers = {c["brand"]: c["imageUrl"] for c in _read("covers.json").get("covers", [])}
        images = {i["itemId"]: i["imageUrl"] for i in _read("menu_images.json").get("items", [])}
        seen_addr: set[str] = set()
        for r in places:
            key = r.get("menuKey") or _slug(r["name"])
            menu = menus.get(key) or {}
            addr = menu.get("address") or f"{r['address']}, Los Gatos, CA"
            brand = _clean(r["name"])
            # the crawl lists the same deli three times (one per register) — keep one per address+brand stem
            stem = " ".join(brand.lower().split()[:2])
            if (stem, r["address"]) in seen_addr:
                continue
            seen_addr.add((stem, r["address"]))
            cats = [{"name": _cat_name(c.get("name")), "items": [_item(i, _cat_name(c.get("name")), images)
                                                               for i in c.get("items", [])]}
                    for c in menu.get("categories", [])]
            cats = [c for c in cats if c["items"]]
            lat, lon = _LOS_GATOS.get(key, (None, None))
            loc = (GeoPoint(lat=lat, lon=lon) if lat else _offset(LOS_GATOS_CENTER, r.get("miles") or 1, _bearing_for(key)))
            cover = covers.get(brand) or next((i["image_url"] for c in cats for i in c["items"] if i["image_url"]), "")
            self._add({
                "id": key, "name": brand.title() if brand.isupper() else brand.split(" (")[0],
                "address": addr, "address_key": addr.lower(), "city": "Los Gatos", "state": "CA",
                "location": {"lat": loc.lat, "lon": loc.lon}, "approx": True,
                "hours": _hours(r.get("hours") or menu.get("hours")), "description": r.get("description") or "",
                "cover": cover, "tags": classify(brand, [c["name"] for c in cats]), "categories": cats,
                "tax_rate": _TAX["CA"], "prep_minutes": 8, "source": "catalog",
                "unit_token": menu.get("unitToken", ""),
            })

    def _load_dataset(self) -> None:
        try:
            d = _read("dataset.json")
        except FileNotFoundError:
            log.info("extended restaurant dataset not installed; using the bundled demo catalogue")
            return
        cities = {c["id"]: c for c in d["cities"]}
        for r in d["restaurants"]:
            menu = d["menus"].get(r["digest"])
            if not menu:
                continue
            cats = [{"name": _cat_name(c.get("name")), "items": [_item(i, _cat_name(c.get("name")), {})
                                                               for i in c.get("items", [])]}
                    for c in menu.get("categories", [])]
            cats = [c for c in cats if c["items"]]
            if not cats:
                continue
            city = cities.get(r.get("city_id"), {})
            center = GeoPoint(lat=city.get("lat", 0.0), lon=city.get("lng", 0.0))
            loc = _offset(center, r.get("miles") or 1.0, _bearing_for(r["digest"]))
            addr = f"{r['address']}, {r.get('city', '')}, {r.get('state', '')}".strip(", ")
            name = _clean(r["name"])
            self._add({
                "id": f"cx-{r['digest'][:12]}", "name": name, "address": addr, "address_key": addr.lower(),
                "city": r.get("city", ""), "state": r.get("state", ""),
                "location": {"lat": loc.lat, "lon": loc.lon}, "approx": True,
                "hours": r.get("hours") or "", "description": r.get("description") or "",
                "cover": r.get("cover_image") or "", "tags": classify(name, [c["name"] for c in cats]),
                "categories": cats, "tax_rate": _TAX.get(r.get("state", ""), "0.08"), "prep_minutes": 10,
                "source": "catalog", "unit_token": r.get("unit_token", ""),
            })

    # ------------------------------------------------------------------ venues follow the car in demo mode
    def ensure_venues(self, anchor: GeoPoint) -> None:
        if self._venue_anchor and haversine(self._venue_anchor, anchor) < 15_000:
            return
        self._venue_anchor = anchor
        for k in [k for k, m in self.merchants.items() if m["source"] == "venue"]:
            self.merchants.pop(k)
            self._menus.pop(k, None)
        extras = {cat for cat in ("coffee", "sandwich")
                  if not any(m["source"] != "venue" and cat in m["tags"] for m in self.near(anchor, 12_000))}
        for v in venues.build(anchor, extras):
            v["address_key"] = v["address"].lower()
            self.merchants[v["id"]] = v

    # ------------------------------------------------------------------ map places with sample menus
    EXTERNAL_CAP = 400

    def add_external(self, record: dict[str, Any]) -> None:
        """Register a map place (Mapbox) with its sample menu so the normal menu / quote / order path serves it.
        Oldest external places are dropped past ``EXTERNAL_CAP``."""
        mid = record["id"]
        self.merchants.pop(mid, None)       # re-insert at the end: most recent stays
        self._menus.pop(mid, None)
        self.merchants[mid] = record
        ext = [k for k, m in self.merchants.items() if m["source"] == "mapbox"]
        for k in ext[: max(0, len(ext) - self.EXTERNAL_CAP)]:
            self.merchants.pop(k, None)
            self._menus.pop(k, None)

    # ------------------------------------------------------------------ queries
    def near(self, center: GeoPoint, radius_m: float) -> list[dict[str, Any]]:
        out = []
        for m in self.merchants.values():
            loc = GeoPoint(**m["location"])
            d = haversine(center, loc)
            if d <= radius_m:
                out.append({**m, "distance_m": d})
        out.sort(key=lambda m: m["distance_m"])
        return out

    def merchant(self, mid: str) -> dict[str, Any]:
        return self.merchants[mid]

    def menu(self, mid: str) -> Menu:
        if mid not in self._menus:
            m = self.merchants[mid]
            items = [MenuItem.model_validate(i) for c in m["categories"] for i in c["items"]]
            self._menus[mid] = Menu(merchant_id=mid, currency=items[0].base_price.currency if items else "USD",
                                    categories=[c["name"] for c in m["categories"]], items=items)
        return self._menus[mid]

    def place(self, m: dict[str, Any], *, provider: str) -> Place:
        modes = m.get("pickup_modes") or ["curbside"]
        return Place(
            id=f"{provider}:{m['id']}", merchant_id=m["id"], provider=provider, name=m["name"],
            location=GeoPoint(**m["location"]), address=m["address"], category=m["tags"][0],
            cuisine=m.get("cuisine", ""), rating=m.get("rating"), photo_url=m.get("cover") or None,
            open_now=True, pickup_modes=[PickupMode(x) for x in modes], prep_minutes=int(m.get("prep_minutes", 8)),
            extra={"tags": m["tags"], "hours": m.get("hours", ""), "approx": m.get("approx", False),
                   "source": m["source"], "pickup_spot": m.get("pickup_spot", ""), "tax_rate": m["tax_rate"],
                   "orderable": True, "description": m.get("description", ""), "kind": m.get("kind", "restaurant"),
                   "sample_menu": bool(m.get("sample")), "sample_kind": m.get("sample_kind", ""),
                   "brand": m.get("brand", "")},
        )

    # ------------------------------------------------------------------ geocoding
    async def refine(self, ids: list[str], geocode: Callable[[str], Awaitable[GeoPoint | None]]) -> int:
        """Replace approximate positions with geocoded ones (cached). Returns how many were resolved."""
        n = 0
        for mid in ids:
            m = self.merchants.get(mid)
            if not m or not m.get("approx") or m["source"] == "venue":
                continue
            try:
                p = await geocode(m["address"])
            except Exception as e:  # network trouble: keep the approximation
                log.info("geocode failed for %s: %s", m["name"], e)
                continue
            if p is None:
                continue
            m["location"], m["approx"] = {"lat": p.lat, "lon": p.lon}, False
            self._geo_cache[m["address_key"]] = [p.lat, p.lon]
            n += 1
        if n and self._cache_path:
            try:
                self._cache_path.write_text(json.dumps(self._geo_cache))
            except OSError:
                pass
        return n


def money(amount: int, currency: str = "USD") -> Money:
    return Money(amount=amount, currency=currency)


__all__ = ["LocalCatalog", "LOS_GATOS_CENTER", "ModifierGroup", "ModifierOption"]
