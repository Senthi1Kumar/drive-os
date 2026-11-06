from __future__ import annotations

import pytest

from driveos.domain.models import GeoPoint
from driveos.merchants.local_catalog import LOS_GATOS_CENTER, LocalCatalog
from driveos.wallet.auth import OtpService, PhoneNotificationDelivery, SimulatedFaceVerifier
from tests.conftest import requires_dataset


# ---------------------------------------------------------------- catalogue
@requires_dataset
def test_catalog_loads_fixtures_and_dataset(catalog):
    names = {m["name"] for m in catalog.merchants.values()}
    assert {"Plur Coffee", "Heritage Pub & Chop House", "Orange Bowl"} <= names
    # the crawl listed one deli three times; one per address remains
    assert sum(1 for n in names if n.startswith("CRUST")) == 1
    assert len(catalog.merchants) > 300
    assert len(catalog.menu("r3-plur-coffee").items) > 20


@requires_dataset
def test_dataset_variations_become_options(catalog):
    multi = next(m for m in catalog.merchants.values() if m["source"] == "catalog" and any(
        i["modifier_groups"] for c in m["categories"] for i in c["items"]))
    menu = catalog.menu(multi["id"])
    item = next(i for i in menu.items if i.modifier_groups)
    g = item.modifier_groups[0]
    assert len(g.options) > 1 and len(g.default_ids()) == 1
    assert all(o.price_delta.amount >= 0 for o in g.options)


def test_venues_cover_errands(catalog):
    near = catalog.near(LOS_GATOS_CENTER, 5000)
    tags = {t for m in near for t in m["tags"]}
    assert {"gas", "ev", "store", "pharmacy", "bar", "coffee", "sandwich"} <= tags


def test_geocode_refinement_is_cached(tmp_path):
    c = LocalCatalog(tmp_path, include_dataset=False)

    async def geocode(addr):
        return GeoPoint(lat=37.25, lon=-121.94)

    import asyncio

    n = asyncio.run(c.refine(["r3-plur-coffee"], geocode))
    assert n == 1 and not c.merchant("r3-plur-coffee")["approx"]
    c2 = LocalCatalog(tmp_path, include_dataset=False)
    assert c2.merchant("r3-plur-coffee")["location"] == {"lat": 37.25, "lon": -121.94}


async def test_local_search_by_item(demo):
    places = await demo.search(near=LOS_GATOS_CENTER, route=None, category="food", query="latte", limit=6)
    hit = [p for p in places if p.extra.get("matched_item")]
    assert any(p.name == "Plur Coffee" for p in hit) or any(p.name == "Plur Coffee" for p in places)


async def test_ordering_off_refuses_quotes(catalog):
    from driveos.domain.errors import OrderingUnavailable
    from driveos.domain.models import Cart
    from driveos.merchants.base import make_line
    from driveos.merchants.local import LocalMerchantProvider

    live = LocalMerchantProvider(catalog, ordering="off")
    menu = await live.get_menu("r3-plur-coffee")
    with pytest.raises(OrderingUnavailable):
        await live.quote(Cart(merchant_id="r3-plur-coffee", lines=[make_line("l", menu.items[0], {}, 1)]))
    places = await live.search(near=LOS_GATOS_CENTER, route=None, category="gas", query=None, limit=5)
    assert places == []   # demo venues are not offered in live mode


# ---------------------------------------------------------------- one-time codes
async def test_otp_issue_and_verify():
    seen = {}
    svc = OtpService(PhoneNotificationDelivery(lambda code, purpose: seen.update(code=code, purpose=purpose)))
    ch = await svc.issue("+14085550117", "$40.00 at Summit Fuel")
    assert ch.destination == "•••• 0117" and len(seen["code"]) == 6
    assert not svc.verify(ch.challenge_id, "000000" if seen["code"] != "000000" else "111111").success
    ok = svc.verify(ch.challenge_id, seen["code"])
    assert ok.success and ok.tier.value == "T2"
    assert not svc.verify(ch.challenge_id, seen["code"]).success   # single use


async def test_otp_locks_after_three_misses():
    seen = {}
    svc = OtpService(PhoneNotificationDelivery(lambda code, purpose: seen.update(code=code)))
    ch = await svc.issue("+14085550117", "x")
    wrong = "123456" if seen["code"] != "123456" else "654321"
    details = [svc.verify(ch.challenge_id, wrong).detail for _ in range(3)]
    assert details == ["mismatch:2", "mismatch:1", "locked"]
    assert svc.verify(ch.challenge_id, seen["code"]).detail == "locked"


async def test_otp_expires(monkeypatch):
    from datetime import timedelta

    from driveos.wallet import auth as auth_mod

    svc = OtpService(PhoneNotificationDelivery(None))
    ch = await svc.issue("+14085550117", "x")
    real = auth_mod.utcnow
    monkeypatch.setattr(auth_mod, "utcnow", lambda: real() + timedelta(minutes=6))
    assert svc.verify(ch.challenge_id, "123456").detail == "expired"


# ---------------------------------------------------------------- Face ID
async def test_face_sim_match_and_reject():
    f = SimulatedFaceVerifier(scan_s=0.05)
    assert (await f.verify(2)).success
    import asyncio

    f2 = SimulatedFaceVerifier(scan_s=5)
    t = asyncio.ensure_future(f2.verify(2))
    await asyncio.sleep(0.05)
    f2.report(False)
    r = await t
    assert not r.success and r.detail == "no-match"
