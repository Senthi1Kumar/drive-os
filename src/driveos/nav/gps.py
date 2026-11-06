"""Live GPS for DriveOS.

``GpsVehicle`` turns position fixes into the same ``VehicleState`` stream the simulator produces, so
discovery, routing, ETA and pickup-arrival detection all run on the real position.

Fix sources (``vehicle.gps_source`` / ``driveos --gps-source``):

    auto                    gpsd if it is running, else the OS location service (Qt Positioning), else IP
    qt                      OS location service via Qt Positioning — CoreLocation (macOS), Windows Location,
                            GeoClue2 (Linux); a USB GPS also works here with QT_NMEA_SERIAL_PORT=/dev/ttyACM0
    gpsd[:host:port]        gpsd JSON (Linux in-car computers, Raspberry Pi with a GPS HAT)
    serial:/dev/ttyACM0[@9600]   NMEA 0183 straight from a USB / UART receiver (u-blox, GlobalSat …)
    tcp:host:port           NMEA over TCP — phone apps such as "GPS2IP" (iOS) or "Share GPS" (Android)
    udp:port                NMEA pushed over UDP (most phone NMEA forwarders)
    replay[:file][@speed]   replay a recorded .nmea or .gpx drive in real time (bundled Los Gatos drive)
    ip                      approximate position from your IP address — city level, never moves

Gear is not on the GPS, so it is inferred from speed with hysteresis: the car is "in Drive" once it has
been moving above walking pace for a few seconds and back "in Park" after it has been still for a while.
Ordering restrictions that depend on Park therefore follow the real car.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import math
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from ..domain.models import GeoPoint, Route
from .geo import haversine
from .vehicle import VehicleSimulator, VehicleSource, VehicleState

log = logging.getLogger("driveos.gps")

KNOT = 0.514444
SAMPLE_DRIVE = Path(__file__).resolve().parent.parent / "data" / "gps" / "los_gatos_drive.nmea"


# ---------------------------------------------------------------------------------------------- fixes
@dataclass(frozen=True)
class GpsFix:
    lat: float
    lon: float
    speed_mps: float | None = None
    track: float | None = None
    accuracy_m: float | None = None
    altitude_m: float | None = None
    satellites: int | None = None
    time: dt.datetime | None = None

    @property
    def point(self) -> GeoPoint:
        return GeoPoint(lat=self.lat, lon=self.lon)


def nmea_checksum_ok(sentence: str) -> bool:
    s = sentence.strip()
    if not s.startswith("$"):
        return False
    if "*" not in s:
        return True                               # checksum is optional in NMEA 0183
    body, _, cs = s[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(cs[:2], 16)
    except ValueError:
        return False


def _latlon(v: str, hemi: str) -> float | None:
    if not v:
        return None
    dot = v.find(".")
    deg_len = (dot if dot >= 0 else len(v)) - 2
    try:
        deg = float(v[:deg_len])
        mins = float(v[deg_len:])
    except ValueError:
        return None
    x = deg + mins / 60.0
    return -x if hemi in ("S", "W") else x


def _f(v: str) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None


def parse_nmea(sentence: str) -> dict | None:
    """Parse one RMC or GGA sentence (any talker: GP, GN, GL, GA, BD). Returns None for anything else."""
    s = sentence.strip()
    if not s.startswith("$") or not nmea_checksum_ok(s):
        return None
    f = s[1:].split("*")[0].split(",")
    kind = f[0][-3:]
    if kind == "RMC" and len(f) >= 10:
        if f[2] != "A":                           # V = receiver warning, no fix
            return {"type": "RMC", "valid": False}
        lat, lon = _latlon(f[3], f[4]), _latlon(f[5], f[6])
        if lat is None or lon is None:
            return {"type": "RMC", "valid": False}
        when = None
        if len(f[1]) >= 6 and len(f[9]) == 6:
            with contextlib.suppress(ValueError):
                secs = float(f[1][4:])
                yy = int(f[9][4:6])
                when = dt.datetime((1900 if yy >= 80 else 2000) + yy, int(f[9][2:4]), int(f[9][0:2]), int(f[1][0:2]),
                                   int(f[1][2:4]), int(secs), int((secs % 1) * 1e6), tzinfo=dt.UTC)
        knots = _f(f[7])
        return {"type": "RMC", "valid": True, "lat": lat, "lon": lon,
                "speed_mps": knots * KNOT if knots is not None else None, "track": _f(f[8]), "time": when}
    if kind == "GGA" and len(f) >= 10:
        quality = int(f[6]) if f[6].isdigit() else 0
        if quality == 0:
            return {"type": "GGA", "valid": False}
        lat, lon = _latlon(f[2], f[3]), _latlon(f[4], f[5])
        if lat is None or lon is None:
            return {"type": "GGA", "valid": False}
        hdop = _f(f[8])
        return {"type": "GGA", "valid": True, "lat": lat, "lon": lon, "quality": quality,
                "satellites": int(f[7]) if f[7].isdigit() else None, "hdop": hdop,
                "accuracy_m": round(hdop * 5.0, 1) if hdop else None, "altitude_m": _f(f[9])}
    return None


class NmeaAssembler:
    """Combines RMC (position, speed, course) and GGA (accuracy, satellites) into one fix per epoch."""

    def __init__(self) -> None:
        self._gga: dict | None = None

    def feed(self, line: str) -> GpsFix | None | bool:
        """Returns a GpsFix, False for an explicit 'no fix' report, None for nothing new."""
        m = parse_nmea(line)
        if m is None:
            return None
        if not m["valid"]:
            return False if m["type"] == "RMC" else None
        if m["type"] == "GGA":
            self._gga = m
            return None
        g = self._gga if self._gga and abs(self._gga["lat"] - m["lat"]) < 0.01 else None
        return GpsFix(m["lat"], m["lon"], m["speed_mps"], m["track"],
                      accuracy_m=g.get("accuracy_m") if g else None,
                      altitude_m=g.get("altitude_m") if g else None,
                      satellites=g.get("satellites") if g else None, time=m["time"])


def make_nmea(fix: GpsFix, when: dt.datetime) -> list[str]:
    """RMC + GGA sentences for a fix (used for the bundled sample drive and in tests)."""

    def dm(x: float, lat: bool) -> tuple[str, str]:
        a = abs(x)
        d = int(a)
        m = (a - d) * 60
        hemi = ("N" if x >= 0 else "S") if lat else ("E" if x >= 0 else "W")
        return (f"{d:02d}{m:07.4f}" if lat else f"{d:03d}{m:07.4f}"), hemi

    def wrap(body: str) -> str:
        c = 0
        for ch in body:
            c ^= ord(ch)
        return f"${body}*{c:02X}"

    la, lh = dm(fix.lat, True)
    lo, oh = dm(fix.lon, False)
    t = when.strftime("%H%M%S") + f".{when.microsecond // 10000:02d}"
    kn = (fix.speed_mps or 0.0) / KNOT
    rmc = wrap(f"GPRMC,{t},A,{la},{lh},{lo},{oh},{kn:.1f},{(fix.track or 0.0):.1f},{when.strftime('%d%m%y')},,,A")
    hdop = (fix.accuracy_m or 4.0) / 5.0
    gga = wrap(f"GPGGA,{t},{la},{lh},{lo},{oh},1,{fix.satellites or 9:02d},{hdop:.1f},"
               f"{fix.altitude_m or 120.0:.1f},M,-30.0,M,,")
    return [gga, rmc]


def parse_gpx(text: str) -> list[tuple[GpsFix, float | None]]:
    """Track points with their timestamps (epoch seconds) — namespaces ignored."""
    root = ET.fromstring(text)
    out: list[tuple[GpsFix, float | None]] = []
    for el in root.iter():
        if not el.tag.endswith(("trkpt", "rtept")):
            continue
        ts = None
        for c in el:
            if c.tag.endswith("time") and c.text:
                with contextlib.suppress(ValueError):
                    ts = dt.datetime.fromisoformat(c.text.strip().replace("Z", "+00:00")).timestamp()
        out.append((GpsFix(float(el.get("lat")), float(el.get("lon"))), ts))
    return out


def bearing(a: GeoPoint, b: GeoPoint) -> float:
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dl = math.radians(b.lon - a.lon)
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


# ---------------------------------------------------------------------------------------------- backends
FixStream = AsyncIterator["GpsFix | bool"]


async def _lines_tcp(host: str, port: int) -> AsyncIterator[str]:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        while line := await reader.readline():
            yield line.decode("ascii", "ignore")
    finally:
        writer.close()


async def nmea_stream(lines: AsyncIterator[str]) -> FixStream:
    asm = NmeaAssembler()
    async for line in lines:
        r = asm.feed(line)
        if r is not None:
            yield r


async def tcp_source(host: str, port: int) -> FixStream:
    async for r in nmea_stream(_lines_tcp(host, port)):
        yield r


async def udp_source(port: int) -> FixStream:
    q: asyncio.Queue[str] = asyncio.Queue()

    class P(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            for ln in data.decode("ascii", "ignore").splitlines():
                q.put_nowait(ln)

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(P, local_addr=("0.0.0.0", port))

    async def lines():
        while True:
            yield await q.get()

    try:
        async for r in nmea_stream(lines()):
            yield r
    finally:
        transport.close()


async def serial_source(device: str, baud: int = 9600) -> FixStream:
    """NMEA from a serial receiver. Uses pyserial when installed, else reads the tty directly
    (USB CDC receivers such as u-blox ignore the baud rate)."""
    q: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = False

    def reader():
        try:
            try:
                import serial  # type: ignore

                port = serial.Serial(device, baud, timeout=1)
                readline = port.readline
            except ImportError:
                port = open(device, "rb", buffering=0)  # noqa: SIM115
                buf = bytearray()

                def readline():
                    while not stop:
                        ch = port.read(1)
                        if not ch:
                            return b""
                        buf.extend(ch)
                        if ch == b"\n":
                            out = bytes(buf)
                            buf.clear()
                            return out
                    return b""
            with port:
                while not stop:
                    ln = readline()
                    if ln:
                        loop.call_soon_threadsafe(q.put_nowait, ln.decode("ascii", "ignore"))
        except Exception as e:
            log.warning("serial GPS %s: %s", device, e)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    fut = loop.run_in_executor(None, reader)

    async def lines():
        while (ln := await q.get()) is not None:
            yield ln
        await fut
        raise ConnectionError(f"serial GPS {device} closed")

    try:
        async for r in nmea_stream(lines()):
            yield r
    finally:
        stop = True


async def gpsd_source(host: str = "127.0.0.1", port: int = 2947) -> FixStream:
    import json

    reader, writer = await asyncio.open_connection(host, port)
    writer.write(b'?WATCH={"enable":true,"json":true}\n')
    await writer.drain()
    try:
        while line := await reader.readline():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("class") != "TPV":
                continue
            if msg.get("mode", 0) < 2 or "lat" not in msg:
                yield False
                continue
            acc = max(msg.get("epx") or 0, msg.get("epy") or 0) or msg.get("eph")
            yield GpsFix(float(msg["lat"]), float(msg["lon"]), msg.get("speed"), msg.get("track"),
                         accuracy_m=float(acc) if acc else None, altitude_m=msg.get("altMSL") or msg.get("alt"))
    finally:
        writer.close()


async def replay_source(path: Path | None = None, speed: float = 1.0, loop_forever: bool = True) -> FixStream:
    """Replay a recorded drive in real time (scaled by ``speed``)."""
    path = path or SAMPLE_DRIVE
    text = path.read_text(errors="ignore")
    if path.suffix.lower() == ".gpx" or text.lstrip().startswith("<"):
        pts = parse_gpx(text)
        epochs: list[tuple[GpsFix, float | None]] = pts
    else:
        asm = NmeaAssembler()
        epochs = []
        for ln in text.splitlines():
            r = asm.feed(ln)
            if isinstance(r, GpsFix):
                epochs.append((r, r.time.timestamp() if r.time else None))
    if not epochs:
        raise ValueError(f"no fixes in {path}")
    while True:
        prev_ts: float | None = None
        prev_fix: GpsFix | None = None
        for fix, ts in epochs:
            gap = (ts - prev_ts) if (ts is not None and prev_ts is not None) else 1.0
            if prev_fix is not None:
                await asyncio.sleep(max(0.0, min(gap, 30.0)) / max(speed, 0.01))
            if fix.speed_mps is None and prev_fix is not None and gap > 0:
                d = haversine(prev_fix.point, fix.point)
                fix = replace(fix, speed_mps=d / gap, track=bearing(prev_fix.point, fix.point) if d > 1 else None)
            yield replace(fix, accuracy_m=fix.accuracy_m or 4.0)
            prev_ts, prev_fix = ts, fix
        if not loop_forever:
            return
        await asyncio.sleep(2.0 / max(speed, 0.01))


async def ip_source(http=None) -> FixStream:
    """City-level position from the public IP — a last resort so the demo opens where you are."""
    import httpx

    urls = ["https://ipapi.co/json/", "https://ipinfo.io/json"]
    async with httpx.AsyncClient(timeout=6.0) as c:
        for u in urls:
            try:
                r = await c.get(u)
                j = r.json()
                if "loc" in j:
                    lat, lon = (float(x) for x in j["loc"].split(","))
                else:
                    lat, lon = float(j["latitude"]), float(j["longitude"])
                yield GpsFix(lat, lon, 0.0, None, accuracy_m=5000.0)
                break
            except Exception as e:
                log.info("ip location via %s failed: %s", u, e)
        else:
            raise ConnectionError("IP location unavailable")
    await asyncio.Event().wait()                  # a fixed position: nothing more to report


async def qt_source(source_name: str | None = None, interval_ms: int = 1000,
                    first_fix_s: float | None = None) -> FixStream:
    """The OS location service through Qt Positioning (needs the Qt event loop, i.e. the app)."""
    from PySide6.QtPositioning import QGeoPositionInfo, QGeoPositionInfoSource

    params = {"desktopId": "driveos"}             # GeoClue2 needs an app id
    try:
        src = (QGeoPositionInfoSource.createSource(source_name, params, None) if source_name
               else QGeoPositionInfoSource.createDefaultSource(params, None))
    except TypeError:
        src = (QGeoPositionInfoSource.createSource(source_name, None) if source_name
               else QGeoPositionInfoSource.createDefaultSource(None))
    if src is None:
        raise ConnectionError("no OS location service (Qt Positioning found no plugin: "
                              f"{QGeoPositionInfoSource.availableSources()})")
    q: asyncio.Queue = asyncio.Queue()

    def on_pos(info: QGeoPositionInfo):
        c = info.coordinate()
        if not c.isValid():
            q.put_nowait(False)
            return
        A = QGeoPositionInfo.Attribute
        sp = info.attribute(A.GroundSpeed) if info.hasAttribute(A.GroundSpeed) else None
        tr = info.attribute(A.Direction) if info.hasAttribute(A.Direction) else None
        acc = info.attribute(A.HorizontalAccuracy) if info.hasAttribute(A.HorizontalAccuracy) else None
        alt = c.altitude()
        q.put_nowait(GpsFix(c.latitude(), c.longitude(),
                            None if sp is None or math.isnan(sp) else float(sp),
                            None if tr is None or math.isnan(tr) else float(tr),
                            accuracy_m=None if acc is None or math.isnan(acc) else float(acc),
                            altitude_m=None if math.isnan(alt) else alt))

    def on_err(err):
        q.put_nowait(ConnectionError(f"OS location error: {err}"))

    src.positionUpdated.connect(on_pos)
    src.errorOccurred.connect(on_err)
    src.setUpdateInterval(interval_ms)
    src.startUpdates()
    log.info("Qt Positioning source: %s", src.sourceName())
    got = False
    try:
        while True:
            if not got and first_fix_s:
                try:
                    item = await asyncio.wait_for(q.get(), first_fix_s)
                except TimeoutError:
                    raise ConnectionError(f"OS location service gave no position in {first_fix_s:.0f}s") from None
            else:
                item = await q.get()
            if isinstance(item, Exception):
                raise item
            got = got or isinstance(item, GpsFix)
            yield item
    finally:
        src.stopUpdates()
        src.deleteLater()


async def _gpsd_reachable(host: str, port: int) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 0.6)
        w.close()
        return True
    except Exception:
        return False


def _qt_available() -> bool:
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtPositioning import QGeoPositionInfoSource

        return QCoreApplication.instance() is not None and bool(QGeoPositionInfoSource.availableSources())
    except Exception:
        return False


async def auto_source(state: dict) -> FixStream:
    """gpsd → OS location service → IP location, falling through when a source gives nothing."""
    if await _gpsd_reachable("127.0.0.1", 2947):
        state["label"] = "gpsd"
        async for r in gpsd_source():
            yield r
        return
    if _qt_available():
        state["label"] = "location service"
        try:
            async for r in qt_source(first_fix_s=15.0):
                yield r
            return
        except ConnectionError as e:
            log.info("auto GPS: %s — falling back to IP location", e)
    state["label"] = "IP location"
    async for r in ip_source():
        yield r


async def open_source(spec: str) -> tuple[str, FixStream]:
    """Resolve a ``gps_source`` spec into (label, stream)."""
    spec = (spec or "auto").strip()
    head = spec.split(":", 1)[0]
    kind = head.split("@", 1)[0].lower()
    rest = spec[len(kind):].lstrip(":")
    if kind == "auto":
        st: dict = {"label": "auto"}
        stream = auto_source(st)
        return st, stream  # type: ignore[return-value]
    if kind == "qt":
        return "location service", qt_source(rest or None)
    if kind == "gpsd":
        host, _, port = (rest or "127.0.0.1:2947").partition(":")
        return "gpsd", gpsd_source(host or "127.0.0.1", int(port or 2947))
    if kind == "serial":
        dev, _, baud = rest.partition("@")
        return f"GPS {Path(dev).name}", serial_source(dev, int(baud or 9600))
    if kind == "tcp":
        host, _, port = rest.rpartition(":")
        return "phone GPS", tcp_source(host or "127.0.0.1", int(port))
    if kind == "udp":
        return "phone GPS", udp_source(int(rest or 10110))
    if kind == "replay":
        f, _, sp = rest.rpartition("@") if "@" in rest else (rest, "", "")
        return "replay", replay_source(Path(f).expanduser() if f else None, float(sp or 1.0))
    if kind == "ip":
        return "IP location", ip_source()
    raise ValueError(f"unknown GPS source {spec!r} (auto, qt, gpsd, serial:DEV, tcp:HOST:PORT, udp:PORT, "
                     "replay[:FILE][@SPEED], ip)")


# ---------------------------------------------------------------------------------------------- vehicle
class GearInference:
    """Speed → P/D with hysteresis, so GPS jitter at a standstill doesn't flip the car into Drive."""

    def __init__(self, drive_mps: float = 2.0, drive_s: float = 2.0, park_mps: float = 0.8, park_s: float = 6.0):
        self.drive_mps, self.drive_s, self.park_mps, self.park_s = drive_mps, drive_s, park_mps, park_s
        self.gear = "P"
        self._since: float | None = None

    def update(self, speed: float, now: float) -> str:
        if self.gear == "P":
            cond = speed >= self.drive_mps
            hold = self.drive_s
        else:
            cond = speed <= self.park_mps
            hold = self.park_s
        if cond:
            if self._since is None:
                self._since = now
            if now - self._since >= hold:
                self.gear = "D" if self.gear == "P" else "P"
                self._since = None
        else:
            self._since = None
        return self.gear


class GpsVehicle(VehicleSource):
    STALE_S = 12.0

    def __init__(self, spec: str = "auto", *, outside_temp_f: float | None = None,
                 opener: Callable[[str], object] | None = None, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self.spec = spec
        self.outside_temp_f = outside_temp_f
        self.status = "off"                 # off | searching | fix | lost | unavailable
        self.label = ""                     # which backend is feeding us
        self.accuracy_m: float | None = None
        self.satellites: int | None = None
        self.error = ""
        self.fixes = 0
        self._opener = opener or open_source
        self._clock = clock
        self._gear = GearInference()
        self._last_fix_at = 0.0
        self._last: GpsFix | None = None
        self._last_t = 0.0
        self._speed = 0.0
        self._task: asyncio.Task | None = None
        self._watch: asyncio.Task | None = None
        self.status_listeners: list[Callable[[str], None]] = []
        self.bus_gear: str | None = None

    # -- control
    async def start(self) -> None:
        if self._task:
            return
        self._set_status("searching")
        self._task = asyncio.create_task(self._run(), name="gps")
        self._watch = asyncio.create_task(self._watchdog(), name="gps-watchdog")

    async def stop(self) -> None:
        for t in (self._task, self._watch):
            if t:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        self._task = self._watch = None
        self._set_status("off")

    def set_gear(self, gear: str) -> None:
        """HMI requests are ignored: a real car's gear comes from the vehicle bus (``set_bus_gear``) or speed."""

    def set_bus_gear(self, gear: str | None) -> None:
        """Real gear from the vehicle bus (a CAN bridge); ``None`` returns to inferring it from speed."""
        self.bus_gear = gear
        if self.state is not None and gear and gear != self.state.gear:
            self._publish(replace(self.state, gear=gear))

    # -- internals
    def _set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            for fn in list(self.status_listeners):
                with contextlib.suppress(Exception):
                    fn(s)

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                label, stream = await self._opener(self.spec)
                dyn = label if isinstance(label, dict) else None
                self.label = dyn["label"] if dyn else label
                self.error = ""
                log.info("GPS source: %s (%s)", self.label, self.spec)
                async for item in stream:
                    backoff = 1.0
                    if dyn:
                        self.label = dyn["label"]
                    if item is False:
                        if self.status == "fix":
                            self._set_status("lost")
                        elif self.status != "lost":
                            self._set_status("searching")
                        continue
                    self.on_fix(item)
                raise ConnectionError("GPS stream ended")
            except asyncio.CancelledError:
                raise
            except ValueError as e:                 # bad spec or unreadable file: retrying won't help
                self.error = str(e)
                log.error("GPS: %s", e)
                self._set_status("unavailable")
                return
            except Exception as e:
                self.error = str(e)
                log.warning("GPS (%s): %s — retrying in %.0fs", self.spec, e, backoff)
                self._set_status("unavailable" if self.fixes == 0 else "lost")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self.status == "fix" and self._clock() - self._last_fix_at > self.STALE_S:
                self._set_status("lost")
            s = self.state
            if s is not None:
                # keep inferring Park while the receiver is quiet at a standstill
                gear = self.bus_gear or self._gear.update(self._speed if self.status == "fix" else 0.0,
                                                          self._clock())
                if gear != s.gear:
                    self._publish(replace(s, gear=gear))

    def on_fix(self, fix: GpsFix) -> None:
        """Feed one fix (backends call this; tests call it directly)."""
        now = self._clock()
        self.fixes += 1
        self._last_fix_at = now
        self.accuracy_m = fix.accuracy_m
        self.satellites = fix.satellites
        prev, prev_t = self._last, self._last_t
        speed = fix.speed_mps
        track = fix.track
        if prev is not None:
            d = haversine(prev.point, fix.point)
            gap = max(now - prev_t, 1e-3)
            noise = max(fix.accuracy_m or 5.0, 3.0)
            if speed is None:
                speed = d / gap if d > noise else 0.0
            if track is None and d > noise:
                track = bearing(prev.point, fix.point)
        speed = max(0.0, float(speed or 0.0))
        # light smoothing — receivers report speed noisily at a standstill
        self._speed = speed if self._speed == 0.0 else 0.6 * speed + 0.4 * self._speed
        if self._speed < 0.3:
            self._speed = 0.0
        heading = track if (track is not None and self._speed > 0.8) else (self.state.heading if self.state else 0.0)
        gear = self._gear.update(self._speed, now)
        gear = self.bus_gear or gear
        self._last, self._last_t = fix, now
        self._set_status("fix")
        self._publish(VehicleState(fix.point, float(heading), self._speed, gear, self.outside_temp_f, "gps"))


class SwitchableVehicle(VehicleSource):
    """Simulator ⇄ live GPS at runtime. Only the active source's updates reach Nova.

    In GPS mode the real car does the driving: ``follow``/``resume``/``hold`` are no-ops and guidance
    tracks the live position. Switching back to the simulator starts it from the last GPS position.
    """

    def __init__(self, sim: VehicleSimulator, gps: GpsVehicle, mode: str = "sim"):
        super().__init__()
        self.sim = sim
        self.gps = gps
        self.mode = mode
        self.mode_listeners: list[Callable[[str], None]] = []
        sim.subscribe(lambda s: self._from("sim", s))
        gps.subscribe(lambda s: self._from("gps", s))
        self.state = (gps.state if mode == "gps" and gps.state else sim.state)

    def _from(self, who: str, s: VehicleState) -> None:
        if who == self.mode:
            self._publish(s)

    @property
    def active(self) -> VehicleSource:
        return self.gps if self.mode == "gps" else self.sim

    async def start(self) -> None:
        await self.sim.start()
        if self.mode == "gps":
            await self.gps.start()

    async def stop(self) -> None:
        await self.gps.stop()
        await self.sim.stop()

    async def set_mode(self, mode: str) -> None:
        mode = "gps" if mode == "gps" else "sim"
        if mode == self.mode:
            return
        if mode == "gps":
            self.sim.hold()
            self.sim.set_gear("P")
            self.mode = "gps"
            await self.gps.start()
            if self.gps.state is not None:
                self._publish(self.gps.state)
        else:
            last = self.gps.state
            await self.gps.stop()
            self.mode = "sim"
            if last is not None:
                self.sim.place_at(last.position, last.heading)
            elif self.sim.state is not None:
                self._publish(self.sim.state)
        for fn in list(self.mode_listeners):
            with contextlib.suppress(Exception):
                fn(self.mode)

    # -- driving controls go to the simulator only
    def follow(self, route: Route, stops_along_m: list[float] | None = None) -> None:
        if self.mode == "sim":
            self.sim.follow(route, stops_along_m)

    def resume(self) -> None:
        if self.mode == "sim":
            self.sim.resume()

    def hold(self) -> None:
        if self.mode == "sim":
            self.sim.hold()

    def set_gear(self, gear: str) -> None:
        if self.mode == "sim":
            self.sim.set_gear(gear)

    def place_at(self, point: GeoPoint, heading: float | None = None) -> None:
        """Picking a location on the map means simulating there — leave GPS mode first (caller awaits set_mode)."""
        self.sim.place_at(point, heading)

    def teleport(self, along_m: float, *, park: bool = True) -> None:
        if self.mode == "sim":
            self.sim.teleport(along_m, park=park)

    def inject_detour(self, offset_m: float = 120.0) -> None:
        if self.mode == "sim":
            self.sim.inject_detour(offset_m)

    @property
    def route(self):
        return self.sim.route


__all__ = ["GpsFix", "GpsVehicle", "SwitchableVehicle", "GearInference", "NmeaAssembler", "parse_nmea",
           "make_nmea", "parse_gpx", "open_source", "replay_source", "SAMPLE_DRIVE", "nmea_checksum_ok", "bearing"]
