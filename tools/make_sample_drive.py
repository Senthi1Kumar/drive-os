"""Regenerate src/driveos/data/gps/los_gatos_drive.nmea — a 1 Hz recorded drive (RMC + GGA) from the
default start in Los Gatos to Plur Coffee, produced by the vehicle simulator's physics.

    python tools/make_sample_drive.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from driveos.config import VehicleConfig
from driveos.merchants.local_catalog import LocalCatalog
from driveos.nav.geo import point_at
from driveos.nav.gps import GpsFix, make_nmea
from driveos.nav.router import OfflineRouter
from driveos.nav.vehicle import VehicleSimulator

OUT = Path(__file__).resolve().parents[1] / "src" / "driveos" / "data" / "gps" / "los_gatos_drive.nmea"


async def main() -> None:
    import tempfile

    cfg = VehicleConfig()
    cat = LocalCatalog(Path(tempfile.mkdtemp()))
    dest = cat.place(cat.merchant("r3-plur-coffee"), provider="local").location
    route = await OfflineRouter().route(cfg.start, dest, destination_name="Plur Coffee")
    sim = VehicleSimulator(cfg)
    sim.follow(route)
    t0 = dt.datetime(2026, 10, 27, 16, 30, 0, tzinfo=dt.UTC)
    lines: list[str] = []
    sec = 0
    for _ in range(10):                       # parked at the start
        lines += make_nmea(GpsFix(cfg.start.lat, cfg.start.lon, 0.0, 0.0, 3.8, 120.0, 10), t0 + dt.timedelta(seconds=sec))
        sec += 1
    while sim.gear != "P" or sec < 12:
        for _ in range(10):                   # 0.1 s physics ticks
            target = sim._target_speed()
            accel = 2.0 if target > sim.speed else -2.5
            sim.speed = max(0.0, min(target, sim.speed + accel * 0.1) if accel > 0 else max(target, sim.speed + accel * 0.1))
            sim.along += sim.speed * 0.1
        total = sim.route.cumulative_m[-1]
        if sim.along >= total - 2:
            sim.along, sim.speed, sim.gear = total, 0.0, "P"
        pos, hdg, _ = point_at(sim.route, sim.along)
        lines += make_nmea(GpsFix(pos.lat, pos.lon, sim.speed, hdg, 3.2, 118.0, 11), t0 + dt.timedelta(seconds=sec))
        sec += 1
    for _ in range(25):                       # parked at the pickup
        lines += make_nmea(GpsFix(pos.lat, pos.lon, 0.0, hdg, 3.5, 118.0, 11), t0 + dt.timedelta(seconds=sec))
        sec += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print(f"{OUT}: {sec} s, {route.distance_m:.0f} m")


if __name__ == "__main__":
    asyncio.run(main())
