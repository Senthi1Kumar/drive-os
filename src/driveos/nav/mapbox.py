"""Mapbox Directions v5 + Search Box v1 (category search along route, forward geocoding).

Contracts follow the Mapbox Directions and Search Box API references (docs.mapbox.com). Notes that shape this client:
* coordinates are lon,lat; driving-traffic accepts up to 25 coordinates;
* Search Box `route` must be encoded with the precision named in `route_geometry`;
* category results carry no detour time — DiscoveryService computes detours with Directions.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..config import MapboxConfig
from ..core.http import Http
from ..domain.errors import ConfigError, ProviderUnavailable
from ..domain.models import BannerLanes, GeoPoint, Lane, Maneuver, Route, RouteStep, VoiceCue
from .geo import decode_polyline, encode_polyline, haversine, with_cumulative
from .guidance import CONGESTION_LETTER, fit_congestion, maneuver_icon

log = logging.getLogger("driveos.mapbox")
API = "https://api.mapbox.com"

# our categories -> Mapbox canonical category ids
CATEGORY_IDS = {"coffee": "coffee", "cafe": "cafe", "food": "restaurant", "restaurant": "restaurant",
                "fast_food": "fast_food", "bakery": "bakery", "charging": "ev_charging_station",
                "parking": "parking_lot", "fuel": "gas_station"}


class MapboxClient:
    def __init__(self, cfg: MapboxConfig, http: Http):
        if not cfg.token:
            raise ConfigError("Mapbox token not configured")
        self.cfg = cfg
        self.http = http
        self._token = cfg.token.get_secret_value()

    # ------------------------------------------------------------------ directions
    async def directions(self, points: list[GeoPoint], *, names: list[str] | None = None) -> Route:
        if len(points) < 2 or len(points) > 25:
            raise ValueError("directions needs 2..25 coordinates")
        coords = ";".join(f"{p.lon:.6f},{p.lat:.6f}" for p in points)
        params = {
            "access_token": self._token,
            "geometries": "polyline6",
            "overview": "full",
            "steps": "true",
            "banner_instructions": "true",
            "voice_instructions": "true",
            "annotations": "congestion,distance,duration",
            "alternatives": "false",
            "voice_units": "imperial",
            "language": self.cfg.language,
        }
        if names:
            params["waypoint_names"] = ";".join(names)
        url = f"{API}/directions/v5/mapbox/{self.cfg.profile}/{coords}"
        r = await self.http.request("GET", url, params=params, service="mapbox.directions")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200 or data.get("code") != "Ok" or not data.get("routes"):
            # code only: server messages and URLs can echo the access token
            raise ProviderUnavailable(f"mapbox directions: {data.get('code') or r.status_code}")
        return parse_directions(data, names=names)

    # ------------------------------------------------------------------ search along route
    async def category_along_route(
        self, category: str, route: Route, *, origin: GeoPoint, limit: int = 10, time_deviation_min: int = 6,
    ) -> list[dict[str, Any]]:
        cid = CATEGORY_IDS.get(category, category)
        poly = encode_polyline(_thin(route.geometry, 220), precision=6)
        params = {
            "access_token": self._token,
            "sar_type": "isochrone",
            "route": poly,
            "route_geometry": "polyline6",
            "time_deviation": str(time_deviation_min),
            "limit": str(min(limit, 25)),
            "proximity": f"{origin.lon:.6f},{origin.lat:.6f}",
            "language": self.cfg.language,
        }
        r = await self.http.request("GET", f"{API}/search/searchbox/v1/category/{cid}", params=params,
                                    service="mapbox.search")
        if r.status_code != 200:
            raise ProviderUnavailable(f"mapbox search: HTTP {r.status_code}")
        return [parse_search_feature(f) for f in r.json().get("features", [])]

    async def category_near(self, category_id: str, *, proximity: GeoPoint, limit: int = 10,
                            bbox_m: float | None = None) -> list[dict[str, Any]]:
        params = {"access_token": self._token, "limit": str(min(limit, 25)),
                  "proximity": f"{proximity.lon:.6f},{proximity.lat:.6f}", "language": self.cfg.language}
        if bbox_m:
            dlat = bbox_m / 111_320.0
            dlon = bbox_m / (111_320.0 * max(0.2, math.cos(math.radians(proximity.lat))))
            params["bbox"] = (f"{proximity.lon - dlon:.5f},{proximity.lat - dlat:.5f},"
                              f"{proximity.lon + dlon:.5f},{proximity.lat + dlat:.5f}")
        r = await self.http.request("GET", f"{API}/search/searchbox/v1/category/{category_id}", params=params,
                                    service="mapbox.search")
        if r.status_code != 200:
            raise ProviderUnavailable(f"mapbox search: HTTP {r.status_code}")
        return [parse_search_feature(f) for f in r.json().get("features", [])]

    async def geocode(self, address: str, *, proximity: GeoPoint | None = None) -> GeoPoint | None:
        params = {"access_token": self._token, "q": address, "limit": "1", "types": "address,poi"}
        if proximity:
            params["proximity"] = f"{proximity.lon:.6f},{proximity.lat:.6f}"
        r = await self.http.request("GET", f"{API}/search/geocode/v6/forward", params=params,
                                    service="mapbox.geocode")
        if r.status_code != 200:
            raise ProviderUnavailable(f"mapbox geocode: HTTP {r.status_code}")
        feats = r.json().get("features") or []
        if not feats:
            return None
        lon, lat = feats[0]["geometry"]["coordinates"][:2]
        return GeoPoint(lat=lat, lon=lon)

    async def reverse(self, point: GeoPoint) -> str | None:
        params = {"access_token": self._token, "longitude": f"{point.lon:.6f}", "latitude": f"{point.lat:.6f}",
                  "types": "place,locality,neighborhood", "limit": "1"}
        r = await self.http.request("GET", f"{API}/search/geocode/v6/reverse", params=params,
                                    service="mapbox.geocode")
        if r.status_code != 200:
            return None
        feats = r.json().get("features") or []
        if not feats:
            return None
        p = feats[0].get("properties", {})
        ctx = p.get("context", {})
        region = (ctx.get("region") or {}).get("name", "")
        return ", ".join(x for x in (p.get("name"), region) if x)

    async def forward(self, query: str, *, proximity: GeoPoint, limit: int = 3) -> list[dict[str, Any]]:
        params = {"access_token": self._token, "q": query, "limit": str(limit),
                  "proximity": f"{proximity.lon:.6f},{proximity.lat:.6f}", "language": self.cfg.language}
        r = await self.http.request("GET", f"{API}/search/searchbox/v1/forward", params=params,
                                    service="mapbox.geocode")
        if r.status_code != 200:
            raise ProviderUnavailable(f"mapbox forward: HTTP {r.status_code}")
        return [parse_search_feature(f) for f in r.json().get("features", [])]


def _thin(points: list[GeoPoint], max_points: int) -> list[GeoPoint]:
    if len(points) <= max_points:
        return points
    step = len(points) / (max_points - 1)
    out = [points[int(i * step)] for i in range(max_points - 1)]
    out.append(points[-1])
    return out


def _banner_lanes(banner: dict[str, Any]) -> list[Lane]:
    """Lane components of a bannerInstructions entry's `sub`: `active` = recommended for the maneuver,
    `active_direction` = the arrow of that lane the route takes."""
    sub = banner.get("sub") or {}
    return [Lane(indications=[str(d) for d in c.get("directions") or []], valid=bool(c.get("active")),
                 active_indication=(str(c["active_direction"]) if c.get("active") and c.get("active_direction") else None))
            for c in sub.get("components") or [] if c.get("type") == "lane"]


def parse_directions(data: dict[str, Any], *, names: list[str] | None = None) -> Route:
    r0 = data["routes"][0]
    geom = decode_polyline(r0["geometry"], precision=6) if isinstance(r0["geometry"], str) else \
        [GeoPoint(lat=c[1], lon=c[0]) for c in r0["geometry"]["coordinates"]]
    steps: list[RouteStep] = []
    cursor = 0
    for leg in r0.get("legs", []):
        for s in leg.get("steps", []):
            m = s.get("maneuver", {})
            loc = GeoPoint(lat=m["location"][1], lon=m["location"][0])
            # locate the maneuver vertex, scanning forward from the previous one
            best_i, best_d = cursor, float("inf")
            for i in range(cursor, min(len(geom), cursor + 4000)):
                d = haversine(geom[i], loc)
                if d < best_d:
                    best_i, best_d = i, d
                if best_d < 1.0:
                    break
            cursor = best_i
            banner = None
            if s.get("bannerInstructions"):
                banner = (s["bannerInstructions"][0].get("primary") or {}).get("text")
            banner_lanes = [BannerLanes(at_m=float(b.get("distanceAlongGeometry") or 0.0), lanes=_banner_lanes(b))
                            for b in s.get("bannerInstructions") or []]
            voice = [VoiceCue(at_m=float(v.get("distanceAlongGeometry") or 0.0), text=str(v["announcement"]))
                     for v in s.get("voiceInstructions") or [] if v.get("announcement")]
            steps.append(RouteStep(
                name=s.get("name", ""), distance_m=float(s.get("distance", 0.0)), duration_s=float(s.get("duration", 0.0)),
                maneuver=Maneuver(type=m.get("type", "continue"), modifier=m.get("modifier"),
                                  instruction=m.get("instruction", ""), location=loc,
                                  bearing_after=m.get("bearing_after")),
                banner=banner, geometry_index=best_i,
                icon=maneuver_icon(m.get("type"), m.get("modifier")), voice=voice, banner_lanes=banner_lanes,
            ))
    wp_names = [w.get("name", "") for w in data.get("waypoints", [])]
    letters = "".join(CONGESTION_LETTER.get(str(c).lower(), "u")
                      for leg in r0.get("legs", []) for c in (leg.get("annotation") or {}).get("congestion") or [])
    poly = r0["geometry"] if isinstance(r0["geometry"], str) else encode_polyline(geom, 6)
    route = Route(geometry=geom, distance_m=float(r0["distance"]), duration_s=float(r0["duration"]), steps=steps,
                  polyline6=poly, congestion=fit_congestion(letters, len(geom)),
                  summary=" · ".join(leg.get("summary", "") for leg in r0.get("legs", []) if leg.get("summary")),
                  destination_name=(names[-1] if names else (wp_names[-1] if wp_names else "")),
                  waypoint_names=names or wp_names)
    return with_cumulative(route)


def parse_search_feature(f: dict[str, Any]) -> dict[str, Any]:
    p = f.get("properties", {})
    coords = f.get("geometry", {}).get("coordinates") or [
        p.get("coordinates", {}).get("longitude"), p.get("coordinates", {}).get("latitude")]
    meta = p.get("metadata") or {}
    return {
        "id": p.get("mapbox_id", ""),
        "name": p.get("name", ""),
        "location": GeoPoint(lat=coords[1], lon=coords[0]),
        "address": p.get("full_address") or p.get("address") or p.get("place_formatted", ""),
        "categories": p.get("poi_category_ids") or [],
        "brand": (p.get("brand") or [None])[0] if isinstance(p.get("brand"), list) else p.get("brand"),
        "phone": meta.get("phone"),
        "rating": meta.get("rating"),
        "distance_m": p.get("distance"),
        "eta_min": p.get("eta"),
    }
