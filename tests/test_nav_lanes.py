"""Lane guidance: bannerInstructions[].sub.components of type lane -> RouteStep.banner_lanes -> nav.step.lanes."""

from __future__ import annotations

from driveos.nav.guidance import lanes_at
from driveos.nav.mapbox import parse_directions
from tests.test_nav_guidance import mapbox_body


def _lane(directions, active, active_direction=None):
    c = {"text": "", "type": "lane", "directions": directions, "active": active}
    if active_direction:
        c["active_direction"] = active_direction
    return c


def _sub(*lanes):
    return {"text": "", "type": "lane", "components": list(lanes)}


def body_with_lanes():
    body = mapbox_body()
    first = body["routes"][0]["legs"][0]["steps"][0]
    first["bannerInstructions"] = [
        {"distanceAlongGeometry": 690, "primary": {"text": "Oak Ave", "type": "turn", "modifier": "right"}},
        {"distanceAlongGeometry": 400, "primary": {"text": "Oak Ave", "type": "turn", "modifier": "right"},
         "sub": _sub(_lane(["left"], False), _lane(["straight"], False),
                     _lane(["straight", "right"], True, "right"), _lane(["right"], True, "right"))},
    ]
    return body


def test_lane_components_are_parsed_per_banner():
    r = parse_directions(body_with_lanes())
    banners = r.steps[0].banner_lanes
    assert [b.at_m for b in banners] == [690, 400]
    assert banners[0].lanes == []
    lanes = banners[1].lanes
    assert [ln.indications for ln in lanes] == [["left"], ["straight"], ["straight", "right"], ["right"]]
    assert [ln.valid for ln in lanes] == [False, False, True, True]
    assert [ln.active_indication for ln in lanes] == [None, None, "right", "right"]


def test_steps_without_lane_data_have_none():
    r = parse_directions(mapbox_body())
    assert all(s.banner_lanes == [] or all(not b.lanes for b in s.banner_lanes) for s in r.steps)


def test_non_lane_sub_components_are_ignored():
    body = mapbox_body()
    body["routes"][0]["legs"][0]["steps"][0]["bannerInstructions"][0]["sub"] = {
        "text": "Exit 4", "type": "exit", "components": [{"text": "Exit 4", "type": "exit"}]}
    assert parse_directions(body).steps[0].banner_lanes[0].lanes == []


def test_lanes_at_picks_the_banner_in_force():
    step = parse_directions(body_with_lanes()).steps[0]
    assert lanes_at(step, 600) == []   # far from the turn: only the lane-less banner applies
    got = lanes_at(step, 350)
    assert len(got) == 4
    assert got[2] == {"indications": ["straight", "right"], "valid": True, "active": True, "active_indication": "right"}
    assert got[0] == {"indications": ["left"], "valid": False, "active": False}
    assert lanes_at(None, 10) == []


# ---------------------------------------------------------------- nav.step.lanes on the wire
from tests.test_nav_route_features import _navigating, rig  # noqa: E402,F401  (the nova rig)


async def test_snapshot_carries_lanes_for_the_upcoming_maneuver(rig):  # noqa: F811
    from driveos.domain.models import BannerLanes, Lane
    from driveos.server.snapshot import _nav_map

    await _navigating(rig)
    pr = rig.progress
    cur = rig.nav.route.steps[pr.step_index]
    assert "lanes" not in _nav_map(rig)["step"]
    cur.banner_lanes = [BannerLanes(at_m=1e9, lanes=[
        Lane(indications=["left"], valid=False),
        Lane(indications=["straight", "right"], valid=True, active_indication="right")])]
    lanes = _nav_map(rig)["step"]["lanes"]
    assert lanes == [{"indications": ["left"], "valid": False, "active": False},
                     {"indications": ["straight", "right"], "valid": True, "active": True,
                      "active_indication": "right"}]
