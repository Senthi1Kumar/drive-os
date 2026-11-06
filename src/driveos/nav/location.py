"""Which position the app uses: live GPS when there is a fix, otherwise a simulated place.

``LocationPolicy`` is the pure decision (with hysteresis); ``LocationManager`` applies it to a running
``Nova``/``SwitchableVehicle`` through their public methods, so it needs no change inside ``nova.py``.

* Going live needs a fix that has held for ``acquire_s``; falling back needs ``lose_s`` without one; and two
  switches are never closer than ``min_dwell_s``. An indoor receiver that blinks in and out never flaps.
* A mode change made by the user (the Nav screen switch, a picked location) ends automatic switching.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import LocationConfig
from ..domain.models import GeoPoint
from .geo import haversine

log = logging.getLogger("driveos.location")


class LocationPolicy:
    def __init__(self, cfg: LocationConfig, *, live: bool = False, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.live = live
        self._clock = clock
        self._changed = clock() - cfg.min_dwell_s      # the first decision is not held back
        self._since: float | None = None               # when the opposite condition started

    def update(self, fix: bool) -> bool | None:
        """Feed one observation. Returns the new state (True = live) when it changes, else None."""
        now = self._clock()
        if fix == self.live:
            self._since = None
            return None
        if self._since is None:
            self._since = now
        need = self.cfg.acquire_s if fix else self.cfg.lose_s
        if now - self._since < need or now - self._changed < self.cfg.min_dwell_s:
            return None
        self.live = fix
        self._changed = now
        self._since = None
        return self.live


@dataclass
class LocationSource:
    live: bool
    label: str      # "Live GPS" | "Simulated · Palo Alto"


class LocationManager:
    POLL_S = 1.0

    def __init__(self, nova, vehicle, cfg: LocationConfig, *, on_change: Callable[[], None] | None = None,
                 refresh: Callable[[GeoPoint], object] | None = None, clock: Callable[[], float] = time.monotonic):
        self.nova = nova
        self.vehicle = vehicle
        self.cfg = cfg
        self.on_change = on_change
        self.refresh = refresh          # async (position) -> None: re-query nearby places
        self._clock = clock
        self.gps = getattr(vehicle, "gps", None)
        self.auto = cfg.auto and self.gps is not None
        live = getattr(vehicle, "mode", "sim") == "gps"
        self.policy = LocationPolicy(cfg, live=live, clock=clock)
        self._expected = "gps" if live else "sim"
        self._task: asyncio.Task | None = None
        self._anchor: GeoPoint | None = None
        self._probing = False

    # ------------------------------------------------------------------ state for the UI
    def source(self) -> LocationSource:
        if getattr(self.vehicle, "mode", "sim") == "gps":
            return LocationSource(True, "Live GPS")
        town = (self.nova.location_label or self.cfg.fallback_label).split(",")[0]
        return LocationSource(False, f"Simulated · {town}")

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.cfg.auto and not self.policy.live:
            await self._apply_fallback()
        self._probing = await self._resolve_spec() if self.auto else False
        if self.auto and not self._probing:
            self.auto = False
        if self.auto:
            await self._probe()
            self._task = asyncio.create_task(self._run(), name="location")
        await self._refresh(force=True)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _resolve_spec(self) -> bool:
        """With ``gps_source = "auto"`` the probe only listens to a local receiver (gpsd, or a USB/ACM serial
        GPS). The OS location service and IP lookup are left to the manual Live GPS switch: neither is a
        receiver fix, and probing them would pull Qt into a headless server."""
        if self.gps is None:
            return False
        if self.gps.spec != "auto":
            return True
        from .gps import _gpsd_reachable

        if await _gpsd_reachable("127.0.0.1", 2947):
            self.gps.spec = "gpsd"
            return True
        for dev in ("/dev/ttyACM0", "/dev/ttyUSB0"):
            if Path(dev).exists():
                self.gps.spec = f"serial:{dev}"
                return True
        return False

    def _fix_ok(self) -> bool:
        g = self.gps
        if g is None or g.status != "fix":
            return False
        acc = g.accuracy_m
        return acc is not None and acc <= self.cfg.max_accuracy_m

    async def _probe(self) -> None:
        """Keep the receiver reading while simulating, so a fix is noticed."""
        if self.gps is not None and self._probing and getattr(self.vehicle, "mode", "sim") != "gps":
            with contextlib.suppress(Exception):
                await self.gps.start()

    async def _apply_fallback(self) -> None:
        await self.nova.set_location(self.cfg.fallback, self.cfg.fallback_label)
        self._expected = getattr(self.vehicle, "mode", "sim")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.POLL_S)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("location tick")

    async def tick(self) -> None:
        mode = getattr(self.vehicle, "mode", "sim")
        if self.auto and mode != self._expected:
            self.auto = False                      # the user took over
            log.info("location: manual %s mode, automatic switching off", mode)
        if self.auto and self.gps is not None:
            change = self.policy.update(self._fix_ok())
            if change is True:
                await self.nova.set_vehicle_mode("gps")
                self._expected = "gps"
                log.info("location: live GPS")
                self._changed_ui()
            elif change is False:
                await self.nova.set_vehicle_mode("sim")
                await self._apply_fallback()
                await self._probe()
                log.info("location: simulated (%s)", self.cfg.fallback_label)
                self._changed_ui()
                await self._refresh(force=True)
        await self._refresh()

    def _changed_ui(self) -> None:
        if self.on_change:
            self.on_change()

    async def _refresh(self, force: bool = False) -> None:
        if self.refresh is None:
            return
        pos = self.nova._pos()
        if not force and self._anchor is not None and haversine(self._anchor, pos) < self.cfg.refresh_m:
            return
        self._anchor = pos
        await self.refresh(pos)
