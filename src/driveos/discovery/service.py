"""Nearby discovery: what can be bought or reached close to the car, and what each stop costs in time.

Parked or browsing: places are ranked by straight-line distance from the car. While a route is active the
same search is scored by detour (extra time to stop there and continue), so "find coffee on the way"
prefers places along the drive.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..config import Settings
from ..domain.categories import CATEGORIES
from ..domain.errors import DriveOSError
from ..domain.models import GeoPoint, PickupMode, Place, Route
from ..merchants.registry import MerchantRegistry
from ..nav.geo import haversine, project, with_cumulative
from ..nav.router import Router

log = logging.getLogger("driveos.discovery")

# time spent at the stop itself, by pickup mode (s)
DWELL_S = {PickupMode.CURBSIDE: 180, PickupMode.DRIVE_THRU: 240, PickupMode.WINDOW: 180, PickupMode.WALK_IN: 300,
           PickupMode.PUMP: 360, PickupMode.CHARGER: 900, PickupMode.IN_STORE: 300}
CITY_SPEED_MPS = 11.0


@dataclass
class DiscoveryResult:
    places: list[Place]
    checked: int
    along_route: bool


class DiscoveryService:
    def __init__(self, settings: Settings, registry: MerchantRegistry, router: Router):
        self.settings = settings
        self.registry = registry
        self.router = router

    async def nearby(self, *, category: str, query: str | None, position: GeoPoint, route: Route | None = None,
                     along_m: float = 0.0, limit: int | None = None) -> DiscoveryResult:
        limit = limit or self.settings.merchants.max_results
        remaining = _remaining(route, along_m) if route else None
        candidates = await self.registry.search(near=position, route=remaining, category=category, query=query,
                                                limit=limit * 2)
        out: list[Place] = []
        for p in candidates:
            if p.open_now is False:
                continue
            d = haversine(position, p.location)
            p.extra["distance_m"] = d
            p.extra.setdefault("orderable", True)
            p.extra.setdefault("has_menu", True)
            p.extra["category"] = category
            if remaining is not None:
                pr = project(remaining, p.location)
                p.distance_along_route_m = pr.along_m
                p.extra["offset_m"] = pr.offset_m
            # quick estimate; refined below for the shortlist
            p.detour_s = _estimate_detour(p, d, remaining)
            out.append(p)

        def rank(p: Place):
            item_hit = 0 if (query and p.extra.get("matched_item")) else 1
            menu_rank = 0 if p.extra.get("has_menu", True) else 1
            key = p.detour_s if remaining is not None else p.extra["distance_m"]
            return (menu_rank, item_hit, key)

        out.sort(key=rank)
        out = out[:limit]
        if remaining is not None and self.router is not None:
            await self._refine_detours(out, position, remaining)
            out.sort(key=rank)
        return DiscoveryResult(places=out, checked=len(candidates), along_route=remaining is not None)

    async def _refine_detours(self, places: list[Place], position: GeoPoint, remaining: Route) -> None:
        dest = remaining.geometry[-1]
        base_s = remaining.duration_s

        async def one(p: Place) -> None:
            try:
                r = await asyncio.wait_for(self.router.route(position, dest, via=[p.location]), 6.0)
                p.detour_s = max(0.0, r.duration_s - base_s) + _dwell(p)
            except (DriveOSError, TimeoutError):
                pass
            except Exception:
                log.exception("detour estimate failed for %s", p.name)

        await asyncio.gather(*(one(p) for p in places))


def _dwell(p: Place) -> float:
    return float(DWELL_S.get(p.pickup_modes[0], 240)) if p.pickup_modes else 240.0


def _estimate_detour(p: Place, distance_m: float, remaining: Route | None) -> float:
    if remaining is None:
        return distance_m * 1.3 / CITY_SPEED_MPS
    off = p.extra.get("offset_m", distance_m)
    return 2 * off * 1.3 / CITY_SPEED_MPS + _dwell(p)


def _remaining(route: Route, along_m: float) -> Route:
    r = with_cumulative(route)
    i = 0
    while i + 1 < len(r.cumulative_m) and r.cumulative_m[i + 1] <= along_m:
        i += 1
    geom = r.geometry[i:] if len(r.geometry) - i >= 2 else r.geometry[-2:]
    frac = 1 - (along_m / r.cumulative_m[-1]) if r.cumulative_m[-1] else 1.0
    return with_cumulative(Route(geometry=geom, distance_m=r.distance_m * frac, duration_s=r.duration_s * frac,
                                 steps=[], destination_name=r.destination_name))


def noun(category: str, n: int) -> str:
    c = CATEGORIES.get(category)
    if not c:
        return "place" if n == 1 else "places"
    return c.one if n == 1 else c.many
