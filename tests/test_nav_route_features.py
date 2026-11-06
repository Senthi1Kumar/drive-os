"""Track M wiring: reroute + ETA refresh in Nova, search along the route, "navigate to X", spoken turn
guidance, and the indoor demo drive. No live network."""

from __future__ import annotations

import asyncio
import logging
import types

import httpx
import pytest
import respx
from pydantic import SecretStr

from driveos.agent.intents import Context, IntentParser, is_approval
from driveos.config import MapboxConfig
from driveos.core.http import Http
from driveos.domain.models import GeoPoint
from driveos.merchants.mapbox_pois import MapboxPoiProvider
from driveos.nav.geo import decode_polyline, haversine, point_at, with_cumulative
from driveos.nav.guidance import CueTracker
from driveos.nav.mapbox import MapboxClient, parse_directions
from driveos.nav.router import OfflineRouter
from driveos.nav.vehicle import VehicleSimulator, VehicleState

from .agent_util import RecSpeaker
from .test_nav_guidance import TOKEN, A, mapbox_body

PARSER = IntentParser()


def parse(text, **ctx):
    return PARSER.parse(text, Context(**ctx))


# ---------------------------------------------------------------- intents
@pytest.mark.parametrize("text,cat,dest", [
    ("Can you search some pharmacies on our route to Orange Bowl?", "pharmacy", "orange bowl"),
    ("pharmacies on our route", "pharmacy", None),
    ("find a gas station along the way", "gas", None),
    ("any coffee on my route", "coffee", None),
])
def test_find_along_route_intent(text, cat, dest):
    i = parse(text)
    assert i.name == "find_along_route"
    assert i.slots["category"] == cat and i.slots.get("destination") == dest


@pytest.mark.parametrize("text,q", [
    ("take me to Orange Bowl", "orange bowl"),
    ("navigate to 123 Main St", "123 main st"),
    ("drive to the Miami airport please", "the miami airport"),
    ("Nova, take me to Hard Rock Stadium", "hard rock stadium"),
])
def test_navigate_to_intent(text, q):
    i = parse(text)
    assert i.name == "navigate_to" and i.slots["query"].replace(" please", "") == q.replace(" please", "")


@pytest.mark.parametrize("text", ["take me there", "drive to it", "take me to the first one", "navigate"])
def test_referential_drive_is_not_navigate_to(text):
    assert parse(text).name != "navigate_to"


def test_saved_place_still_wins():
    i = parse("take me to home", saved_places={"home": "home"})
    assert i.name == "navigate_saved"


@pytest.mark.parametrize("text", ["turn right", "take me to 123 Main St", "pharmacies on our route",
                                  "navigate to the airport"])
def test_checkout_turns_never_approve_or_navigate(text):
    i = parse(text, view="checkout", checkout_stage="choose")
    assert i.name not in ("approve", "navigate_to", "find_along_route")
    if text == "turn right":
        assert i.name not in ("drive", "navigate_saved", "stop_nav")
    assert not is_approval(text.lower())


# ---------------------------------------------------------------- guidance cues
def _route():
    return with_cumulative(parse_directions(mapbox_body()))


def test_cue_spoken_once_at_threshold():
    r = _route()
    t = CueTracker(r, along_m=0.0)
    assert t.poll(100.0, now=0.0) is None
    assert t.poll(400.0, now=1.0) is None
    first = t.poll(500.0, now=2.0)
    assert first and "200 feet" in first
    assert t.poll(505.0, now=3.0) is None
    assert t.poll(520.0, now=4.0) is None


def test_cue_held_while_blocked_and_released():
    r = _route()
    t = CueTracker(r, along_m=0.0)
    assert t.poll(500.0, now=0.0, blocked=True) is None
    assert t.poll(502.0, now=1.0, blocked=True) is None
    assert "200 feet" in (t.poll(504.0, now=2.0, blocked=False) or "")


def test_stale_pending_cue_is_dropped():
    r = _route()
    t = CueTracker(r, along_m=0.0, stale_s=10.0)
    assert t.poll(500.0, now=0.0, blocked=True) is None
    assert t.poll(505.0, now=30.0, blocked=False) is None      # too old
    t2 = CueTracker(r, along_m=0.0)
    assert t2.poll(500.0, now=0.0, blocked=True) is None
    assert t2.poll(700.0, now=1.0, blocked=False) in (None, "You will arrive soon")   # moved to the next step


def test_arrive_step_cues_are_not_spoken():
    r = _route()
    t = CueTracker(r, along_m=0.0)
    spoken = []
    for a in range(0, int(r.cumulative_m[-1]) + 1, 10):
        s = t.poll(float(a), now=float(a))
        if s:
            spoken.append(s)
    assert len(spoken) == len(set(spoken))
    assert not any("arrived" in s.lower() for s in spoken)


# ---------------------------------------------------------------- nova rig
class FakeRouter:
    name = "fake"

    def __init__(self):
        self.calls: list[dict] = []
        self.offline = OfflineRouter()

    async def route(self, origin, destination, *, via=None, destination_name="", via_names=None, fresh=False):
        self.calls.append({"origin": origin, "dest": destination, "via": list(via or []), "fresh": fresh})
        return await self.offline.route(origin, destination, via=via, destination_name=destination_name,
                                        via_names=via_names)


@pytest.fixture
async def rig(settings):
    from driveos.container import build_services

    settings.vehicle.simulator_time_scale = 1
    spk = RecSpeaker()
    s = build_services(settings, speaker=spk)
    router = FakeRouter()
    s.nova.router = router
    s.discovery.router = router
    clock = [1000.0]
    s.nova._clock = lambda: clock[0]
    await s.nova.start()
    s.nova.fake_clock = clock
    s.nova.spk = spk
    s.nova.fake_router = router
    yield s.nova
    await s.aclose()


async def _settle(nova):
    while nova._tasks:
        await asyncio.gather(*list(nova._tasks), return_exceptions=True)


def _state(p: GeoPoint, speed=10.0, source="sim"):
    return VehicleState(p, 0.0, speed, "D", 64.0, source)


async def _navigating(nova, north=0.02):
    base = nova._pos()
    dest = GeoPoint(lat=base.lat + north, lon=base.lon + north)
    await nova.navigate_to_point(dest, "Test Cafe")
    assert nova.nav.mode == "navigating"
    nova.fake_router.calls.clear()
    return dest


def _far_off(nova):
    pr = nova.progress
    p = pr.snapped
    return GeoPoint(lat=p.lat + 0.0012, lon=p.lon + 0.0012)    # ~170 m away


async def test_sim_vehicle_off_route_triggers_fresh_reroute(rig):
    from driveos.server.snapshot import _nav
    n = rig
    await _navigating(n)
    off = _far_off(n)
    for _ in range(2):
        n._on_vehicle(_state(off))
    await _settle(n)
    assert n.fake_router.calls == []                      # needs N consecutive fixes
    n._on_vehicle(_state(off))
    await _settle(n)
    assert len(n.fake_router.calls) == 1 and n.fake_router.calls[0]["fresh"] is True
    assert n.nav.reroutes == 1 and n.nav.rerouted_at > 0
    assert _nav(n)["reroute"] is True and _nav(n)["reroutes"] == 1
    assert any("erouting" in s for s in n.spk.said)


async def test_reroute_respects_cooldown(rig):
    n = rig
    await _navigating(n)
    for _ in range(3):
        n._on_vehicle(_state(_far_off(n)))
    await _settle(n)
    assert n.nav.reroutes == 1
    n.fake_clock[0] += 5
    for _ in range(4):
        n._on_vehicle(_state(_far_off(n)))
    await _settle(n)
    assert n.nav.reroutes == 1                            # cooling down
    n.fake_clock[0] += 30
    for _ in range(4):
        n._on_vehicle(_state(_far_off(n)))
    await _settle(n)
    assert n.nav.reroutes == 2


async def test_tracker_uses_configured_thresholds(rig):
    n = rig
    n.s.nav.off_route_fixes = 5
    await _navigating(n)
    assert n.tracker._off.fixes == 5 and n.tracker._off.threshold_m == n.s.nav.off_route_m


async def test_eta_refresh_on_schedule_is_silent(rig):
    n = rig
    await _navigating(n)
    spoken = len(n.spk.said)
    on = n.progress.snapped
    n._on_vehicle(_state(on))
    n.fake_clock[0] += 30
    n._on_vehicle(_state(on))
    await _settle(n)
    assert n.fake_router.calls == []
    n.fake_clock[0] += 70
    n._on_vehicle(_state(on))
    await _settle(n)
    assert len(n.fake_router.calls) == 1 and n.fake_router.calls[0]["fresh"] is True
    assert n.nav.reroutes == 0 and len(n.spk.said) == spoken


async def test_eta_refresh_reset_on_start_and_stop(rig):
    n = rig
    await _navigating(n)
    n._eta._last = 5.0
    await n.stop_nav()
    assert n._eta._last is None


# ---------------------------------------------------------------- spoken guidance in Nova
def _guided(n):
    r = _route()
    from driveos.agent.state import Nav
    n.nav = Nav(mode="navigating", dest_name="Cafe", dest=r.geometry[-1], route=r)
    n._on_vehicle(_state(A))
    n._set_route(r)
    return r


async def test_guidance_cue_spoken_once(rig):
    n = rig
    r = _guided(n)
    before = len(n.spk.said)
    for a in (300.0, 500.0, 505.0, 510.0):
        n._on_vehicle(_state(point_at(r, a)[0]))
        await _settle(n)
    cues = [s for s in n.spk.said[before:] if "200 feet" in s]
    assert len(cues) == 1


async def test_guidance_suppressed_during_checkout_and_biometrics(rig):
    n = rig
    r = _guided(n)
    n.shop.checkout = types.SimpleNamespace(stage="face")
    n._on_vehicle(_state(point_at(r, 500.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)
    n.shop.checkout = None
    n.bio_capture = True
    n._on_vehicle(_state(point_at(r, 505.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)
    n.bio_capture = False
    n.capture_busy = lambda: True
    n._on_vehicle(_state(point_at(r, 506.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)
    n.capture_busy = None
    n._on_vehicle(_state(point_at(r, 507.0)[0]))
    await _settle(n)
    assert sum("200 feet" in s for s in n.spk.said) == 1      # released while still fresh


async def test_guidance_not_while_speaking_or_listening(rig):
    n = rig
    r = _guided(n)
    n.voice.mode = "listening"
    n._on_vehicle(_state(point_at(r, 500.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)
    n.voice.mode = "idle"
    n._on_vehicle(_state(point_at(r, 502.0)[0]))
    await _settle(n)
    assert sum("200 feet" in s for s in n.spk.said) == 1


async def test_guidance_stale_cue_dropped(rig):
    n = rig
    r = _guided(n)
    n.voice.mode = "listening"
    n._on_vehicle(_state(point_at(r, 500.0)[0]))
    n.fake_clock[0] += 60
    n.voice.mode = "idle"
    n._on_vehicle(_state(point_at(r, 505.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)


# ---------------------------------------------------------------- search along the route (Mapbox params)
@pytest.fixture
async def http():
    h = Http()
    yield h
    await h.client.aclose()


def _features(n=3, base=A):
    return {"features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [base.lon + i * 1e-3,
                                                                                         base.lat + 2e-3]},
                          "properties": {"mapbox_id": f"id{i}", "name": f"Pharm {i}",
                                         "full_address": f"{i} Main St", "poi_category_ids": ["pharmacy"]}}
                         for i in range(n)]}


@respx.mock
async def test_search_along_route_request_params(http):
    respx.get(url__regex=r".*/searchbox/v1/list/category.*").mock(
        return_value=httpx.Response(200, json={"listItems": [{"canonical_id": "pharmacy"}]}))
    call = respx.get(url__regex=r".*/searchbox/v1/category/pharmacy.*").mock(
        return_value=httpx.Response(200, json=_features()))
    prov = MapboxPoiProvider(MapboxClient(MapboxConfig(token=SecretStr(TOKEN)), http))
    route = _route()
    places = await prov.search(near=A, route=route, category="pharmacy", query=None, limit=5)
    q = call.calls.last.request.url.params
    assert q["sar_type"] == "isochrone" and q["route_geometry"] == "polyline6"
    assert q["time_deviation"] and int(q["limit"]) >= 5
    assert len(decode_polyline(q["route"], 6)) >= 2
    assert "bbox" not in q
    assert [p.name for p in places] == ["Pharm 0", "Pharm 1", "Pharm 2"]


@respx.mock
async def test_search_along_route_falls_back_to_route_samples(http):
    respx.get(url__regex=r".*/searchbox/v1/list/category.*").mock(
        return_value=httpx.Response(200, json={"listItems": [{"canonical_id": "pharmacy"}]}))
    calls = []

    def answer(request):
        calls.append(request.url.params)
        if "sar_type" in request.url.params:
            return httpx.Response(400, json={"message": "no"})
        return httpx.Response(200, json=_features(2))

    respx.get(url__regex=r".*/searchbox/v1/category/pharmacy.*").mock(side_effect=answer)
    prov = MapboxPoiProvider(MapboxClient(MapboxConfig(token=SecretStr(TOKEN)), http))
    places = await prov.search(near=A, route=_route(), category="pharmacy", query=None, limit=5)
    sampled = [c for c in calls if "bbox" in c]
    assert len(sampled) >= 2 and places and TOKEN not in str([p.model_dump() for p in places])


@respx.mock
async def test_forward_geocode_request(http):
    call = respx.get(url__regex=r".*/searchbox/v1/forward.*").mock(
        return_value=httpx.Response(200, json=_features(1)))
    out = await MapboxClient(MapboxConfig(token=SecretStr(TOKEN)), http).forward("Orange Bowl", proximity=A)
    q = call.calls.last.request.url.params
    assert q["q"] == "Orange Bowl" and q["proximity"] and len(out) == 1


# ---------------------------------------------------------------- Nova: navigate to X, along route, stops
class FakeClient:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    async def forward(self, query, *, proximity, limit=3):
        self.queries.append(query)
        return self.hits


async def test_navigate_to_query_geocodes_and_starts(rig):
    n = rig
    dest = GeoPoint(lat=n._pos().lat + 0.01, lon=n._pos().lon + 0.01)
    n.router.client = FakeClient([{"id": "x", "name": "Orange Bowl", "location": dest, "address": "1 Bowl Way"}])
    await n.navigate_to_query("orange bowl")
    assert n.nav.mode == "navigating" and n.nav.dest_name == "Orange Bowl"
    assert haversine(n.nav.dest, dest) < 1
    assert n.router.client.queries == ["orange bowl"]


async def test_navigate_to_query_offline_uses_catalogue(rig):
    n = rig
    await n.navigate_to_query("plur coffee")
    assert n.nav.mode == "navigating" and "plur" in n.nav.dest_name.lower()


async def test_navigate_to_query_unknown_says_so(rig):
    n = rig
    await n.navigate_to_query("zzzz qqqq nowhere")
    assert n.nav.mode == "idle" and any("couldn't find" in s.lower() for s in n.spk.said)


async def test_find_along_route_scores_by_detour_when_navigating(rig):
    n = rig
    await _navigating(n, north=0.03)
    places = await n.find_along_route("coffee", None, speak=False)
    assert places and n.shop.view == "places" and "along your route" in n.shop.status
    assert all(p.distance_along_route_m is not None for p in places)
    detours = [p.detour_s for p in places]
    assert detours == sorted(detours)


async def test_find_along_route_in_overview_uses_route(rig):
    n = rig
    base = n._pos()
    await n.drive_to(n.home_nearby[0] if n.home_nearby else (await n.discover("coffee", None, speak=False))[0],
                     start=False)
    assert n.nav.mode == "overview"
    places = await n.find_along_route("coffee", None, speak=False)
    assert places and "along your route" in n.shop.status
    assert base


async def test_find_along_route_without_route_is_a_nearby_search(rig):
    n = rig
    places = await n.find_along_route("coffee", None, speak=False)
    assert places and "along your route" not in n.shop.status


async def test_find_along_route_names_destination(rig):
    n = rig
    await n.find_along_route("coffee", None, destination="plur coffee", speak=False)
    assert n.nav.route is not None and "plur" in n.nav.dest_name.lower()
    assert n.nav.mode in ("overview", "navigating")
    assert "along your route" in n.shop.status


async def test_add_stop_reroutes_via_the_place_and_keeps_it_on_reroute(rig):
    from driveos.server.snapshot import _nav
    n = rig
    await _navigating(n, north=0.03)
    places = await n.find_along_route("coffee", None, speak=False)
    stop = places[0]
    await n.add_stop(stop)
    assert n.fake_router.calls[-1]["via"] == [stop.location]
    assert n.fake_router.calls[-1]["fresh"] is True
    assert n.nav.via == [stop.location]
    assert _nav(n)["stops"][0]["name"] == stop.name
    for _ in range(3):
        n._on_vehicle(_state(_far_off(n)))
    await _settle(n)
    assert n.fake_router.calls[-1]["via"] == [stop.location]


# ---------------------------------------------------------------- harness tools
def test_new_tools_visible_and_payment_still_hidden():
    from driveos.agent.harness.tools import ToolRegistry
    names = {s.name for s in ToolRegistry().visible()}
    assert {"find_places_along_route", "navigate_to", "add_stop"} <= names
    assert not names & {"approve", "pay", "choose_auth", "place_order", "checkout"}


async def test_tools_call_through_to_nova(rig):
    from driveos.agent.harness.tools import SPECS, ToolEnv
    n = rig
    spec = {s.name: s for s in SPECS}
    dest = GeoPoint(lat=n._pos().lat + 0.01, lon=n._pos().lon + 0.01)
    n.router.client = FakeClient([{"id": "x", "name": "Orange Bowl", "location": dest, "address": ""}])
    env = ToolEnv(turn_id="t")
    a = spec["navigate_to"].args(query="orange bowl")
    res = await spec["navigate_to"].handler(n, a, env)
    assert res["ok"] and n.nav.mode == "navigating"
    a = spec["find_places_along_route"].args(category="coffee")
    res = await spec["find_places_along_route"].handler(n, a, env)
    assert res["ok"] and res["count"] >= 1
    a = spec["add_stop"].args(index=0)
    res = await spec["add_stop"].handler(n, a, env)
    assert res["ok"] and len(n.nav.via) == 1


# ---------------------------------------------------------------- spoken turns through the rules path
async def test_handle_text_routes_the_new_intents(rig):
    n = rig
    dest = GeoPoint(lat=n._pos().lat + 0.01, lon=n._pos().lon + 0.01)
    n.router.client = FakeClient([{"id": "x", "name": "Orange Bowl", "location": dest, "address": ""}])
    await n.handle_text("take me to Orange Bowl")
    assert n.nav.mode == "navigating"
    await n.handle_text("find coffee on our route")
    assert n.shop.view == "places" and "along your route" in n.shop.status
    await n.handle_text("add the first one as a stop")
    assert len(n.nav.via) == 1


async def test_no_user_text_in_logs_by_default(rig, caplog):
    n = rig
    caplog.set_level(logging.DEBUG)
    await n.handle_text("take me to Secret Hospital Wing")
    await n.handle_text("pharmacies on our route to Hidden Orange Bowl")
    low = caplog.text.lower()
    assert "secret hospital" not in low and "hidden orange" not in low


# ---------------------------------------------------------------- M7 simulated drive
def _sim(settings, **nav):
    for k, v in nav.items():
        setattr(settings.nav, k, v)
    from driveos.nav.vehicle import build_vehicle
    v = build_vehicle(settings.vehicle, settings.nav)
    return v.sim if hasattr(v, "sim") else v


def test_sim_speed_comes_from_nav_config(settings):
    sim = _sim(settings, sim_speed_kmh=36.0)
    assert sim.cruise == pytest.approx(10.0)
    from driveos.config import NavConfig
    assert NavConfig().sim_speed_kmh == 35.0


async def test_sim_follows_the_route_polyline(settings):
    sim = VehicleSimulator(settings.vehicle, settings.nav)
    sim.TICK_S = 0.01
    sim.scale = 40
    r = _route()
    sim.place_at(A)
    sim.follow(r)
    await sim.start()
    seen = []
    sim.subscribe(lambda s: seen.append(s.position))
    await asyncio.sleep(0.6)
    await sim.stop()
    assert len(seen) > 5
    from driveos.nav.geo import project
    assert max(project(r, p).offset_m for p in seen) < 3.0
    assert sim.along > 20


async def test_sim_detour_leaves_the_route_and_follow_clears_it(settings):
    sim = VehicleSimulator(settings.vehicle, settings.nav)
    sim.TICK_S = 0.01
    sim.scale = 40
    r = _route()
    sim.place_at(A)
    sim.follow(r)
    sim.inject_detour(150.0)
    await sim.start()
    await asyncio.sleep(0.3)
    from driveos.nav.geo import project
    off = project(r, sim.state.position).offset_m
    assert off > 100
    sim.follow(r)
    await asyncio.sleep(0.1)
    await sim.stop()
    assert project(r, sim.state.position).offset_m < 5


async def test_sim_detour_at_m_fires_once(settings):
    settings.nav.sim_detour_at_m = 100.0
    sim = VehicleSimulator(settings.vehicle, settings.nav)
    sim.TICK_S = 0.01
    sim.scale = 60
    r = _route()
    sim.place_at(A)
    sim.follow(r)
    await sim.start()
    await asyncio.sleep(0.8)
    from driveos.nav.geo import project
    assert project(r, sim.state.position).offset_m > 100
    sim.follow(r)                                  # a reroute re-targets the car; the detour does not repeat
    await asyncio.sleep(0.8)
    await sim.stop()
    assert project(r, sim.state.position).offset_m < 5


async def test_gps_still_takes_precedence(settings):
    from driveos.nav.vehicle import build_vehicle
    v = build_vehicle(settings.vehicle, settings.nav)
    assert v.mode == "sim"
    settings.vehicle.source = "gps"
    assert build_vehicle(settings.vehicle, settings.nav).mode == "gps"


async def test_vehicle_fixes_carry_a_rising_monotonic_stamp(settings):
    sim = VehicleSimulator(settings.vehicle, settings.nav)
    sim.TICK_S = 0.01
    sim.scale = 40
    sim.place_at(A)
    sim.follow(_route())
    stamps = []
    sim.subscribe(lambda s: stamps.append(s.t_ms))
    await sim.start()
    await asyncio.sleep(0.2)
    await sim.stop()
    assert len(stamps) > 5 and stamps[0] > 0
    assert stamps == sorted(stamps) and stamps[-1] > stamps[0]


def test_state_push_ignores_a_restamped_parked_car():
    from driveos.server.ws import UiServer
    a = {"vehicle": {"lat": 1.0, "lon": 2.0, "t_ms": 1000.0}, "tab": "nav"}
    b = {"vehicle": {"lat": 1.0, "lon": 2.0, "t_ms": 1100.0}, "tab": "nav"}
    c = {"vehicle": {"lat": 1.1, "lon": 2.0, "t_ms": 1100.0}, "tab": "nav"}
    assert UiServer._change_key(a) == UiServer._change_key(b)
    assert UiServer._change_key(a) != UiServer._change_key(c)
    assert a["vehicle"]["t_ms"] == 1000.0   # the state itself is untouched


async def test_navigation_cue_waits_for_driver_turn(rig):
    n = rig
    r = _guided(n)
    n.voice.continuous = True            # wake-once conversation: the mic is open between turns
    n.voice.mode = "listening"
    n.voice.user_speaking = True
    n._on_vehicle(_state(point_at(r, 500.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)       # queued, not spoken over the driver
    n.voice.user_speaking = False        # the turn boundary: still fresh, speaks once
    n._on_vehicle(_state(point_at(r, 502.0)[0]))
    await _settle(n)
    n._on_vehicle(_state(point_at(r, 504.0)[0]))
    await _settle(n)
    assert sum("200 feet" in s for s in n.spk.said) == 1


async def test_idle_continuous_listening_does_not_starve_cues(rig):
    n = rig
    r = _guided(n)
    n.voice.continuous = True
    n.voice.mode = "listening"
    n._on_vehicle(_state(point_at(r, 500.0)[0]))
    await _settle(n)
    assert sum("200 feet" in s for s in n.spk.said) == 1


async def test_stale_cue_is_dropped_after_a_long_driver_turn(rig):
    n = rig
    r = _guided(n)
    n.voice.continuous = True
    n.voice.mode = "listening"
    n.voice.user_speaking = True
    n._on_vehicle(_state(point_at(r, 500.0)[0]))
    n.fake_clock[0] += 60
    n.voice.user_speaking = False
    n._on_vehicle(_state(point_at(r, 505.0)[0]))
    await _settle(n)
    assert not any("200 feet" in s for s in n.spk.said)


# ---------------------------------------------------------------- route identity (no overview jump on re-plan)
async def test_refresh_and_reroute_keep_the_route_id_and_say_why(rig):
    from driveos.server.snapshot import _nav_map
    n = rig
    await _navigating(n)
    first = _nav_map(n)["route"]
    assert first["id"] > 0 and first["reason"] == "new"
    on = n.progress.snapped
    n._on_vehicle(_state(on))
    n.fake_clock[0] += 100
    n._on_vehicle(_state(on))
    await _settle(n)
    refreshed = _nav_map(n)["route"]
    assert refreshed["id"] == first["id"] and refreshed["reason"] == "refresh"
    n.fake_clock[0] += 100
    for _ in range(3):
        n._on_vehicle(_state(_far_off(n)))
    await _settle(n)
    rerouted = _nav_map(n)["route"]
    assert rerouted["id"] == first["id"] and rerouted["reason"] == "reroute"


async def test_a_new_destination_gets_a_new_route_id(rig):
    from driveos.server.snapshot import _nav_map
    n = rig
    await _navigating(n)
    a = _nav_map(n)["route"]["id"]
    await _navigating(n, north=0.03)
    b = _nav_map(n)["route"]
    assert b["id"] != a and b["reason"] == "new"


async def test_same_destination_drive_command_does_not_restart_the_route(rig):
    n = rig
    await _navigating(n)
    route_id = n.nav.route_id
    n.shop.place = n.nav.dest_place
    await n.drive_here()
    assert n.nav.route_id == route_id
    assert n.nav.mode == "navigating"
    assert n.fake_router.calls == []
