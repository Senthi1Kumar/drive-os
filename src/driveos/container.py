"""Composition root: builds every service from Settings. The UI and CLI only talk to `Services`."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import __version__
from .agent.nova import Nova, Speaker, UiSink
from .config import Settings
from .core.http import Http
from .core.keys import FileKeyStore
from .core.private_fs import ensure_private_dir, harden_tree
from .discovery.service import DiscoveryService
from .domain.errors import ConfigError
from .domain.models import GeoPoint
from .merchants.registry import MerchantRegistry, build_registry
from .nav.router import MapboxRouter, OfflineRouter, Router
from .nav.vehicle import VehicleSource, build_vehicle
from .payments import build_payments
from .payments.base import PaymentProvider
from .wallet.auth import FaceVerifier, OtpService, build_face, build_otp
from .wallet.authority import TransactionAuthority
from .wallet.ledger import Ledger
from .wallet.saga import OrderOrchestrator

log = logging.getLogger("driveos.container")


@dataclass
class Services:
    settings: Settings
    http: Http
    ledger: Ledger
    registry: MerchantRegistry
    payments: PaymentProvider
    router: Router
    vehicle: VehicleSource
    face: FaceVerifier
    otp: OtpService
    authority: TransactionAuthority
    orchestrator: OrderOrchestrator
    discovery: DiscoveryService
    nova: Nova
    brain: object | None = None
    location: object | None = None     # nav.location.LocationManager
    images: object | None = None        # images.store.ImageStore

    async def start(self) -> None:
        n = self.ledger.verify()
        log.info("ledger verified (%d entries)", n)
        await self.vehicle.start()
        await self.nova.start()
        if self.location is not None:
            await self.location.start()
        if self.brain is not None:
            await self.brain.start()

    async def aclose(self) -> None:
        if self.location is not None:
            await self.location.stop()
        if self.images is not None:
            await self.images.aclose()
        if self.brain is not None:
            await self.brain.aclose()
        await self.orchestrator.aclose()
        await self.vehicle.stop()
        await self.registry.aclose()
        await self.payments.aclose()
        await self.http.aclose()
        self.ledger.close()


def build_router(settings: Settings, http: Http) -> Router:
    offline = OfflineRouter()
    if settings.mapbox.token:
        from .nav.mapbox import MapboxClient
        try:
            return MapboxRouter(MapboxClient(settings.mapbox, http), fallback=offline)
        except ConfigError as e:
            log.error("mapbox disabled: %s", e)
    return offline


def build_brain(settings: Settings, http: Http):
    """The OpenRouter tool agent. With no key it still loads, so the driver hears a one-time rules-only notice."""
    if settings.nlu.agent_enabled:
        from .agent.harness.brain import HarnessBrain
        return HarnessBrain(settings.nlu, http, data_dir=settings.biometrics.home)
    return None


def build_geocoder(settings: Settings, http: Http, router: Router):
    """point -> "Town, State" for the live-GPS location label: Mapbox, then OpenStreetMap, then the nearest
    catalogue city."""
    import json
    from importlib import resources

    from .nav.geo import haversine

    cities = []
    try:
        d = json.loads(resources.files("driveos.data.catalog").joinpath("dataset.json").read_text())
        cities = [(GeoPoint(lat=c["lat"], lon=c["lng"]), f"{c['city']}, {c['state']}") for c in d["cities"]]
    except Exception:
        pass
    cities.append((settings.vehicle.start, settings.vehicle.start_label))

    async def reverse(p: GeoPoint) -> str | None:
        client = getattr(router, "client", None)
        if client is not None:
            try:
                label = await client.reverse(p)
                if label:
                    return label
            except Exception as e:
                log.info("mapbox reverse failed: %s", e)
        try:
            r = await http.request("GET", "https://nominatim.openstreetmap.org/reverse", service="osm.reverse",
                                   params={"format": "jsonv2", "lat": f"{p.lat:.5f}", "lon": f"{p.lon:.5f}",
                                           "zoom": "12", "addressdetails": "1"},
                                   headers={"User-Agent": f"DriveOS/{__version__} (in-car demo)"}, retries=0)
            if r.status_code == 200:
                a = r.json().get("address", {})
                town = a.get("city") or a.get("town") or a.get("village") or a.get("suburb") or a.get("county")
                region = a.get("state") or a.get("country")
                if town:
                    return ", ".join(x for x in (town, region) if x)
        except Exception as e:
            log.info("osm reverse failed: %s", e)
        best = min(cities, key=lambda c: haversine(c[0], p), default=None)
        if best and haversine(best[0], p) < 30_000:
            return best[1]
        return None

    return reverse


def build_services(settings: Settings, *, speaker: Speaker | None = None, sink: UiSink | None = None,
                   registry: MerchantRegistry | None = None, payments: PaymentProvider | None = None,
                   face: FaceVerifier | None = None) -> Services:
    for d in {settings.data_dir, settings.biometrics.home}:
        ensure_private_dir(d)
        harden_tree(d)
    http = Http()
    keys = FileKeyStore(settings.data_dir / "device_key.pem")
    ledger = Ledger(settings.data_dir / "ledger.sqlite3", keys)
    registry = registry or build_registry(settings, http)
    payments = payments or build_payments(settings, http)
    router = build_router(settings, http)
    vehicle = build_vehicle(settings.vehicle, settings.nav)
    face = face or build_face(settings.auth, settings.biometrics)
    holder: dict = {}
    otp = build_otp(settings.auth, http, lambda code, purpose: holder["nova"].show_otp_on_phone(code, purpose))
    authority = TransactionAuthority(settings, ledger, registry, payments)
    orchestrator = OrderOrchestrator(settings, ledger, authority, registry, payments)
    discovery = DiscoveryService(settings, registry, router)
    brain = build_brain(settings, http)
    nova = Nova(settings, router=router, vehicle=vehicle, discovery=discovery, registry=registry, ledger=ledger,
                authority=authority, orchestrator=orchestrator, face=face, otp=otp, speaker=speaker, sink=sink,
                brain=brain)
    holder["nova"] = nova
    nova.geocoder = build_geocoder(settings, http, router)
    if brain is not None:
        brain.bind(nova)
    images = build_images(settings, http, nova)
    nova.images = images
    from .discovery.home import HomeFeed
    from .nav.location import LocationManager
    feed = HomeFeed(discovery, on_change=nova.publish)
    nova.home_feed = feed
    location = LocationManager(nova, vehicle, settings.location, on_change=nova.publish, refresh=feed.refresh)
    nova.location = location
    return Services(settings, http, ledger, registry, payments, router, vehicle, face, otp, authority, orchestrator,
                    discovery, nova, brain, location, images)


def build_images(settings: Settings, http: Http, nova: Nova):
    """Thumbnail cache for the HMI (photos downloaded in the background, generated food images resampled)."""
    if not settings.images.enabled:
        return None
    from pathlib import Path

    from .images.store import ImageStore, http_fetcher
    gen = settings.images.generated_dir or Path(__file__).resolve().parents[2] / "hmi_lvgl" / "assets" / "food"
    store = ImageStore(settings.data_dir / "cache" / "images", generated_dir=gen if gen.is_dir() else None,
                       max_bytes=settings.images.max_cache_mb * 1024 * 1024, fetch=http_fetcher(http),
                       on_ready=nova.publish)
    store.prepare_generated()
    return store
