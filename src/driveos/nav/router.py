"""Routing front-end: Mapbox Directions when a token is configured, an offline estimator otherwise.

The offline router has no street data. It draws a plausible two-leg path (along the larger axis first,
then the smaller), estimates distance with a road-network factor and duration at urban speeds, and emits
depart / turn / arrive steps so guidance, ETA and arrival detection all work in demo mode or when the
car is out of coverage. Screens label offline routes as estimates.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..domain.errors import ProviderUnavailable
from ..domain.models import GeoPoint, Maneuver, Route, RouteStep, VoiceCue
from .geo import bearing, cumulative, densify, haversine, with_cumulative
from .guidance import maneuver_icon, route_polyline6
from .mapbox import MapboxClient

log = logging.getLogger("driveos.router")


class Router(Protocol):
    name: str

    async def route(self, origin: GeoPoint, destination: GeoPoint, *, via: list[GeoPoint] | None = None,
                    destination_name: str = "", via_names: list[str] | None = None,
                    fresh: bool = False) -> Route: ...


_COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]


def _compass(deg: float) -> str:
    return _COMPASS[int(((deg % 360) + 22.5) // 45) % 8]


def _turn(b_in: float, b_out: float) -> str:
    d = (b_out - b_in + 540) % 360 - 180
    if abs(d) < 25:
        return "straight"
    if abs(d) > 155:
        return "uturn"
    side = "right" if d > 0 else "left"
    return side if abs(d) > 60 else f"slight {side}"


class OfflineRouter:
    name = "offline"
    ROAD_FACTOR = 1.0  # the L-shaped path already approximates street distance
    SPEED_MPS = 12.5   # ~28 mph urban average

    async def route(self, origin: GeoPoint, destination: GeoPoint, *, via: list[GeoPoint] | None = None,
                    destination_name: str = "", via_names: list[str] | None = None, fresh: bool = False) -> Route:
        stops = [origin, *(via or []), destination]
        names = [*(via_names or [""] * len(via or [])), destination_name]
        geom: list[GeoPoint] = [origin]
        corners: list[tuple[int, str]] = []  # (geometry index, kind)
        for leg, (a, b) in enumerate(zip(stops, stops[1:], strict=False)):
            dlat_m = haversine(a, GeoPoint(lat=b.lat, lon=a.lon))
            dlon_m = haversine(a, GeoPoint(lat=a.lat, lon=b.lon))
            corner = GeoPoint(lat=b.lat, lon=a.lon) if dlat_m >= dlon_m else GeoPoint(lat=a.lat, lon=b.lon)
            if min(dlat_m, dlon_m) > 40:
                seg = densify([a, corner], 30)[1:]
                geom.extend(seg)
                corners.append((len(geom) - 1, "turn"))
                geom.extend(densify([corner, b], 30)[1:])
            else:
                geom.extend(densify([a, b], 30)[1:])
            corners.append((len(geom) - 1, "arrive" if leg == len(stops) - 2 else "waypoint"))
        cum = cumulative(geom)
        steps: list[RouteStep] = []
        first_b = bearing(geom[0], geom[min(1, len(geom) - 1)])
        steps.append(RouteStep(name="", distance_m=0, duration_s=0, geometry_index=0,
                               maneuver=Maneuver(type="depart", instruction=f"Head {_compass(first_b)}",
                                                 location=origin, bearing_after=first_b),
                               banner=f"Head {_compass(first_b)}"))
        wp = 0
        for idx, kind in corners:
            loc = geom[idx]
            if kind == "turn":
                b_in = bearing(geom[max(0, idx - 1)], loc)
                b_out = bearing(loc, geom[min(len(geom) - 1, idx + 1)])
                mod = _turn(b_in, b_out)
                instr = "Continue straight" if mod == "straight" else f"Turn {mod}"
                steps.append(RouteStep(name="", distance_m=0, duration_s=0, geometry_index=idx,
                                       maneuver=Maneuver(type="turn", modifier=mod, instruction=instr, location=loc,
                                                         bearing_after=b_out), banner=instr))
            else:
                nm = names[wp] if wp < len(names) else ""
                wp += 1
                instr = f"Arrive at {nm}" if nm else "Arrive at your destination"
                steps.append(RouteStep(name=nm, distance_m=0, duration_s=0, geometry_index=idx,
                                       maneuver=Maneuver(type="arrive", instruction=instr, location=loc),
                                       banner=instr))
        # fill per-step distance / duration
        for i, s in enumerate(steps):
            end = cum[steps[i + 1].geometry_index] if i + 1 < len(steps) else cum[-1]
            s.distance_m = max(0.0, end - cum[s.geometry_index])
            s.duration_s = s.distance_m / self.SPEED_MPS
        for st in steps:
            st.icon = maneuver_icon(st.maneuver.type, st.maneuver.modifier)
        for cur, nxt in zip(steps, steps[1:], strict=False):
            if nxt.maneuver.type != "arrive" and cur.distance_m > 60:
                instr = nxt.maneuver.instruction
                cur.voice.append(VoiceCue(at_m=min(160.0, cur.distance_m * 0.6),
                                          text=f"In 500 feet, {instr[:1].lower() + instr[1:]}"))
        dist = cum[-1] * self.ROAD_FACTOR
        route = Route(geometry=geom, distance_m=dist, duration_s=dist / self.SPEED_MPS + 30 * (len(corners) - 1),
                     steps=steps, summary="estimated", destination_name=destination_name,
                     waypoint_names=names, cumulative_m=cum, congestion="u" * (len(geom) - 1))
        route_polyline6(route)
        return route


class MapboxRouter:
    name = "mapbox"

    def __init__(self, client: MapboxClient, fallback: OfflineRouter | None = None):
        self.client = client
        self.fallback = fallback

    async def route(self, origin: GeoPoint, destination: GeoPoint, *, via: list[GeoPoint] | None = None,
                    destination_name: str = "", via_names: list[str] | None = None, fresh: bool = False) -> Route:
        pts = [origin, *(via or []), destination]
        names = ["", *(via_names or [""] * len(via or [])), destination_name]
        try:
            return with_cumulative(await self.client.directions(pts, names=names))
        except ProviderUnavailable:
            if not self.fallback:
                raise
            log.warning("Mapbox unavailable — using the offline estimator")
            return await self.fallback.route(origin, destination, via=via, destination_name=destination_name,
                                             via_names=via_names)
