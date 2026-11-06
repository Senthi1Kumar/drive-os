"""Headless back-end: the same services and voice pipeline as the Qt app, on plain asyncio."""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass

from ..config import Settings
from ..container import Services, build_services
from ..ui.actions import UiActions
from ..voice.tts import SilentSpeaker, build_speaker
from .enroll import EnrollService
from .ws import SpeakerTap, UiServer, UiSink

log = logging.getLogger("driveos.server")


@dataclass
class Backend:
    services: Services
    server: UiServer
    pipeline: object | None

    async def start(self) -> None:
        await self.services.start()
        if self.pipeline:
            await self.pipeline.start()
        await self.server.start()
        self.server.mark_dirty()

    async def stop(self) -> None:
        await self.server.stop()
        if self.pipeline:
            await self.pipeline.stop()
        await self.services.aclose()


def build_backend(settings: Settings, *, voice: bool = True, host: str | None = None,
                  port: int | None = None) -> Backend:
    holder: dict[str, UiServer] = {}
    tap = SpeakerTap(build_speaker(settings.voice) if voice else SilentSpeaker(),
                     lambda name, data: holder["server"].emit(name, data) if "server" in holder else None)
    sink = UiSink()
    services = build_services(settings, speaker=tap, sink=sink)
    actions = UiActions()
    actions.nova = services.nova

    pipeline = None
    if voice and settings.voice.enabled:
        from ..voice.audio import Microphone
        from ..voice.pipeline import VoicePipeline
        from ..voice.stt import build_stt
        from ..voice.wake import build_wake

        stt = build_stt(settings.voice, settings.biometrics.models_dir)
        mic = Microphone(settings.voice.input_device, settings.voice.sample_rate) if stt else None
        if hasattr(services.face, "attach_audio"):
            services.face.attach_audio(mic, stt)
        pipeline = VoicePipeline(settings.voice, services.nova, mic=mic, stt=stt,
                                 wake=build_wake(settings.voice, stt) if stt else None,
                                 level_cb=lambda v: holder["server"].set_level(v))
    else:
        services.nova.set_voice_available(False)

    def switch_to_voice_id() -> None:
        from ..wallet.auth import build_face

        if settings.auth.face not in ("auto", "driveauth") or services.nova.shop.checkout is not None:
            return
        face = build_face(settings.auth, settings.biometrics)
        if hasattr(face, "attach_audio") and pipeline is not None:
            face.attach_audio(pipeline.mic, pipeline.stt)
        services.face = face
        services.nova.set_face(face)

    async def on_enrolled() -> None:
        await asyncio.to_thread(switch_to_voice_id)

    enroll = EnrollService(settings, services.nova, pipeline, on_change=lambda: holder["server"].mark_dirty(),
                           set_level=lambda v: holder["server"].set_level(v), on_enrolled=on_enrolled)
    services.nova.capture_busy = lambda: enroll.active or enroll.face.active
    server = UiServer(services.nova, sink, actions, host=host or settings.server.host,
                      port=settings.server.port if port is None else port, pipeline=pipeline, enroll=enroll)
    holder["server"] = server
    return Backend(services, server, pipeline)


async def serve_forever(settings: Settings, *, voice: bool = True, host: str | None = None,
                        port: int | None = None) -> int:
    from ..app import _weather

    backend = build_backend(settings, voice=voice, host=host, port=port)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, done.set)
        except (NotImplementedError, RuntimeError):
            pass
    await backend.start()
    weather = asyncio.ensure_future(_weather(backend.services.nova, backend.services.http))
    try:
        await done.wait()
    finally:
        weather.cancel()
        await backend.stop()
    return 0
