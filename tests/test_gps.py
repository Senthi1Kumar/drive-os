"""Live GPS: NMEA/GPX parsing, sources, gear inference, the simulator ⇄ GPS switch, and Nova on real fixes."""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from driveos.domain.models import GeoPoint
from driveos.nav.gps import (
    SAMPLE_DRIVE,
    GearInference,
    GpsFix,
    GpsVehicle,
    NmeaAssembler,
    SwitchableVehicle,
    make_nmea,
    nmea_checksum_ok,
    open_source,
    parse_gpx,
    parse_nmea,
    replay_source,
)

RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


async def _wait(cond, timeout=8.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------------------------------ parsing
def test_nmea_checksum_and_rmc():
    assert nmea_checksum_ok(RMC)
    assert not nmea_checksum_ok(RMC.replace("*6A", "*6B"))
    m = parse_nmea(RMC)
    assert m["valid"] and m["lat"] == pytest.approx(48.1173) and m["lon"] == pytest.approx(11.516667, abs=1e-5)
    assert m["speed_mps"] == pytest.approx(22.4 * 0.514444) and m["track"] == 84.4
    assert m["time"] == dt.datetime(1994, 3, 23, 12, 35, 19, tzinfo=dt.UTC)


def test_gga_accuracy_and_no_fix():
    g = parse_nmea(GGA)
    assert g["satellites"] == 8 and g["accuracy_m"] == pytest.approx(4.5) and g["altitude_m"] == 545.4
    assert parse_nmea(f"$GPRMC,123519,V,,,,,,,230394,,*{_xor('GPRMC,123519,V,,,,,,,230394,,'):02X}")["valid"] is False
    assert parse_nmea("$GPGSV,3,1,11*00") is None
    assert parse_nmea("garbage") is None


def _xor(body: str) -> int:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return c


def test_assembler_merges_gga_into_rmc_and_roundtrips():
    fix = GpsFix(-33.8688, 151.2093, 12.5, 270.0, accuracy_m=3.0, altitude_m=20.0, satellites=12)
    lines = make_nmea(fix, dt.datetime(2026, 10, 27, 1, 2, 3, tzinfo=dt.UTC))
    asm = NmeaAssembler()
    assert asm.feed(lines[0]) is None            # GGA held until the RMC of the same epoch
    out = asm.feed(lines[1])
    assert out.lat == pytest.approx(-33.8688, abs=1e-5) and out.lon == pytest.approx(151.2093, abs=1e-5)
    assert out.speed_mps == pytest.approx(12.5, abs=0.06) and out.track == 270.0
    assert out.satellites == 12 and out.accuracy_m == pytest.approx(3.0)


def test_gpx_parsing():
    gpx = """<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
      <trkpt lat="37.2358" lon="-121.9746"><time>2026-10-27T16:00:00Z</time></trkpt>
      <trkpt lat="37.2368" lon="-121.9746"><time>2026-10-27T16:00:05Z</time></trkpt>
    </trkseg></trk></gpx>"""
    pts = parse_gpx(gpx)
    assert len(pts) == 2 and pts[1][1] - pts[0][1] == 5.0


# ------------------------------------------------------------------------------------------ sources
async def test_open_source_specs():
    label, stream = await open_source("replay@50")
    assert label == "replay"
    first = await anext(stream)
    assert isinstance(first, GpsFix) and first.lat == pytest.approx(37.2358, abs=1e-4)
    await stream.aclose()
    with pytest.raises(ValueError):
        await open_source("carrier-pigeon")


async def test_replay_gpx_derives_speed_and_heading(tmp_path: Path):
    p = tmp_path / "drive.gpx"
    p.write_text("""<gpx><trk><trkseg>
      <trkpt lat="37.2358" lon="-121.9746"><time>2026-10-27T16:00:00Z</time></trkpt>
      <trkpt lat="37.2367" lon="-121.9746"><time>2026-10-27T16:00:10Z</time></trkpt></trkseg></trk></gpx>""")
    fixes = [f async for f in replay_source(p, speed=1000, loop_forever=False)]
    assert fixes[1].speed_mps == pytest.approx(10.0, rel=0.05) and fixes[1].track == pytest.approx(0, abs=1)


async def test_tcp_nmea_source_like_a_phone_app():
    lines = make_nmea(GpsFix(37.2358, -121.9746, 0.0, 0.0, 5.0), dt.datetime.now(dt.UTC))

    async def serve(reader, writer):
        for ln in lines:
            writer.write((ln + "\r\n").encode())
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    label, stream = await open_source(f"tcp:127.0.0.1:{port}")
    fix = await asyncio.wait_for(anext(stream), 3)
    assert label == "phone GPS" and fix.accuracy_m == pytest.approx(5.0)
    await stream.aclose()
    server.close()


def test_bundled_sample_drive_is_valid():
    asm = NmeaAssembler()
    fixes = [r for ln in SAMPLE_DRIVE.read_text().splitlines() if isinstance(r := asm.feed(ln), GpsFix)]
    assert len(fixes) > 200
    assert max(f.speed_mps for f in fixes) > 10


# ------------------------------------------------------------------------------------------ vehicle
def test_gear_inference_has_hysteresis():
    g = GearInference()
    assert g.update(5.0, 0.0) == "P"
    assert g.update(0.0, 1.0) == "P"            # a blip doesn't count
    g.update(5.0, 2.0)
    assert g.update(5.0, 4.5) == "D"
    assert g.update(0.2, 5.0) == "D"            # a red light isn't Park…
    assert g.update(0.2, 11.5) == "P"           # …but staying still is


def test_gps_vehicle_derives_speed_and_ignores_jitter():
    clk = Clock()
    v = GpsVehicle("replay", clock=clk)
    seen = []
    v.subscribe(seen.append)
    v.on_fix(GpsFix(37.2358, -121.9746, accuracy_m=5.0))
    clk.t += 1
    v.on_fix(GpsFix(37.23581, -121.9746, accuracy_m=5.0))   # ~1 m: noise
    assert seen[-1].speed_mps == 0.0
    for i in range(2, 8):
        clk.t += 1
        v.on_fix(GpsFix(37.2358 + i * 0.00012, -121.9746, accuracy_m=5.0))   # ~13 m/s north
    assert seen[-1].speed_mps > 9 and abs(seen[-1].heading) < 2 and seen[-1].gear == "D"
    assert seen[-1].source == "gps" and v.status == "fix"


async def test_switchable_vehicle(settings):
    from driveos.nav.vehicle import VehicleSimulator

    q: asyncio.Queue = asyncio.Queue()

    async def opener(spec):
        async def stream():
            while True:
                yield await q.get()
        return "test", stream()

    sim = VehicleSimulator(settings.vehicle)
    gps = GpsVehicle("test", opener=opener)
    sw = SwitchableVehicle(sim, gps)
    states = []
    sw.subscribe(states.append)
    await sw.start()
    await sw.set_mode("gps")
    q.put_nowait(GpsFix(40.7128, -74.0060, 0.0, accuracy_m=8.0))
    assert await _wait(lambda: states and states[-1].source == "gps")
    assert sw.state.position.lat == pytest.approx(40.7128)
    sw.set_gear("D")                                    # the HMI can't put a real car in gear
    assert sw.state.gear == "P" and sim.gear == "P"
    await sw.set_mode("sim")                            # simulator picks up where GPS left off
    assert sim.state.position.lat == pytest.approx(40.7128) and sw.mode == "sim"
    assert gps.status == "off"
    await sw.stop()


# ------------------------------------------------------------------------------------------ Nova on GPS
@pytest.fixture
async def gps_nova(settings):
    from driveos.container import build_services

    q: asyncio.Queue = asyncio.Queue()

    async def opener(spec):
        async def stream():
            while True:
                yield await q.get()
        return "test GPS", stream()

    s = build_services(settings)
    s.vehicle.gps._opener = opener
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 200, 0.05
    labels = []

    async def geocode(p):
        labels.append(p)
        return "Bengaluru, Karnataka" if p.lat < 20 else "Los Gatos, California"

    s.nova.geocoder = geocode
    await s.start()
    s.nova._q = q
    yield s.nova
    await s.aclose()


async def test_first_fix_relabels_and_finds_places_where_you_are(gps_nova):
    n = gps_nova
    await n.set_vehicle_mode("gps")
    assert n.gps_info()["label"] == "GPS · searching"
    n._q.put_nowait(GpsFix(12.9716, 77.5946, 0.0, accuracy_m=6.0))
    assert await _wait(lambda: n.location_label == "Bengaluru, Karnataka")
    assert n.gps_info()["label"] == "GPS ±6 m"
    await n.handle_text("find me a coffee")              # no data-set city nearby → demo coffee bar
    assert [p.name for p in n.shop.places] == ["Daybreak Coffee Bar"]
    await n.handle_text("I want a sandwich")
    assert n.shop.places[0].name == "Fieldstone Deli"


async def test_real_position_reaching_the_pickup_completes_the_order(gps_nova):
    n = gps_nova
    await n.handle_text("find me a coffee")
    await n.open_place(place_id="local:r3-plur-coffee", speak=False)
    await n.handle_text("add a caramel latte")
    await n.handle_text("check out")
    await n.handle_text("confirm")
    await n.handle_text("yes")                           # guidance on…
    await n.set_vehicle_mode("gps")                     # …and the real car drives
    assert n.nav.mode == "navigating"
    dest = n.shop.order_place.location
    for _ in range(4):
        n._q.put_nowait(GpsFix(dest.lat, dest.lon, 0.0, accuracy_m=4.0))
        await asyncio.sleep(0.6)
    tx = n.shop.tx
    assert await _wait(lambda: tx.terminal, 15) and tx.state == "completed"


async def test_voice_switches_vehicle_mode(gps_nova):
    n = gps_nova
    await n.handle_text("use live GPS")
    assert n.vehicle_mode == "gps"
    await n.handle_text("simulate the drive")
    assert n.vehicle_mode == "sim"


async def test_location_picker_leaves_gps_mode(gps_nova):
    n = gps_nova
    await n.set_vehicle_mode("gps")
    await n.set_location(GeoPoint(lat=37.7749, lon=-122.4194), "San Francisco, California")
    assert n.vehicle_mode == "sim" and n._pos().lat == pytest.approx(37.7749)


# ------------------------------------------------------------------------------------------ demo
async def test_headless_demo_runs_the_whole_flow(settings):
    from driveos.container import build_services
    from driveos.demo import Demo

    settings.vehicle.simulator_time_scale = 60
    s = build_services(settings)
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 400, 0.02
    s.face.scan_s = 0.02
    lines = []
    await s.start()
    try:
        await asyncio.wait_for(Demo(s, pace=0.02, echo=lines.append).run(), 120)
    finally:
        await s.aclose()
    txs = list(s.orchestrator.transactions.values())
    assert len(txs) == 2 and all(t.state == "completed" for t in txs)
    assert any("use face id" in ln for ln in lines)                  # the fuel prepay is above the voice cap
    assert s.nova.nav.mode == "arrived" and s.nova.nav.dest_name == "Home"



async def test_leaving_the_route_on_gps_reroutes(gps_nova):
    n = gps_nova
    await n.handle_text("take me to work")
    await n.set_vehicle_mode("gps")
    assert n.nav.mode == "navigating"
    first = n.nav.route
    start = n._pos()
    for i in range(6):                                   # drive the wrong way, ~600 m south
        n._q.put_nowait(GpsFix(start.lat - 0.001 * (i + 1), start.lon - 0.004, 12.0, 180.0, accuracy_m=4.0))
        await asyncio.sleep(0.15)
    assert await _wait(lambda: n.nav.route is not first, 5)
    assert n.nav.mode == "navigating"
