"""Live points of interest from Mapbox Search Box: gas stations, bars, chargers, stores, cafés.

These are real places near the car, used for discovery and navigation. They are *not* orderable — no
ordering backend stands behind a map POI — so the Shop screen shows them with "Drive" only, and the
assistant says so. A merchant that also exists in an ordering source (local catalogue, Square, MCP)
is de-duplicated in favour of the orderable record.

Category ids are resolved against Mapbox's live category list once per session, because canonical ids
are only guaranteed by that endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from ..domain.categories import CATEGORIES
from ..domain.errors import OrderingUnavailable, ProviderUnavailable
from ..domain.models import Cart, GeoPoint, Menu, PickupMode, Place, Route
from ..nav.geo import haversine, point_at, with_cumulative
from ..nav.mapbox import API, MapboxClient

log = logging.getLogger("driveos.pois")

# Search Box canonical category ids (checked against GET /search/searchbox/v1/list/category)
_CANDIDATES = {
    "coffee": ["coffee", "cafe", "coffee_shop"], "food": ["restaurant", "fast_food"],
    "sandwich": ["sandwich_shop", "deli", "fast_food"], "bakery": ["bakery"], "bar": ["bar", "pub", "brewery"],
    "gas": ["gas_station"], "ev": ["charging_station"],
    "store": ["grocery", "convenience_store", "supermarket"], "pharmacy": ["pharmacy"],
    "hotel": ["hotel", "lodging"],
}
CACHE_GRID_DEG = 0.005    # results are shared by positions within ~500 m

# Mapbox files some companies and offices under shop categories. A name that reads like a business entity is dropped
# unless it also carries a retail word or the feature is a branded chain that really carries the searched category.
_NONRETAIL = re.compile(
    r"\b(technolog(?:y|ies)|corporation|corp|consulting|holdings|capital|partners|labs?|laborator(?:y|ies)|software|"
    r"ventures|enterprises|solutions|incorporated|inc|llc|l\.l\.c|ltd)\b\.?", re.I)
_RETAIL = re.compile(
    r"\b(pharmac\w*|drug\w*|rx|chemist|apothecary|health|wellness|market|markets|mart|store|shop|grocer\w*|foods?|"
    r"deli|cafe|caf\u00e9|coffee|tea|bakery|bake\w*|bar|pub|brew\w*|tavern|grill|kitchen|restaurant|bistro|diner|"
    r"pizza\w*|sushi|taqueria|station|gas|fuel|oil|charging|chargers?|supercharger|hotel|inn|suites|lodge|motel|"
    r"resort|boba|donut|bagel|sandwich\w*|supermarket|convenience|liquor|wine|spirits|cellar)\b", re.I)
DUP_RADIUS_M = 50.0
BACKFILL = 6              # candidates fetched beyond the limit so dropped results can be replaced


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def is_junk(name: str, categories: list[str], brand: str | None, searched: list[str]) -> bool:
    """A non-retail business name (an office or company) that Mapbox filed under a shop category."""
    if not _NONRETAIL.search(name) or _RETAIL.search(name):
        return False
    return not (brand and any(c in categories for c in searched))


def clean_features(feats: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    """Drop non-retail names and repeats (same name within ``DUP_RADIUS_M``), keeping the first of each."""
    searched = _CANDIDATES.get(category, [category])
    out: list[dict[str, Any]] = []
    for f in feats:
        name = f.get("name") or ""
        if not name or is_junk(name, f.get("categories") or [], f.get("brand"), searched):
            continue
        key = _norm(name)
        if any(_norm(g["name"]) == key and haversine(g["location"], f["location"]) <= DUP_RADIUS_M for g in out):
            continue
        out.append(f)
    return out


class MapboxPoiProvider:
    source = "mapbox"
    capture_before_accept = False

    SAMPLES = 4              # fallback: search boxes spaced along the route
    SAMPLE_RADIUS_M = 3000

    def __init__(self, client: MapboxClient, *, radius_m: float = 8000, deviation_min: int = 6, catalog=None,
                 sample_menus: bool = False, cache_ttl_s: float = 600.0, cache_max: int = 64,
                 clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.radius_m = radius_m
        self.deviation_min = deviation_min
        self.catalog = catalog
        self.sample_menus = sample_menus and catalog is not None
        self.cache_ttl_s = cache_ttl_s
        self.cache_max = cache_max
        self._clock = clock
        self._known: set[str] | None = None
        # In memory only and short-lived: Search Box results are not stored on disk or kept across restarts.
        self._cache: dict[tuple[str, int, int, int], tuple[float, list[dict[str, Any]]]] = {}
        self.requests = 0

    async def _ids(self, category: str) -> list[str]:
        if self._known is None:
            try:
                r = await self.client.http.request(
                    "GET", f"{API}/search/searchbox/v1/list/category",
                    params={"access_token": self.client._token, "language": self.client.cfg.language},
                    service="mapbox.categories")
                self._known = {i.get("canonical_id", "") for i in r.json().get("listItems", [])} if \
                    r.status_code == 200 else set()
            except ProviderUnavailable:
                self._known = set()
        cands = _CANDIDATES.get(category, CATEGORIES[category].mapbox if category in CATEGORIES else [category])
        hits = [c for c in cands if not self._known or c in self._known]
        return hits[:2] or cands[:1]

    async def search(self, *, near: GeoPoint, route: Route | None, category: str, query: str | None,
                     limit: int) -> list[Place]:
        if category in ("", "any"):
            return []
        feats: list[dict[str, Any]] = []
        for cid in await self._ids(category):
            if route is not None and len(route.geometry) >= 2:
                feats.extend(await self._along(cid, route, near, limit))
            else:
                feats.extend(await self._near_cached(cid, near, limit))
            if len(clean_features(feats, category)) >= limit:
                break
        cat = CATEGORIES.get(category)
        out: list[Place] = []
        seen: set[str] = set()
        for f in clean_features(feats, category):
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            out.append(self._place(f, category, cat, near))
        out.sort(key=lambda p: p.extra["distance_m"])
        return out

    def _place(self, f: dict[str, Any], category: str, cat, near: GeoPoint) -> Place:
        d = haversine(near, f["location"])
        modes = [PickupMode(cat.pickup)] if cat else [PickupMode.WALK_IN]
        extra = {"tags": [category], "orderable": False, "has_menu": False, "source": "mapbox", "sample_menu": False,
                 "brand": f.get("brand") or "", "kind": category, "hours": "", "distance_m": d,
                 "poi_categories": list(f.get("categories") or [])}
        if self.sample_menus:
            from .sample_menus import merchant_record
            rec = merchant_record(f, category)
            if rec is not None:
                self.catalog.add_external(rec)
                p = self.catalog.place(rec, provider="local")
                p.extra.update(distance_m=d, orderable=True, has_menu=True, source="mapbox", sample_menu=True,
                               poi_categories=list(f.get("categories") or []))
                p.open_now = None
                p.pickup_modes = [PickupMode(x) for x in rec["pickup_modes"]]
                return p
        return Place(id=f"mapbox:{f['id']}", merchant_id=f["id"], provider=self.source, name=f["name"],
                     location=f["location"], address=f["address"] or "", category=category, rating=f.get("rating"),
                     open_now=None, pickup_modes=modes, extra=extra)

    async def _near_cached(self, cid: str, near: GeoPoint, limit: int) -> list[dict[str, Any]]:
        key = (cid, round(near.lat / CACHE_GRID_DEG), round(near.lon / CACHE_GRID_DEG), int(limit))
        hit = self._cache.get(key)
        now = self._clock()
        if hit and now - hit[0] < self.cache_ttl_s:
            return hit[1]
        try:
            self.requests += 1
            feats = await self.client.category_near(cid, proximity=near, limit=min(25, limit + BACKFILL),
                                                    bbox_m=self.radius_m)
        except ProviderUnavailable as e:
            log.warning("mapbox category %s failed: %s", cid, e)
            return hit[1] if hit else []     # stale beats nothing
        self._cache[key] = (now, feats)
        if len(self._cache) > self.cache_max:
            for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1][0])[: len(self._cache) - self.cache_max]:
                self._cache.pop(k, None)
        return feats

    async def _along(self, cid: str, route: Route, near: GeoPoint, limit: int) -> list[dict[str, Any]]:
        """Search Box category search with the route as context (sar_type=isochrone, time_deviation); when that is
        refused or empty, boxes sampled along the route."""
        try:
            feats = await self.client.category_along_route(cid, route, origin=near, limit=min(25, limit + BACKFILL),
                                                           time_deviation_min=self.deviation_min)
            if feats:
                return feats
        except ProviderUnavailable as e:
            log.warning("mapbox search along route %s failed: %s", cid, e)
        r = with_cumulative(route)
        total = r.cumulative_m[-1]
        pts = [point_at(r, total * (i + 0.5) / self.SAMPLES)[0] for i in range(self.SAMPLES)]

        async def one(p: GeoPoint) -> list[dict[str, Any]]:
            try:
                return await self.client.category_near(cid, proximity=p, limit=limit, bbox_m=self.SAMPLE_RADIUS_M)
            except ProviderUnavailable as e:
                log.warning("mapbox category %s sample failed: %s", cid, e)
                return []

        return [f for batch in await asyncio.gather(*(one(p) for p in pts)) for f in batch]

    async def get_menu(self, merchant_id: str) -> Menu:
        raise OrderingUnavailable(merchant_id)

    async def quote(self, cart: Cart, *, merchant_name: str = ""):
        raise OrderingUnavailable(cart.merchant_id)

    async def place_order(self, *a, **k):
        raise OrderingUnavailable("mapbox")

    async def watch(self, order):  # pragma: no cover - never placed
        if False:
            yield None

    async def notify_arrival(self, order) -> None:
        return None

    async def cancel(self, order) -> bool:
        return False

    async def aclose(self) -> None:
        return None
