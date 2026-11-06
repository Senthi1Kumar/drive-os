"""All configured merchant sources behind one lookup, queried in priority order."""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from ..core.http import Http
from ..domain.errors import ConfigError, DriveOSError
from ..domain.models import GeoPoint, Place, Route
from ..nav.geo import haversine
from .base import MerchantProvider

log = logging.getLogger("driveos.merchants")
SEARCH_TIMEOUT_S = 8.0


class MerchantRegistry:
    def __init__(self, providers: list[MerchantProvider]):
        if not providers:
            raise ConfigError("no merchant sources configured")
        self.providers = providers
        self._by_source = {p.source: p for p in providers}

    def get(self, provider: str) -> MerchantProvider:
        try:
            return self._by_source[provider]
        except KeyError as e:
            raise ConfigError(f"unknown merchant provider {provider}") from e

    async def search(self, *, near: GeoPoint, route: Route | None, category: str, query: str | None,
                     limit: int) -> list[Place]:
        async def one(p: MerchantProvider) -> list[Place]:
            try:
                return await asyncio.wait_for(
                    p.search(near=near, route=route, category=category, query=query, limit=limit), SEARCH_TIMEOUT_S)
            except (DriveOSError, TimeoutError) as e:
                log.warning("merchant source %s unavailable: %s", p.source, e)
                return []
            except Exception:
                log.exception("merchant source %s failed", p.source)
                return []

        results = await asyncio.gather(*(one(p) for p in self.providers))
        merged: list[Place] = []
        for places in results:  # providers are in priority order; first one to report a merchant wins
            for pl in places:
                dup = any(m.name.lower() == pl.name.lower() and haversine(m.location, pl.location) < 80
                          for m in merged)
                if not dup:
                    merged.append(pl)
        return merged

    async def aclose(self) -> None:
        for p in self.providers:
            try:
                await p.aclose()
            except Exception:
                log.exception("closing %s", p.source)


def local_ordering(settings: Settings) -> str:
    mode = settings.merchants.local_ordering
    if mode == "auto":
        return "simulated" if settings.demo else "off"
    return mode


def build_registry(settings: Settings, http: Http, *, catalog=None) -> MerchantRegistry:
    """Providers in priority order. Orderable sources come before map POIs so de-duplication keeps them."""
    providers: list[MerchantProvider] = []
    scale = settings.vehicle.simulator_time_scale
    sources = list(settings.merchants.sources)
    # with a Mapbox token the errands (fuel, chargers, stores, ...) are real places with sample menus,
    # so the invented demo venues are left out
    sample = settings.merchants.sample_menus and local_ordering(settings) == "simulated"
    real_places = bool(settings.mapbox.token) and "mapbox" in sources and "local" in sources and sample
    shared_catalog = None
    sources.sort(key=lambda s: s == "mapbox")  # map POIs last
    for src in sources:
        if src == "local":
            from .local import LocalMerchantProvider
            from .local_catalog import LocalCatalog
            cat = catalog or LocalCatalog(settings.data_dir, include_dataset=settings.merchants.include_dataset)
            shared_catalog = cat
            providers.append(LocalMerchantProvider(cat, time_scale=scale, ordering=local_ordering(settings),
                                                   radius_m=settings.merchants.search_radius_m,
                                                   demo_venues=not real_places))
        elif src == "mapbox":
            if not settings.mapbox.token:
                continue
            from ..nav.mapbox import MapboxClient
            from .mapbox_pois import MapboxPoiProvider
            providers.append(MapboxPoiProvider(MapboxClient(settings.mapbox, http),
                                               radius_m=min(settings.merchants.search_radius_m, 10_000),
                                               deviation_min=settings.nav.search_deviation_min,
                                               catalog=shared_catalog if sample else None, sample_menus=sample,
                                               cache_ttl_s=settings.merchants.mapbox_cache_ttl_s))
        elif src == "mcp":
            from .mcp_provider import McpMerchantProvider
            env = {"DRIVEOS_TIME_SCALE": str(scale)}
            if settings.google_places.api_key:
                env["GOOGLE_PLACES_API_KEY"] = settings.google_places.api_key.get_secret_value()
            for cfg in settings.merchants.mcp_servers:
                providers.append(McpMerchantProvider(cfg, extra_env=env))
        elif src == "square":
            try:
                from .square import SquareMerchantProvider
                providers.append(SquareMerchantProvider(settings.square, http))
            except ConfigError as e:
                log.error("square source disabled: %s", e)
    return MerchantRegistry(providers)
