"""M3 + M4: Directions mapping (steps, icons, voice, congestion), polyline6, off-route/reroute gating,
origin cache, token hygiene and the additive ``nav`` snapshot fields. No live network."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx
from pydantic import SecretStr

from driveos.config import MapboxConfig
from driveos.core.http import Http
from driveos.domain.errors import ProviderUnavailable
from driveos.domain.models import GeoPoint
from driveos.nav.geo import decode_polyline, encode_polyline, haversine, with_cumulative
from driveos.nav.guidance import OffRouteDetector, RerouteGate, maneuver_icon
from driveos.nav.mapbox import MapboxClient, parse_directions
from driveos.nav.router import MapboxRouter, OfflineRouter
from driveos.nav.tracker import RouteTracker

A = GeoPoint(lat=37.2358, lon=-121.9624)
B = GeoPoint(lat=37.2420, lon=-121.9624)
C = GeoPoint(lat=37.2420, lon=-121.9500)
TOKEN = "pk.SECRETTOKEN123"


def _geom():
    # ~690 m north, then ~1.1 km east, densified a little so congestion has several segments
    pts = [A, GeoPoint(lat=37.2390, lon=-121.9624), B, GeoPoint(lat=37.2420, lon=-121.9560), C]
    return pts


def mapbox_body(geom=None, legs=1):
    geom = geom or _geom()
    n = len(geom) - 1
    cong = ["low", "moderate", "heavy", "severe"][:n] + ["low"] * max(0, n - 4)
    steps = [
        {"name": "Main St", "distance": 690, "duration": 60,
         "maneuver": {"type": "depart", "location": [A.lon, A.lat], "instruction": "Drive north on Main St"},
         "bannerInstructions": [{"distanceAlongGeometry": 690, "primary": {
             "text": "Oak Ave", "type": "turn", "modifier": "right"}}],
         "voiceInstructions": [
             {"distanceAlongGeometry": 690, "announcement": "Drive north on Main St for half a mile"},
             {"distanceAlongGeometry": 200, "announcement": "In 200 feet, turn right onto Oak Ave"}]},
        {"name": "Oak Ave", "distance": 1000, "duration": 90,
         "maneuver": {"type": "turn", "modifier": "right", "location": [B.lon, B.lat],
                      "instruction": "Turn right onto Oak Ave"},
         "bannerInstructions": [{"distanceAlongGeometry": 1000, "primary": {
             "text": "Your destination", "type": "arrive", "modifier": "right"}}],
         "voiceInstructions": [{"distanceAlongGeometry": 300, "announcement": "You will arrive soon"}]},
        {"name": "Oak Ave", "distance": 0, "duration": 0,
         "maneuver": {"type": "arrive", "modifier": "right", "location": [C.lon, C.lat],
                      "instruction": "You have arrived"}},
    ]
    return {"code": "Ok", "waypoints": [{"name": "Main St"}, {"name": "Cafe"}], "routes": [{
        "geometry": encode_polyline(geom, 6), "distance": 1690.0, "duration": 150.0,
        "legs": [{"summary": "Main St, Oak Ave", "steps": steps, "annotation": {"congestion": cong}}]}]}


# ---------------------------------------------------------------- polyline + mapping
def test_polyline6_round_trip():
    pts = _geom()
    back = decode_polyline(encode_polyline(pts, 6), precision=6)
    assert len(back) == len(pts)
    assert all(haversine(a, b) < 0.5 for a, b in zip(pts, back, strict=True))


def test_parse_directions_maps_steps_icons_voice():
    r = parse_directions(mapbox_body(), names=["", "Cafe"])
    assert [s.icon for s in r.steps] == ["depart", "turn_right", "arrive"]
    assert r.steps[0].banner == "Oak Ave"
    assert r.steps[0].voice[0].at_m == 690 and "half a mile" in r.steps[0].voice[0].text
    assert r.steps[0].voice[1].at_m == 200
    assert r.polyline6 == encode_polyline(_geom(), 6)


def test_congestion_count_is_points_minus_one():
    r = parse_directions(mapbox_body())
    assert len(r.congestion) == len(r.geometry) - 1
    assert r.congestion == "lmhs"


def test_congestion_missing_or_short_is_padded_unknown():
    body = mapbox_body()
    body["routes"][0]["legs"][0].pop("annotation")
    r = parse_directions(body)
    assert r.congestion == "u" * (len(r.geometry) - 1)
    body = mapbox_body()
    body["routes"][0]["legs"][0]["annotation"]["congestion"] = ["heavy"]
    assert parse_directions(body).congestion == "huuu"


def test_maneuver_icons():
    assert maneuver_icon("turn", "left") == "turn_left"
    assert maneuver_icon("turn", "slight right") == "slight_right"
    assert maneuver_icon("turn", "sharp left") == "sharp_left"
    assert maneuver_icon("turn", "uturn") == "uturn"
    assert maneuver_icon("fork", "left") == "fork_left"
    assert maneuver_icon("on ramp", "right") == "ramp_right"
    assert maneuver_icon("roundabout", "right") == "roundabout"
    assert maneuver_icon("merge", None) == "merge"
    assert maneuver_icon("continue", "straight") == "straight"
    assert maneuver_icon("arrive", "left") == "arrive"
    assert maneuver_icon("weird", None) == "straight"


# ---------------------------------------------------------------- request + token hygiene
@pytest.fixture
async def http():
    h = Http()
    yield h
    await h.client.aclose()


def _client(http):
    return MapboxClient(MapboxConfig(token=SecretStr(TOKEN)), http)


@respx.mock
async def test_directions_request_params(http):
    call = respx.get(url__regex=r".*/directions/v5/mapbox/driving-traffic/.*").mock(
        return_value=httpx.Response(200, json=mapbox_body()))
    await _client(http).directions([A, C])
    q = call.calls.last.request.url.params
    assert q["annotations"] == "congestion,distance,duration"
    assert q["geometries"] == "polyline6" and q["overview"] == "full"
    assert q["steps"] == q["banner_instructions"] == q["voice_instructions"] == "true"


@respx.mock
async def test_token_never_logged_on_error(http, caplog):
    caplog.set_level(logging.DEBUG)
    respx.get(url__regex=r".*/directions/.*").mock(side_effect=httpx.ConnectError(f"boom {TOKEN}"))
    with pytest.raises(ProviderUnavailable) as ei:
        await _client(http).directions([A, C])
    assert TOKEN not in str(ei.value)
    assert TOKEN not in caplog.text
    respx.get(url__regex=r".*/directions/.*").mock(
        return_value=httpx.Response(401, json={"code": "InvalidToken", "message": f"bad {TOKEN}"}))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ProviderUnavailable) as ei:
            await _client(http).directions([A, C])
    assert TOKEN not in str(ei.value) and TOKEN not in caplog.text


async def test_router_fallback_log_has_no_token(caplog):
    class Boom:
        async def directions(self, pts, names=None):
            raise ProviderUnavailable(f"x {TOKEN}")

    caplog.set_level(logging.DEBUG)
    r = MapboxRouter(Boom(), fallback=OfflineRouter())  # type: ignore[arg-type]
    route = await r.route(A, C)
    assert route.summary == "estimated"
    assert TOKEN not in caplog.text


# ---------------------------------------------------------------- navigation responses stay uncached
async def test_mapbox_directions_are_requested_for_every_route():
    calls = []

    class Fake:
        async def directions(self, pts, names=None):
            calls.append(pts[0])
            return parse_directions(mapbox_body())

    r = MapboxRouter(Fake())  # type: ignore[arg-type]
    await r.route(A, C, destination_name="Cafe")
    near = GeoPoint(lat=A.lat + 0.0003, lon=A.lon)
    await r.route(near, C, destination_name="Cafe")
    await r.route(near, C, destination_name="Cafe", fresh=True)
    assert len(calls) == 3
    assert not hasattr(r, "_cache")


# ---------------------------------------------------------------- offline router
async def test_offline_router_has_same_fields():
    r = await OfflineRouter().route(A, C, destination_name="Cafe")
    assert r.congestion == "u" * (len(r.geometry) - 1)
    assert r.polyline6 and len(decode_polyline(r.polyline6)) == len(r.geometry)
    assert all(s.icon for s in r.steps)
    assert r.steps[-1].icon == "arrive"


# ---------------------------------------------------------------- off-route + reroute gate
def test_off_route_needs_consecutive_fixes():
    d = OffRouteDetector(threshold_m=40, fixes=3)
    assert not d.observe(10)
    assert not d.observe(55)
    assert not d.observe(60)
    assert d.observe(70)
    d.reset()
    assert not d.observe(55) and not d.observe(5) and not d.observe(55) and not d.observe(55)


def test_tracker_flags_off_route_after_n_fixes():
    r = with_cumulative(parse_directions(mapbox_body()))
    t = RouteTracker(r, off_route_m=40, off_route_fixes=3)
    on = GeoPoint(lat=37.2380, lon=-121.9624)
    assert not t.update(on).off_route
    off = GeoPoint(lat=37.2380, lon=-121.9624 + 0.0008)  # ~70 m east of the road
    flags = [t.update(off).off_route for _ in range(3)]
    assert flags == [False, False, True]
    assert t.update(on).off_route is False


def test_reroute_gate_cooldown_and_inflight():
    g = RerouteGate(cooldown_s=30)
    assert g.allow(now=100.0)
    g.start()
    assert not g.allow(now=101.0)      # in flight
    g.done(now=110.0)
    assert not g.allow(now=120.0)      # cooling down
    assert g.allow(now=141.0)
    assert g.count == 1


def test_eta_refresh_due():
    from driveos.nav.guidance import EtaRefresh
    e = EtaRefresh(interval_s=90)
    assert not e.due(now=0.0)  # armed at first call
    assert not e.due(now=60.0)
    assert e.due(now=91.0)
    assert not e.due(now=100.0)
    assert EtaRefresh(interval_s=0).due(now=1e9) is False  # disabled


# ---------------------------------------------------------------- snapshot
async def test_snapshot_nav_fields(settings):
    from driveos.server import build_backend

    settings.vehicle.simulator_time_scale = 5
    b = build_backend(settings, voice=False, host="127.0.0.1", port=0)
    await b.start()
    try:
        nova = b.services.nova
        base = nova._pos()
        dest = GeoPoint(lat=base.lat + 0.01, lon=base.lon + 0.01)
        await nova.navigate_to_point(dest, "Test Cafe")
        from driveos.server.snapshot import _nav
        n = _nav(nova)
        assert n["active"] and n["dest"] == "Test Cafe" and n["mode"] == "navigating"  # unchanged keys
        assert n["route"]["polyline6"] and len(decode_polyline(n["route"]["polyline6"])) > 2
        assert len(n["congestion"]) == len(decode_polyline(n["route"]["polyline6"])) - 1
        assert set(n["step"]) >= {"icon", "text", "dist_m"} and n["step"]["dist_m"] >= 0
        assert set(n["puck"]) == {"lat", "lon", "heading"}
        assert n["dest_point"]["name"] == "Test Cafe" and abs(n["dest_point"]["lat"] - dest.lat) < 1e-6
        assert n["eta_s"] > 0 and n["remaining_m"] > 0
        assert n["reroute"] is False and n["reroutes"] == 0
        nova.nav.reroutes, nova.nav.rerouted_at = 1, __import__("time").monotonic()
        n = _nav(nova)
        assert n["reroute"] is True and n["reroutes"] == 1
        assert "next_step" not in n or set(n["next_step"]) == {"icon", "text"}
    finally:
        await b.stop()


async def test_snapshot_idle_has_no_route(settings):
    from driveos.server import build_backend
    from driveos.server.snapshot import _nav

    b = build_backend(settings, voice=False, host="127.0.0.1", port=0)
    await b.start()
    try:
        n = _nav(b.services.nova)
        assert not n["active"] and "route" not in n and "step" not in n
    finally:
        await b.stop()
