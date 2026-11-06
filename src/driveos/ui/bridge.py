"""Bridge between the asyncio services and the QML HMI.

``AppBridge`` is exposed to QML as ``app``. State flows one way (Python → QML) through notify signals;
touch, the wheel button and typed utterances flow back through ``act(name, arg)`` and ``say(text)``,
which schedule the same Nova coroutines the voice path uses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QSize, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache
from PySide6.QtQml import QQmlNetworkAccessManagerFactory
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

from .actions import UiActions

log = logging.getLogger("driveos.ui")
ICONS = Path(__file__).resolve().parent / "assets" / "icons"

# Locations offered by the location picker (the 20 cities in the restaurant data set + Los Gatos)
LOCATIONS = [("Los Gatos, California", 37.2358, -121.9746)]


class IconProvider(QQuickImageProvider):
    """``image://icon/<name>?c=RRGGBB&w=2`` → a crisp, tinted Lucide icon at the requested size."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._svg: dict[str, str] = {}

    def requestImage(self, id: str, size: QSize, requested: QSize) -> QImage:  # noqa: A002 - Qt signature
        name, _, query = id.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        color = "#" + params.get("c", "e9eef5").lstrip("#")
        stroke = params.get("w", "2")
        if name not in self._svg:
            p = ICONS / f"{name}.svg"
            self._svg[name] = p.read_text() if p.is_file() else ICONS.joinpath("circle-dot.svg").read_text()
        svg = self._svg[name].replace("currentColor", color).replace('stroke-width="2"', f'stroke-width="{stroke}"')
        w = requested.width() if requested.width() > 0 else 24
        h = requested.height() if requested.height() > 0 else w
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        r = QSvgRenderer(svg.encode())
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r.render(painter)
        painter.end()
        if size is not None:
            size.setWidth(w)
            size.setHeight(h)
        return img


class CachingNamFactory(QQmlNetworkAccessManagerFactory):
    """Menu and storefront photos are cached on disk, so they still show when coverage drops."""

    def __init__(self, cache_dir: Path):
        super().__init__()
        self.cache_dir = cache_dir

    def create(self, parent):
        nam = QNetworkAccessManager(parent)
        cache = QNetworkDiskCache(nam)
        cache.setCacheDirectory(str(self.cache_dir))
        cache.setMaximumCacheSize(200 * 1024 * 1024)
        nam.setCache(cache)
        return nam


class AppBridge(QObject):
    stateChanged = Signal()
    routeChanged = Signal()
    markersChanged = Signal()
    vehicleChanged = Signal()
    cameraChanged = Signal()
    micLevelChanged = Signal()

    def __init__(self, *, map_mode: str, map_url: QUrl, map_config: dict, assets_url: QUrl, font_family: str,
                 display: tuple[int, int], fullscreen: bool, occupant_name: str, locations: list[dict]):
        super().__init__()
        self._state: dict[str, Any] = {}
        self._raw: dict[str, Any] = {}
        self._enroll = None
        self._route: list = []
        self._bounds: list = []
        self._markers: list = []
        self._vehicle: dict = {}
        self._camera = "overview"
        self._level = 0.0
        self._map_mode = map_mode
        self._map_url = map_url
        self._map_config = map_config
        self._assets = assets_url
        self._font = font_family
        self._display = display
        self._fullscreen = fullscreen
        self._occupant = occupant_name
        self._locations = locations
        self.actions = UiActions(locations)
        self.pipeline = None

    # ---------------------------------------------------------------- sink (called by Nova)
    def set_state(self, s: dict) -> None:
        self._raw = s
        self._republish()

    def _republish(self) -> None:
        s = dict(self._raw)
        s["bio"] = self._bio()
        self._state = s
        self.stateChanged.emit()

    def _bio(self) -> dict[str, Any]:
        """The biometric-payment view of Nova's state, derived by the same helpers the WebSocket snapshot uses."""
        from ..server import snapshot as snap

        nova = self.nova
        out: dict[str, Any] = {"auth": {"stage": "none", "challenge": None, "detail": ""}, "methodInfo": {},
                               "phone": None, "offline": False, "enroll": None}
        if nova is None:
            return out
        for key, fn in (("auth", lambda: snap._auth(nova)), ("methodInfo", nova.method_info),
                        ("phone", lambda: snap._phone(nova)), ("offline", lambda: bool(nova.voice.offline))):
            try:
                out[key] = fn()
            except Exception:
                log.debug("bio state %s unavailable", key, exc_info=True)
        if self._enroll is not None:
            out["enroll"] = self._enroll.state()
        return out

    def set_route(self, coords: list, bounds: list | None) -> None:
        self._route = coords
        self._bounds = bounds or []
        self.routeChanged.emit()

    def set_markers(self, m: list) -> None:
        if m != self._markers:
            self._markers = m
            self.markersChanged.emit()

    def set_vehicle(self, v: dict) -> None:
        self._vehicle = v
        self.vehicleChanged.emit()

    def set_camera(self, mode: str) -> None:
        if mode != self._camera:
            self._camera = mode
            self.cameraChanged.emit()

    def set_level(self, v: float) -> None:
        self._level = float(v)
        self.micLevelChanged.emit()

    # ---------------------------------------------------------------- properties
    state = Property("QVariantMap", lambda self: self._state, notify=stateChanged)
    route = Property("QVariantList", lambda self: self._route, notify=routeChanged)
    routeBounds = Property("QVariantList", lambda self: self._bounds, notify=routeChanged)
    markers = Property("QVariantList", lambda self: self._markers, notify=markersChanged)
    vehicle = Property("QVariantMap", lambda self: self._vehicle, notify=vehicleChanged)
    camera = Property(str, lambda self: self._camera, notify=cameraChanged)
    micLevel = Property(float, lambda self: self._level, notify=micLevelChanged)
    mapMode = Property(str, lambda self: self._map_mode, constant=True)
    mapUrl = Property(QUrl, lambda self: self._map_url, constant=True)
    mapConfig = Property("QVariantMap", lambda self: self._map_config, constant=True)
    assetsUrl = Property(QUrl, lambda self: self._assets, constant=True)
    fontFamily = Property(str, lambda self: self._font, constant=True)
    displayWidth = Property(int, lambda self: self._display[0], constant=True)
    displayHeight = Property(int, lambda self: self._display[1], constant=True)
    fullscreen = Property(bool, lambda self: self._fullscreen, constant=True)
    occupantName = Property(str, lambda self: self._occupant, constant=True)
    locations = Property("QVariantList", lambda self: self._locations, constant=True)

    # ---------------------------------------------------------------- Voice ID enrollment
    def _enroll_service(self):
        if self._enroll is None and self.nova is not None:
            from ..server.enroll import EnrollService

            self._enroll = EnrollService(self.nova.s, self.nova, self.pipeline, on_change=self._republish,
                                         set_level=self.set_level, on_enrolled=self._switch_to_voice_id)
        return self._enroll

    async def _switch_to_voice_id(self) -> None:
        import asyncio

        from ..wallet.auth import build_face

        nova = self.nova
        cfg = nova.s
        if cfg.auth.face not in ("auto", "driveauth") or nova.shop.checkout is not None:
            return
        face = await asyncio.to_thread(build_face, cfg.auth, cfg.biometrics)
        if hasattr(face, "attach_audio") and self.pipeline is not None:
            face.attach_audio(self.pipeline.mic, self.pipeline.stt)
        nova.set_face(face)

    def _enroll_call(self, call) -> None:
        from ..server.enroll import EnrollError

        svc = self._enroll_service()
        if svc is None:
            return
        try:
            call(svc)
        except EnrollError as e:
            if self.nova is not None:
                self.nova.notify(str(e))
        self._republish()

    @Slot(str, bool)
    def enrollStart(self, name: str, consent: bool) -> None:
        self._enroll_call(lambda e: e.start(name, True if consent else None))

    @Slot()
    def enrollTake(self) -> None:
        self._enroll_call(lambda e: e.take())

    @Slot()
    def enrollCancel(self) -> None:
        self._enroll_call(lambda e: e.cancel())

    # ---------------------------------------------------------------- input
    @property
    def nova(self):
        return self.actions.nova

    @nova.setter
    def nova(self, value) -> None:
        self.actions.nova = value

    @Slot(str)
    def say(self, text: str) -> None:
        self.actions.say(text)

    @Slot()
    def voiceButton(self) -> None:
        if self.pipeline is not None and self.pipeline.available:
            self.pipeline.listen()
        elif self.nova is not None:
            self.nova.notify("Voice input isn't available — press / to type")

    @Slot()
    def voiceEnd(self) -> None:
        """The End button: also closes the wake-once voice conversation."""
        if self.pipeline is not None:
            self.pipeline.end()

    @Slot(bool)
    def voiceMute(self, muted: bool) -> None:
        if self.pipeline is not None and self.pipeline.available:
            self.pipeline.set_muted(muted)

    @Slot(str)
    @Slot(str, "QVariant")
    @Slot(str, "QVariant", "QVariant")
    def act(self, name: str, arg: Any = None, arg2: Any = None) -> None:
        try:
            self.actions.start(name, arg, arg2)
        except KeyError:
            log.warning("unknown UI action %s", name)


class BridgeSink:
    """Adapts Nova's UiSink protocol onto the bridge."""

    def __init__(self, bridge: AppBridge):
        self.b = bridge

    def ui(self, state: dict) -> None:
        self.b.set_state(state)

    def route(self, coords: list, bounds: list | None) -> None:
        self.b.set_route(coords, bounds)

    def markers(self, markers: list) -> None:
        self.b.set_markers(markers)

    def vehicle(self, v: dict) -> None:
        self.b.set_vehicle(v)

    def camera(self, mode: str) -> None:
        self.b.set_camera(mode)
