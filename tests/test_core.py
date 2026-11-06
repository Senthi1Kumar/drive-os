from __future__ import annotations

from decimal import Decimal

import pytest

from driveos.domain.models import GeoPoint, Route
from driveos.domain.money import Money
from driveos.nav.geo import (
    cumulative,
    decode_polyline,
    densify,
    encode_polyline,
    haversine,
    point_at,
    project,
    splice_stop,
    with_cumulative,
)
from driveos.nav.tracker import RouteTracker


# ---------------------------------------------------------------- money
def test_money_arithmetic_and_format():
    a = Money.from_decimal("4.75")
    b = Money.from_decimal("1.15")
    assert (a + b).amount == 590
    assert (a * 3).amount == 1425
    assert (a - b).format() == "$3.60"
    assert Money(amount=625).apply_rate(Decimal("0.1025")).amount == 64  # 64.06 -> 64 (half-up)
    assert Money.zero() < a


def test_money_rejects_mixed_currency():
    with pytest.raises((ValueError, TypeError)):
        Money(amount=1, currency="USD") + Money(amount=1, currency="EUR")


# ---------------------------------------------------------------- geo
FREMONT = [GeoPoint(lat=37.5347, lon=-121.9949), GeoPoint(lat=37.5410, lon=-121.9949),
           GeoPoint(lat=37.5410, lon=-121.9800), GeoPoint(lat=37.5563, lon=-121.9800)]


def _route(points=FREMONT) -> Route:
    pts = densify(points, 20)
    c = cumulative(pts)
    return with_cumulative(Route(geometry=pts, distance_m=c[-1], duration_s=c[-1] / 15))


@pytest.mark.parametrize("precision", [5, 6])
def test_polyline_roundtrip(precision):
    enc = encode_polyline(FREMONT, precision)
    dec = decode_polyline(enc, precision)
    tol = 10 ** -precision
    assert all(abs(a.lat - b.lat) <= tol and abs(a.lon - b.lon) <= tol for a, b in zip(FREMONT, dec, strict=True))


def test_polyline_known_vector():
    # Google's documented example (precision 5)
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5)
    assert (round(pts[0].lat, 3), round(pts[0].lon, 3)) == (38.5, -120.2)
    assert (round(pts[2].lat, 3), round(pts[2].lon, 3)) == (43.252, -126.453)


def test_haversine_one_degree_lat():
    d = haversine(GeoPoint(lat=0, lon=0), GeoPoint(lat=1, lon=0))
    assert 111_000 < d < 111_400


def test_project_and_point_at_agree():
    r = _route()
    pos, _, _ = point_at(r, 900)
    assert abs(project(r, pos).along_m - 900) < 2


def test_splice_stop_passes_through_stop():
    r = _route()
    stop = GeoPoint(lat=37.5412, lon=-121.9870)
    r2, added = splice_stop(r, stop)
    assert min(haversine(p, stop) for p in r2.geometry) < 1
    assert added > 0
    assert abs(r2.cumulative_m[-1] - (r.cumulative_m[-1] + added)) < 5


def test_tracker_progress_monotonic():
    r = _route()
    t = RouteTracker(r)
    a = t.update(point_at(r, 300)[0], 15).remaining_m
    b = t.update(point_at(r, 1200)[0], 15).remaining_m
    assert b < a
    assert 0 <= t.update(point_at(r, 1200)[0], 15).fraction <= 1
