"""Google Places API (New): search along route, photos, ratings, price levels.

Billing note: `routingSummaries` is an Enterprise + Atmosphere field — enable `with_routing` only if the
detour estimate is worth it; otherwise DiscoveryService computes detours with the router.
Attribution: when a photo carries authorAttributions they must be shown with the image.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.http import Http
from ..domain.errors import ConfigError, ProviderUnavailable
from ..domain.models import GeoPoint
from ..nav.geo import encode_polyline

log = logging.getLogger("driveos.places")
API = "https://places.googleapis.com/v1"
_PRICE = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
          "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
_TYPES = {"coffee": "coffee_shop", "cafe": "cafe", "food": "restaurant", "restaurant": "restaurant",
          "bakery": "bakery", "fast_food": "fast_food_restaurant"}
_FIELDS = ("places.id,places.displayName,places.formattedAddress,places.location,places.rating,"
           "places.userRatingCount,places.priceLevel,places.currentOpeningHours.openNow,places.photos,"
           "places.primaryTypeDisplayName")


class GooglePlaces:
    def __init__(self, api_key: str, http: Http):
        if not api_key:
            raise ConfigError("Google Places key missing")
        self.key = api_key
        self.http = http

    async def _search(self, body: dict[str, Any], fields: str) -> dict[str, Any]:
        r = await self.http.request("POST", f"{API}/places:searchText", json=body, service="google.places",
                                    idempotent=True,
                                    headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": fields,
                                             "Content-Type": "application/json"})
        if r.status_code in (401, 403):
            raise ConfigError("Google Places key rejected")
        if r.status_code != 200:
            raise ProviderUnavailable(f"google places HTTP {r.status_code}")
        return r.json()

    async def along_route(self, category: str, route: list[GeoPoint], *, origin: GeoPoint, query: str | None = None,
                          limit: int = 8, with_routing: bool = False) -> list[dict[str, Any]]:
        poly = encode_polyline(_thin(route, 300), precision=5)
        body: dict[str, Any] = {
            "textQuery": query or category.replace("_", " "),
            "pageSize": max(1, min(limit, 20)),
            "searchAlongRouteParameters": {"polyline": {"encodedPolyline": poly}},
            "openNow": True,
        }
        t = _TYPES.get(category)
        if t and not query:
            body["includedType"] = t
        fields = _FIELDS
        if with_routing:
            body["routingParameters"] = {"origin": {"latitude": origin.lat, "longitude": origin.lon},
                                         "travelMode": "DRIVE"}
            fields += ",routingSummaries"
        data = await self._search(body, fields)
        places = [parse_place(p) for p in data.get("places", [])]
        for p, rs in zip(places, data.get("routingSummaries", []) or [], strict=False):
            legs = rs.get("legs") or []
            p["leg_durations_s"] = [_dur(leg.get("duration")) for leg in legs]
        return places

    async def find(self, name: str, near: GeoPoint, radius_m: float = 400.0) -> dict[str, Any] | None:
        body = {"textQuery": name, "pageSize": 1,
                "locationBias": {"circle": {"center": {"latitude": near.lat, "longitude": near.lon},
                                            "radius": radius_m}}}
        data = await self._search(body, _FIELDS)
        ps = data.get("places") or []
        return parse_place(ps[0]) if ps else None

    async def photo_uri(self, photo_name: str, max_width: int = 640) -> str | None:
        r = await self.http.request("GET", f"{API}/{photo_name}/media", service="google.photo",
                                    params={"maxWidthPx": str(max_width), "skipHttpRedirect": "true",
                                            "key": self.key})
        if r.status_code != 200:
            return None
        return r.json().get("photoUri")


def _thin(points: list[GeoPoint], n: int) -> list[GeoPoint]:
    if len(points) <= n:
        return points
    step = len(points) / (n - 1)
    return [points[int(i * step)] for i in range(n - 1)] + [points[-1]]


def _dur(s: str | None) -> float:
    try:
        return float((s or "0s").rstrip("s"))
    except ValueError:
        return 0.0


def parse_place(p: dict[str, Any]) -> dict[str, Any]:
    photos = p.get("photos") or []
    attributions = []
    if photos:
        attributions = [a.get("displayName", "") for a in photos[0].get("authorAttributions", [])]
    return {
        "id": p.get("id", ""),
        "name": (p.get("displayName") or {}).get("text", ""),
        "address": p.get("formattedAddress", ""),
        "location": {"lat": p.get("location", {}).get("latitude"), "lon": p.get("location", {}).get("longitude")},
        "rating": p.get("rating"),
        "rating_count": p.get("userRatingCount"),
        "price_level": _PRICE.get(p.get("priceLevel", ""), None),
        "open_now": (p.get("currentOpeningHours") or {}).get("openNow"),
        "photo_name": photos[0]["name"] if photos else None,
        "photo_attribution": ", ".join(a for a in attributions if a),
        "cuisine": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
    }
