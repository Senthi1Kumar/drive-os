"""Vehicle state sources.

* VehicleSimulator — drives the active route (accelerating, slowing for turns, stopping at pickup stops).
* Live GPS (nav/gps.py) — OS location service, gpsd, NMEA serial/TCP/UDP, or a recorded drive.
`build_vehicle` returns both behind a runtime switch. Each publishes VehicleState to subscribers ~10 Hz.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..config import NavConfig, VehicleConfig
from ..domain.models import GeoPoint, Route
from .geo import point_at, project, with_cumulative

log = logging.getLogger("driveos.vehicle")


@dataclass(frozen=True)
class VehicleState:
    position: GeoPoint
    heading: float
    speed_mps: float
    gear: str  # P | D | R | N
    outside_temp_f: float | None = None
    source: str = "sim"
    t_ms: float = 0.0  # monotonic stamp of this fix (set on publish); lets a client interpolate between fixes

    @property
    def moving(self) -> bool:
        """Driving restrictions apply whenever the car is not in Park — a red light is not a safe stop."""
        return self.gear != "P"

    @property
    def parked(self) -> bool:
        return self.gear == "P"


Listener = Callable[[VehicleState], None]


class VehicleSource:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self.state: VehicleState | None = None

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _publish(self, s: VehicleState) -> None:
        s = replace(s, t_ms=round(time.monotonic() * 1000.0, 1))
        self.state = s
        for fn in list(self._listeners):
            try:
                fn(s)
            except Exception:
                log.exception("vehicle listener failed")

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def follow(self, route: Route, stops_along_m: list[float] | None = None) -> None: ...
    def resume(self) -> None: ...
    def set_gear(self, gear: str) -> None: ...


class VehicleSimulator(VehicleSource):
    TICK_S = 0.1

    def __init__(self, cfg: VehicleConfig, nav: NavConfig | None = None):
        super().__init__()
        self.cfg = cfg
        self.cruise = nav.sim_speed_kmh / 3.6 if nav is not None else cfg.simulator_speed_mps
        self._detour_at_m = nav.sim_detour_at_m if nav is not None else None
        self._offset_m = 0.0
        self.scale = max(0.1, cfg.simulator_time_scale)
        self.route: Route | None = None
        self.along = 0.0
        self.speed = 0.0
        self.gear = "P"
        self._stops: list[float] = []
        self._hold = True
        self._task: asyncio.Task | None = None
        self._publish(VehicleState(cfg.start, cfg.start_heading, 0.0, "P", cfg.outside_temp_f))

    def follow(self, route: Route, stops_along_m: list[float] | None = None) -> None:
        """Start (or re-target) driving along `route`, keeping the current position on the new geometry."""
        self.route = with_cumulative(route)
        pos = self.state.position if self.state else route.geometry[0]
        self.along = project(self.route, pos).along_m
        self._stops = sorted(a for a in (stops_along_m or []) if a > self.along + 5)
        self._hold = False
        self._offset_m = 0.0
        if self.gear == "P":
            self.gear = "D"

    def inject_detour(self, offset_m: float = 120.0) -> None:
        """Demo/test hook: the car drifts `offset_m` sideways off the route until the next `follow` re-targets it."""
        self._offset_m = offset_m

    def _place(self, along_m: float) -> tuple[GeoPoint, float]:
        assert self.route is not None
        pos, hdg, _ = point_at(self.route, along_m)
        if self._offset_m:
            rad = math.radians(hdg + 90.0)
            dn, de = self._offset_m * math.cos(rad), self._offset_m * math.sin(rad)
            pos = GeoPoint(lat=pos.lat + dn / 111_320.0,
                           lon=pos.lon + de / (111_320.0 * max(0.2, math.cos(math.radians(pos.lat)))))
        return pos, hdg

    def resume(self) -> None:
        self._hold = False
        self.gear = "D"

    def hold(self) -> None:
        self._hold = True

    def teleport(self, along_m: float, *, park: bool = True) -> None:
        """Jump along the active route (showroom demos, QA)."""
        if not self.route:
            return
        self.along = max(0.0, min(along_m, self.route.cumulative_m[-1]))
        self.speed = 0.0
        if park:
            self.gear = "P"
            self._hold = True
        pos, hdg, _ = point_at(self.route, self.along)
        self._publish(VehicleState(pos, hdg, 0.0, self.gear, self.cfg.outside_temp_f, "sim"))

    def set_gear(self, gear: str) -> None:
        self.gear = gear

    def place_at(self, point: GeoPoint, heading: float | None = None) -> None:
        """Move the simulated car (location picker, demos). Clears the active route."""
        self.route = None
        self._stops = []
        self.along = 0.0
        self.speed = 0.0
        self.gear = "P"
        self._hold = True
        self._publish(VehicleState(point, heading if heading is not None else (self.state.heading if self.state
                                                                               else 0.0),
                                   0.0, "P", self.cfg.outside_temp_f, "sim"))

    async def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._run(), name="vehicle-sim")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def _target_speed(self) -> float:
        r = self.route
        assert r is not None
        total = r.cumulative_m[-1]
        limit = self.cruise
        # slow for upcoming maneuvers and stops (comfortable 1.4 m/s² decel)
        upcoming = [r.cumulative_m[min(s.geometry_index, len(r.cumulative_m) - 1)] for s in r.steps[1:]]
        for a in upcoming:
            d = a - self.along
            if 0 < d < 120:
                limit = min(limit, max(6.0, math.sqrt(2 * 1.4 * d) + 5.0))
        for a in [*self._stops, total]:
            d = a - self.along
            if 0 <= d < 200:
                limit = min(limit, math.sqrt(2 * 1.6 * max(d, 0.0)))
        return limit

    async def _run(self) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(self.TICK_S)
            now = time.monotonic()
            dt = (now - last) * self.scale
            last = now
            if self.route is None or self._hold or self.gear == "P":
                self.speed = 0.0
                s = self.state
                assert s is not None
                self._publish(replace(s, speed_mps=0.0, gear=self.gear))
                continue
            target = self._target_speed()
            accel = 2.0 if target > self.speed else -2.5
            self.speed = max(0.0, min(target, self.speed + accel * dt) if accel > 0 else max(target, self.speed + accel * dt))
            self.along += self.speed * dt
            total = self.route.cumulative_m[-1]
            if self._stops and self.along >= self._stops[0] - 3:
                self.along = self._stops.pop(0)
                self.speed = 0.0
                self.gear = "P"
                self._hold = True
            elif self.along >= total - 2:
                self.along = total
                self.speed = 0.0
                self.gear = "P"
                self._hold = True
            if self._detour_at_m is not None and self.along >= self._detour_at_m:
                self._detour_at_m = None
                self._offset_m = 120.0
            pos, hdg = self._place(self.along)
            self._publish(VehicleState(pos, hdg, self.speed, self.gear, self.cfg.outside_temp_f, "sim"))


def build_vehicle(cfg: VehicleConfig, nav: NavConfig | None = None) -> VehicleSource:
    """Simulator and live GPS behind one switch (Nav screen: Simulate / Live GPS)."""
    from .gps import GpsVehicle, SwitchableVehicle

    spec = cfg.gps_source
    if cfg.source == "gpsd":
        spec = f"gpsd:{cfg.gpsd_host}:{cfg.gpsd_port}"
    gps = GpsVehicle(spec, outside_temp_f=cfg.outside_temp_f)
    return SwitchableVehicle(VehicleSimulator(cfg, nav), gps, mode="gps" if cfg.source in ("gps", "gpsd") else "sim")
