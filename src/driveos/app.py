"""Application bootstrap: Qt + asyncio (qasync), services, voice pipeline and the QML HMI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from importlib import resources
from pathlib import Path

from .config import Settings

log = logging.getLogger("driveos.app")
UI = Path(__file__).resolve().parent / "ui"


def _pick_font() -> str:
    from PySide6.QtGui import QFontDatabase

    for f in sorted((UI / "assets" / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    return "Manrope" if "Manrope" in set(QFontDatabase.families()) else "Sans Serif"


def _map_mode(settings: Settings) -> str:
    if not settings.mapbox.token or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return "radar"
    try:
        from PySide6.QtWebEngineQuick import QtWebEngineQuick  # noqa: F401
    except Exception as e:  # pragma: no cover - platform dependent
        log.warning("Qt WebEngine unavailable (%s) — using the offline map", e)
        return "radar"
    return "mapbox"


def _locations(settings: Settings) -> list[dict]:
    out = [{"label": settings.vehicle.start_label, "lat": settings.vehicle.start.lat, "lon": settings.vehicle.start.lon}]
    try:
        d = json.loads(resources.files("driveos.data.catalog").joinpath("dataset.json").read_text())
    except FileNotFoundError:
        return out                    # the extended restaurant dataset is optional
    for c in d["cities"]:
        out.append({"label": f"{c['city']}, {c['state']}", "lat": c["lat"], "lon": c["lng"]})
    return out


async def _weather(nova, http) -> None:
    """Outside temperature for the Home glance (Open-Meteo, no key). Silent when offline."""
    codes = {0: ("Clear", "sun"), 1: ("Mostly clear", "cloud-sun"), 2: ("Partly cloudy", "cloud-sun"),
             3: ("Overcast", "cloud")}
    nova.weather_poke = asyncio.Event()
    while True:
        try:
            p = nova._pos()
            r = await http.request("GET", "https://api.open-meteo.com/v1/forecast", service="weather", params={
                "latitude": f"{p.lat:.3f}", "longitude": f"{p.lon:.3f}",
                "current": "temperature_2m,apparent_temperature,weather_code,is_day",
                "daily": "temperature_2m_max,temperature_2m_min", "forecast_days": "1",
                "temperature_unit": "fahrenheit", "timezone": "auto"}, retries=0)
            if r.status_code == 200:
                d = r.json()
                cur, daily = d.get("current", {}), d.get("daily", {})
                code = int(cur.get("weather_code", 0))
                label, icon = codes.get(code, ("Rain", "cloud-rain") if 50 <= code < 70 else
                                        ("Showers", "cloud-rain") if 80 <= code < 83 else ("Cloudy", "cloud"))
                if not cur.get("is_day", 1) and icon in ("sun", "cloud-sun"):
                    icon = "moon"
                nova.weather = {"temp": round(cur.get("temperature_2m", 0)), "feels": round(cur.get("apparent_temperature", 0)),
                                "hi": round((daily.get("temperature_2m_max") or [0])[0]),
                                "lo": round((daily.get("temperature_2m_min") or [0])[0]), "label": label, "icon": icon}
                nova.publish()
        except Exception as e:
            log.debug("weather unavailable: %s", e)
        nova.weather_poke.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(nova.weather_poke.wait(), 900)


def run(settings: Settings, *, script: list[str] | None = None, screenshot_dir: Path | None = None,
        voice: bool = True, demo: dict | None = None) -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    mode = _map_mode(settings)
    if mode == "mapbox":
        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtWebEngineQuick import QtWebEngineQuick

        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        QtWebEngineQuick.initialize()

    import qasync
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from .container import build_services
    from .ui.bridge import AppBridge, BridgeSink, CachingNamFactory, IconProvider
    from .voice.tts import SilentSpeaker, build_speaker

    qapp = QGuiApplication(sys.argv)
    qapp.setApplicationName("DriveOS")
    qapp.setOrganizationName("Elevatics AI")
    font = _pick_font()
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)

    map_cfg = {"token": settings.mapbox.token.get_secret_value() if settings.mapbox.token else "",
               "style": settings.mapbox.style, "lightPreset": settings.mapbox.light_preset,
               "theme": settings.mapbox.theme, "language": settings.mapbox.language,
               "center": [settings.vehicle.start.lon, settings.vehicle.start.lat]}
    bridge = AppBridge(
        map_mode=mode, map_url=QUrl.fromLocalFile(str(UI / "web" / "map.html")), map_config=map_cfg,
        assets_url=QUrl.fromLocalFile(str(UI / "assets") + "/"), font_family=font,
        display=(settings.display.width, settings.display.height), fullscreen=settings.display.fullscreen,
        occupant_name=settings.occupant().name, locations=_locations(settings),
    )
    speaker = build_speaker(settings.voice) if voice else SilentSpeaker()
    services = build_services(settings, speaker=speaker, sink=BridgeSink(bridge))
    bridge.nova = services.nova

    pipeline = None
    if demo is not None:
        services.nova.set_voice_available(False)       # the scripted driver talks; the mic stays closed
    elif voice and settings.voice.enabled:
        from .voice.audio import Microphone
        from .voice.pipeline import VoicePipeline
        from .voice.stt import build_stt
        from .voice.wake import build_wake

        stt = build_stt(settings.voice, settings.biometrics.models_dir)
        mic = Microphone(settings.voice.input_device, settings.voice.sample_rate) if stt else None
        if hasattr(services.face, "attach_audio"):
            services.face.attach_audio(mic, stt)
        pipeline = VoicePipeline(settings.voice, services.nova, mic=mic, stt=stt,
                                 wake=build_wake(settings.voice, stt) if stt else None, level_cb=bridge.set_level)
        bridge.pipeline = pipeline
    else:
        services.nova.set_voice_available(False)

    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", IconProvider())
    cache = settings.data_dir / "http-cache"
    cache.mkdir(parents=True, exist_ok=True)
    nam_factory = CachingNamFactory(cache)
    engine.setNetworkAccessManagerFactory(nam_factory)
    engine.addImportPath(str(UI / "qml"))
    engine.rootContext().setContextProperty("app", bridge)
    engine.load(QUrl.fromLocalFile(str(UI / "qml" / "DriveOS" / "Main.qml")))
    if not engine.rootObjects():
        log.error("failed to load the HMI")
        return 2

    done = asyncio.Event()

    def stop(*_):
        done.set()

    qapp.aboutToQuit.connect(stop)
    try:
        loop.add_signal_handler(signal.SIGINT, stop)
        loop.add_signal_handler(signal.SIGTERM, stop)
    except (NotImplementedError, RuntimeError):
        signal.signal(signal.SIGINT, lambda *_: stop())

    async def main() -> None:
        await services.start()
        weather = asyncio.ensure_future(_weather(services.nova, services.http))
        if pipeline:
            await pipeline.start()
        if screenshot_dir is not None:
            from .screens import capture_flow

            await capture_flow(engine, services, screenshot_dir)
            done.set()
        elif demo is not None:
            from .demo import Demo, start_from_gps

            await asyncio.sleep(1.5)
            if demo.get("gps"):
                await start_from_gps(services, echo=lambda m: (log.info(m), services.nova.notify(m[:90])))
            runner = Demo(services, pace=demo.get("pace", 1.0), echo=lambda m: log.info(m))
            try:
                await runner.run(loop=demo.get("loop", False))
            except Exception:
                log.exception("demo stopped")
            if demo.get("exit"):
                done.set()
        elif script:
            await asyncio.sleep(1.0)
            for line in script:
                await services.nova.handle_text(line)
                await asyncio.sleep(1.2)
        await done.wait()
        weather.cancel()
        if pipeline:
            await pipeline.stop()
        await services.aclose()

    with loop:
        try:
            loop.run_until_complete(main())
        finally:
            # tear the QML scene down while `app` (the bridge) is still alive, so no binding sees it vanish
            import shiboken6

            for obj in engine.rootObjects():
                shiboken6.delete(obj)
            shiboken6.delete(engine)
            qapp.processEvents()
    return 0
