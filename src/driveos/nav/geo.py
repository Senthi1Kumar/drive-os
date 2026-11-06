"""Geodesy helpers: distances, bearings, polyline codec, route projection and splicing."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..domain.models import GeoPoint, Route

R_EARTH = 6_371_008.8


def haversine(a: GeoPoint, b: GeoPoint) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(h)))


def bearing(a: GeoPoint, b: GeoPoint) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dl = math.radians(b.lon - a.lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def interpolate(a: GeoPoint, b: GeoPoint, t: float) -> GeoPoint:
    return GeoPoint(lat=a.lat + (b.lat - a.lat) * t, lon=a.lon + (b.lon - a.lon) * t)


# ---------------------------------------------------------------- polyline (Google algorithm, any precision)


def encode_polyline(points: Iterable[GeoPoint], precision: int = 6) -> str:
    f = 10 ** precision
    out: list[str] = []
    plat = plon = 0
    for p in points:
        lat, lon = round(p.lat * f), round(p.lon * f)
        for d in (lat - plat, lon - plon):
            v = ~(d << 1) if d < 0 else d << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = lat, lon
    return "".join(out)


def decode_polyline(s: str, precision: int = 6) -> list[GeoPoint]:
    f = 10 ** precision
    pts: list[GeoPoint] = []
    i = lat = lon = 0
    while i < len(s):
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else result >> 1)
        lat += vals[0]
        lon += vals[1]
        pts.append(GeoPoint(lat=lat / f, lon=lon / f))
    return pts


# ---------------------------------------------------------------- routes


def densify(points: Sequence[GeoPoint], max_step_m: float = 25.0) -> list[GeoPoint]:
    out: list[GeoPoint] = [points[0]]
    for a, b in zip(points, points[1:], strict=False):
        d = haversine(a, b)
        n = max(1, math.ceil(d / max_step_m))
        out.extend(interpolate(a, b, k / n) for k in range(1, n + 1))
    return out


def cumulative(points: Sequence[GeoPoint]) -> list[float]:
    acc = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        acc.append(acc[-1] + haversine(a, b))
    return acc


def with_cumulative(route: Route) -> Route:
    if len(route.cumulative_m) != len(route.geometry):
        route.cumulative_m = cumulative(route.geometry)
    return route


@dataclass
class Projection:
    index: int  # segment start vertex
    t: float  # 0..1 along the segment
    point: GeoPoint
    offset_m: float  # perpendicular distance from route
    along_m: float  # distance from route start


def _local_xy(origin: GeoPoint, p: GeoPoint) -> tuple[float, float]:
    kx = math.cos(math.radians(origin.lat)) * 111_320.0
    return ((p.lon - origin.lon) * kx, (p.lat - origin.lat) * 111_132.0)


def project(route: Route, p: GeoPoint, *, hint: int = 0, window: int | None = None) -> Projection:
    """Nearest point on the route polyline. `hint`/`window` restrict the search for tracking."""
    geom = route.geometry
    cum = route.cumulative_m or cumulative(geom)
    lo = max(0, hint - (window or len(geom)))
    hi = min(len(geom) - 1, hint + (window or len(geom)))
    best: Projection | None = None
    for i in range(lo, hi):
        a, b = geom[i], geom[i + 1]
        ax, ay = 0.0, 0.0
        bx, by = _local_xy(a, b)
        px, py = _local_xy(a, p)
        seg2 = bx * bx + by * by
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
        qx, qy = ax + t * bx, ay + t * by
        d = math.hypot(px - qx, py - qy)
        if best is None or d < best.offset_m:
            seg_len = cum[i + 1] - cum[i]
            best = Projection(i, t, interpolate(a, b, t), d, cum[i] + t * seg_len)
    if best is None:
        return Projection(0, 0.0, geom[0], haversine(geom[0], p), 0.0)
    return best


def point_at(route: Route, along_m: float) -> tuple[GeoPoint, float, int]:
    """Point and heading at a distance along the route. Returns (point, bearing, segment index)."""
    geom, cum = route.geometry, route.cumulative_m or cumulative(route.geometry)
    along_m = max(0.0, min(along_m, cum[-1]))
    lo, hi = 0, len(cum) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if cum[mid] <= along_m:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo]
    t = 0.0 if seg == 0 else (along_m - cum[lo]) / seg
    return interpolate(geom[lo], geom[hi], t), bearing(geom[lo], geom[hi]), lo


def bounds(points: Iterable[GeoPoint]) -> tuple[GeoPoint, GeoPoint]:
    pts = list(points)
    return (GeoPoint(lat=min(p.lat for p in pts), lon=min(p.lon for p in pts)),
            GeoPoint(lat=max(p.lat for p in pts), lon=max(p.lon for p in pts)))


def splice_stop(route: Route, stop: GeoPoint, *, from_along_m: float = 0.0) -> tuple[Route, float]:
    """Offline detour: leave the route at the point nearest the stop, go to it and come back.

    Returns the new route and the added distance in metres. Used when no routing service is reachable.
    """
    pr = project(route, stop)
    if pr.along_m < from_along_m:
        pr = project(Route(geometry=route.geometry, distance_m=route.distance_m, duration_s=route.duration_s,
                           cumulative_m=route.cumulative_m), stop)
    geom = list(route.geometry)
    i = pr.index
    spur: list[GeoPoint] = []
    if pr.offset_m > 15:
        spur = densify([pr.point, stop])[1:] + densify([stop, pr.point])[1:]
    new_geom = geom[: i + 1] + [pr.point] + spur + [pr.point] + geom[i + 1:]
    added = 2 * pr.offset_m if spur else 0.0
    speed = route.distance_m / route.duration_s if route.duration_s else 12.0
    new = route.model_copy(update={
        "geometry": new_geom, "distance_m": route.distance_m + added,
        "duration_s": route.duration_s + added / max(speed, 1.0), "cumulative_m": [],
        "polyline6": "", "congestion": "",
    })
    # shift step indices that come after the splice point
    shift = len(new_geom) - len(geom)
    new.steps = [s.model_copy(update={"geometry_index": s.geometry_index + (shift if s.geometry_index > i else 0)})
                 for s in route.steps]
    return with_cumulative(new), added
