"""WebSocket UI server: pushes Nova's state to a thin client and turns its commands into Nova calls."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .. import __version__
from ..agent.nova import Nova
from ..domain.errors import DriveOSError
from ..ui.actions import UiActions
from .commands import CommandError, Commands
from .snapshot import build_state

log = logging.getLogger("driveos.server")
PROTOCOL = 1
PATH = "/v1/ui"
MIN_STATE_GAP_S = 1 / 30
MIN_LEVEL_GAP_S = 1 / 15
TOAST_TTL_S = 4.0


def build_id() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "dev"
    except Exception:
        return "dev"


class UiSink:
    """Nova's UiSink: remembers the latest view and wakes the server; route/markers/camera have no client; vehicle fixes wake the server."""

    def __init__(self):
        self.view: dict[str, Any] | None = None
        self.on_change = lambda: None

    def ui(self, state: dict[str, Any]) -> None:
        self.view = state
        self.on_change()

    def route(self, coords, bounds) -> None: ...
    def markers(self, markers) -> None: ...
    def vehicle(self, v) -> None:
        self.on_change()      # every fix reaches the screen, so its puck moves at the simulator's rate
    def camera(self, mode: str) -> None: ...


class SpeakerTap:
    """Wraps the real speaker so each spoken line is also sent to the client as a ``say`` event."""

    def __init__(self, inner, emit):
        self.inner = inner
        self.emit = emit

    async def say(self, text: str) -> None:
        self.emit("say", {"text": text})
        await self.inner.say(text)

    def stop(self) -> None:
        self.inner.stop()


class UiServer:
    def __init__(self, nova: Nova, sink: UiSink, actions: UiActions, *, host: str = "127.0.0.1", port: int = 8790,
                 pipeline=None, enroll=None):
        self.nova = nova
        self.sink = sink
        self.host, self.port = host, port
        self.commands = Commands(actions, nova, pipeline, enroll)
        self.clients: set[ServerConnection] = set()
        self.seq = 0
        self.level = 0.0
        self.update: dict[str, Any] = {"available": False, "version": None, "highlights": []}
        self._toast: dict[str, str] | None = None
        self._toast_seq = nova.toast_seq
        self._toast_until = 0.0
        self._last_auth = "none"
        self._last_phone: str | None = None
        self._toast_emitted = 0
        self._last_json = ""
        self._last_sent = 0.0
        self._flush_handle: asyncio.TimerHandle | None = None
        self._level_at = 0.0
        self._server = None
        self._build = build_id()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._frame_busy: set[ServerConnection] = set()
        sink.on_change = self.mark_dirty

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        hub = self.nova.preview
        hub.add_state_listener(self._on_hub_state)
        hub.add_frame_sink(self._on_hub_frame)
        self._server = await serve(self._handle, self.host, self.port, process_request=self._check_path)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        log.info("UI server on ws://%s:%d%s", self.host, self.port, PATH)

    async def stop(self) -> None:
        self.nova.preview.remove_state_listener(self._on_hub_state)
        self.nova.preview.remove_frame_sink(self._on_hub_frame)
        if self._flush_handle:
            self._flush_handle.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @staticmethod
    def _check_path(connection, request):
        if request.path.split("?")[0] != PATH:
            return connection.respond(404, "not found\n")
        return None

    # ---------------------------------------------------------------- face preview (binary frames)
    def _on_hub_state(self) -> None:
        """The camera state changed (on an SDK thread): re-publish on the loop."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.mark_dirty)

    def _on_hub_frame(self, frame: bytes) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.push_frame, frame)

    def push_frame(self, frame: bytes) -> None:
        """Send one preview frame to every client as a binary message. A client still sending the previous frame
        misses this one, so a slow link drops frames instead of queueing them."""
        for ws in list(self.clients):
            if ws in self._frame_busy:
                continue
            self._frame_busy.add(ws)
            asyncio.ensure_future(self._send_frame(ws, frame))

    async def _send_frame(self, ws: ServerConnection, frame: bytes) -> None:
        try:
            await ws.send(frame)
        except ConnectionClosed:
            pass
        finally:
            self._frame_busy.discard(ws)

    # ---------------------------------------------------------------- state fan-out
    def set_level(self, v: float) -> None:
        now = time.monotonic()
        if now - self._level_at < MIN_LEVEL_GAP_S and v != 0.0:
            return
        self._level_at = now
        if round(float(v), 2) != round(self.level, 2):
            self.level = float(v)
            self.mark_dirty()

    def set_update(self, *, available: bool, version: str | None = None, highlights: list[str] | None = None) -> None:
        self.update = {"available": available, "version": version, "highlights": highlights or []}
        if available:
            self.emit("update_available", {"version": version, "highlights": highlights or []})
        self.mark_dirty()

    def emit(self, name: str, data: dict[str, Any]) -> None:
        self._send_all({"v": PROTOCOL, "type": "event", "name": name, "data": data})

    def mark_dirty(self) -> None:
        if self._flush_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        wait = max(0.0, MIN_STATE_GAP_S - (time.monotonic() - self._last_sent))
        self._flush_handle = loop.call_later(wait, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        state = self.snapshot()
        body = self._change_key(state)
        if body == self._last_json:
            return
        self._last_json = body
        self._last_sent = time.monotonic()
        self._track_events(state)
        self._send_all(self._state_msg(state))
        if self._toast and time.monotonic() >= self._toast_until:
            self._toast = None
        if self._toast:
            asyncio.get_running_loop().call_later(max(0.0, self._toast_until - time.monotonic()) + 0.01,
                                                  self.mark_dirty)

    @staticmethod
    def _change_key(state: dict[str, Any]) -> str:
        """The state without the fix stamp: a parked car re-stamped every tick is not a change worth a push."""
        veh = state.get("vehicle")
        if isinstance(veh, dict) and "t_ms" in veh:
            state = {**state, "vehicle": {k: v for k, v in veh.items() if k != "t_ms"}}
        return json.dumps(state, sort_keys=True)

    def snapshot(self) -> dict[str, Any]:
        if self.nova.toast_seq != self._toast_seq:
            self._toast_seq = self.nova.toast_seq
            self._toast = {"text": self.nova.toast, "kind": toast_kind(self.nova.toast)}
            self._toast_until = time.monotonic() + TOAST_TTL_S
        elif self._toast and time.monotonic() >= self._toast_until:
            self._toast = None
        view = self.sink.view
        if view is None:
            from ..viewmodel import build_view

            view = build_view(self.nova)
        svc = self.commands.enroll
        enroll = svc.state() if svc else None
        face = svc.face.state() if svc else None
        if enroll is not None and face is not None:
            enroll = {**enroll, "face": face}
        return build_state(self.nova, view, level=self.level, toast=self._toast, update=self.update, enroll=enroll,
                           enroll_face=face)

    def _track_events(self, state: dict[str, Any]) -> None:
        stage = state["shop"]["checkout"]["auth"]["stage"]
        if stage != self._last_auth:
            if stage in ("accepted", "rejected"):
                self.emit("auth_result", {"ok": stage == "accepted", "detail": state["shop"]["checkout"]["auth"]["detail"]})
            self._last_auth = stage
        phone = state.get("phone")
        key = phone["at"] if phone else None
        if key != self._last_phone:
            self._last_phone = key
            if phone:
                self.emit("phone_note", phone)
        if state["toast"] and self._toast_emitted != self._toast_seq:
            self.emit("toast", state["toast"])
        self._toast_emitted = self._toast_seq

    def _state_msg(self, state: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        return {"v": PROTOCOL, "type": "state", "seq": self.seq, "state": state}

    def _send_all(self, msg: dict[str, Any]) -> None:
        if not self.clients:
            return
        data = json.dumps(msg)
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, data))

    @staticmethod
    async def _send(ws: ServerConnection, data: str) -> None:
        try:
            await ws.send(data)
        except ConnectionClosed:
            pass

    # ---------------------------------------------------------------- connections
    async def _handle(self, ws: ServerConnection) -> None:
        hello = {"v": PROTOCOL, "type": "hello", "app": "DriveOS", "version": __version__, "build": self._build}
        images = getattr(self.nova, "images", None)
        if images is not None:
            hello["image_root"] = str(images.root)
        await ws.send(json.dumps(hello))
        state = self.snapshot()
        self._last_json = self._change_key(state)
        await ws.send(json.dumps(self._state_msg(state)))
        frame = self.nova.preview.last_frame
        if frame is not None and self.nova.preview.snapshot()["face_state"] != "none":
            await ws.send(frame)          # a client joining mid-scan (or after a result) sees the image at once
        self.clients.add(ws)
        try:
            async for raw in ws:
                await self._on_message(ws, raw)
        except ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    async def _on_message(self, ws: ServerConnection, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                raise ValueError("not an object")
        except ValueError:
            await ws.send(json.dumps({"v": PROTOCOL, "type": "ack", "id": None, "ok": False, "error": "bad json"}))
            return
        if msg.get("type") != "cmd":
            return
        cid, name = msg.get("id"), msg.get("name")
        error = None
        try:
            if not isinstance(name, str):
                raise CommandError("missing command name")
            args = msg.get("args") or {}
            if not isinstance(args, dict):
                raise CommandError("args must be an object")
            await self.commands.run(name, args)
        except CommandError as e:
            error = str(e)
        except DriveOSError as e:
            log.warning("command %s refused: %s (%s)", name, e, e.code)
            error = e.code
        except Exception as e:
            log.exception("command %s failed", name)
            error = f"{type(e).__name__}: {e}"
        await ws.send(json.dumps({"v": PROTOCOL, "type": "ack", "id": cid, "ok": error is None, "error": error}))
        self.mark_dirty()


def toast_kind(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("fail", "declin", "error", "denied", "locked")):
        return "danger"
    if any(w in t for w in ("isn't", "unavailable", "can't", "couldn't", "no ")):
        return "warn"
    if any(w in t for w in ("placed", "ready", "done", "approved", "ended")):
        return "ok"
    return "info"
