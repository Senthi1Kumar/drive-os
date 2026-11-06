"""Place covers: no repeated picture inside one visible list, stable across snapshots, cuisine-aware, and the
junk-POI filter that keeps offices out of the shop lists."""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import SecretStr

from driveos.config import MapboxConfig
from driveos.core.http import Http
from driveos.domain.models import GeoPoint
from driveos.images import audit, matcher
from driveos.images.matcher import (
    CUISINE_COVERS,
    assign_covers,
    cover_pool,
    cuisine_for,
    key_for_place,
)
from driveos.merchants.mapbox_pois import MapboxPoiProvider, clean_features, is_junk
from driveos.nav.mapbox import MapboxClient

FIXTURES = Path(__file__).parent / "fixtures"
ASSETS = Path(__file__).resolve().parents[1] / "hmi_lvgl" / "assets" / "food"
CATEGORIES = ("coffee", "tea", "bakery", "sandwich", "food", "bar", "gas", "ev", "pharmacy", "store", "hotel")

NAMES = {
    "coffee": ["Blue Door Coffee", "Rise Espresso", "Philz", "Verve", "Bean Counter", "Kaffee House", "Drip Lab", "Moka"],
    "tea": ["Boba Guys", "Tea Garden", "Sharetea", "Matcha Bar", "Chatime", "Leaf and Cup", "Teaspoon", "Plum Tea"],
    "bakery": ["Torigin Bakery", "Sweet Crumb", "La Boulangerie", "Donut Wheel", "Bagel Barn", "Pastry Lane", "Rolls", "Crust"],
    "sandwich": ["Jimmy's Deli", "Subs and More", "The Sandwich Spot", "Ike's", "Deli Delicious", "Panini Place", "Bahn", "Hoagie"],
    "food": ["Mei Palace", "The Local Diner", "Bistro 5", "Casa Lupita", "Prime Steakhouse", "Corner Table", "Harvest", "Olive"],
    "bar": ["Bar Underdog", "Scotty's", "The Wine Room", "The Pro", "Hidden Tap", "The Rose and Crown", "Dive 42", "Taproom"],
    "gas": ["Valero", "Shell", "Chevron", "Chevron Express", "76", "Arco", "Mobil", "Sinclair"],
    "ev": ["Ionic Plaza", "Garage L2", "City Hall Chargers", "Mall Chargers", "Park and Plug", "Library", "Depot", "Hub"],
    "pharmacy": ["Walgreens", "CVS Pharmacy", "Rite Aid", "Town and Country Rx", "Lucky Drugs", "Bayshore Pharmacy", "Medicine Shoppe", "Rx 4"],
    "store": ["Safeway", "Whole Foods", "Trader Joe's", "Lucky", "Ranch 99", "Mollie Stone's", "Corner Mart", "Draeger's"],
    "hotel": ["Garden Inn", "Hotel Avante", "Stanford Suites", "Cardinal Hotel", "Rosewood", "Crowne", "Lodge 7", "Motel 9"],
}


def place(pid: str, name: str, category: str, **extra) -> SimpleNamespace:
    return SimpleNamespace(id=pid, name=name, category=category, photo_url=None, extra=dict(extra))


def realistic_list(category: str, seed: int, n: int = 6) -> list[SimpleNamespace]:
    rnd = random.Random(seed)
    names = rnd.sample(NAMES[category], n)
    return [place(f"mapbox:{rnd.getrandbits(48):012x}", nm, category) for nm in names]


# ------------------------------------------------------------------------------ pools and assets
@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_pool_has_eight_distinct_existing_scenes(category):
    pool = cover_pool(category)
    assert len(set(pool)) >= 8, pool
    assert all((ASSETS / f"{k}.jpg").is_file() for k in pool), category
    assert all((ASSETS / f"{k}.jpg").stat().st_size <= 30 * 1024 for k in pool)


def test_every_cover_and_cuisine_asset_is_registered_and_exists():
    covers = {k for k in matcher.ALL_KEYS if k.startswith("cover_")}
    for pool in matcher._COVERS.values():
        assert set(pool) <= covers
    for key in CUISINE_COVERS.values():
        assert key in covers and (ASSETS / f"{key}.jpg").is_file()
    assert audit.place_cover_stats()["missing_assets"] == []


def test_pool_scenes_are_different_images():
    from hashlib import sha1
    for category in CATEGORIES:
        digests = {sha1((ASSETS / f"{k}.jpg").read_bytes()).hexdigest() for k in cover_pool(category)}
        assert len(digests) == len(cover_pool(category)), category


# ------------------------------------------------------------------------------ no repeats inside one list
@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("seed", range(25))
def test_no_cover_repeats_in_a_six_place_list(category, seed):
    places = realistic_list(category, seed)
    picks = assign_covers(places)
    assert len({picks[p.id] for p in places}) == 6, (category, seed, picks)


def test_the_complaint_lists_no_longer_share_pictures():
    bars = [place(f"mapbox:{n}", n, "bar") for n in
            ("Bar Underdog", "Scotty's", "The Wine Room", "The Pro", "Hidden Tap", "The Rose and Crown")]
    gas = [place(f"mapbox:{n}", n, "gas") for n in ("Valero", "Shell", "Chevron", "Chevron 2", "76", "Arco")]
    for lst in (bars, gas):
        assert len(set(assign_covers(lst).values())) == 6


def test_a_mixed_home_strip_has_no_repeats():
    for seed in range(40):
        rnd = random.Random(seed)
        cats = rnd.sample(CATEGORIES, 8)
        strip = [place(f"mapbox:h{seed}-{i}", f"Place {i}", c) for i, c in enumerate(cats)]
        picks = assign_covers(strip)
        assert len({picks[p.id] for p in strip}) == 8, (seed, cats)


def test_the_first_visible_places_stay_distinct_in_a_long_list():
    long = [place(f"mapbox:long{i}", f"Gas {i}", "gas") for i in range(14)]
    picks = assign_covers(long)
    assert len({picks[p.id] for p in long[:8]}) == 8


def test_a_place_with_a_real_photo_is_left_to_the_store_not_the_cover_logic():
    from driveos.images.view import place_image
    assert place_image(None, place("x", "Cafe", "coffee")) == {}


# ------------------------------------------------------------------------------ stability
def test_assignment_is_stable_while_the_list_is_unchanged_and_order_independent():
    places = realistic_list("food", 7)
    first = assign_covers(places)
    assert assign_covers(places) == first
    assert assign_covers(list(reversed(places))) == first
    matcher._COVER_CACHE.clear()
    assert assign_covers(places) == first                        # pure function of the list, not of the cache


def test_cover_cache_is_bounded():
    for i in range(matcher._COVER_CACHE_MAX + 20):
        assign_covers([place(f"c{i}", "Cafe", "coffee")])
    assert len(matcher._COVER_CACHE) <= matcher._COVER_CACHE_MAX


def test_snapshot_places_keep_their_pictures_between_builds(tmp_path):
    from PIL import Image

    from driveos.images.store import ImageStore
    from driveos.images.view import cover_picks, place_image
    gen = tmp_path / "gen"
    gen.mkdir()
    for pool in matcher._COVERS.values():
        for k in pool:
            Image.new("RGB", (32, 32), (hash(k) % 255, 40, 40)).save(gen / f"{k}.jpg", "JPEG")
    store = ImageStore(tmp_path / "root", generated_dir=gen)
    places = [SimpleNamespace(id=p.id, name=p.name, category=p.category, photo_url=None, extra={})
              for p in realistic_list("bar", 3)]
    a = [place_image(store, p, cover_picks(places)) for p in places]
    b = [place_image(store, p, cover_picks(places)) for p in places]
    assert a == b and len({x["img"] for x in a}) == 6


# ------------------------------------------------------------------------------ cuisine
@pytest.mark.parametrize("name,cats,kind,expected", [
    ("Sushi Tomi", [], "", "sushi"), ("Bangkok Pho House", [], "", "thai"), ("Siam Thai Kitchen", [], "", "thai"),
    ("Tandoori Nights", [], "", "indian"), ("Pizzeria Delfina", [], "", "pizza"), ("Smash Burgers", [], "", "burger"),
    ("Taqueria Lupita", [], "", "mexican"), ("Habibi Hummus", [], "", "mediterranean"),
    ("Mediterranean Wraps", [], "", "mediterranean"), ("Fresh Creamery", [], "", "icecream"),
    ("Superfoods Cafe", [], "", "salad"), ("Wing Stop Chicken", [], "", "chicken"),
    ("Anything", ["thai_restaurant"], "", "thai"), ("Anything", ["ice_cream_shop"], "", "icecream"),
    ("Anything", [], "indian", "indian"), ("Corner Table", [], "", ""),
])
def test_cuisine_mapping(name, cats, kind, expected):
    assert cuisine_for(name, kind, cats) == expected


def test_a_cuisine_restaurant_gets_its_cuisine_cover_and_stays_unique_in_the_list():
    lst = [place("mapbox:a", "Sushi Tomi", "food"), place("mapbox:b", "Siam Thai", "food"),
           place("mapbox:c", "Habibi Hummus", "food"), place("mapbox:d", "Sushi Hana", "food"),
           place("mapbox:e", "Corner Table", "food"), place("mapbox:f", "Olive", "food", poi_categories=["pizza_restaurant"])]
    picks = assign_covers(lst)
    assert picks["mapbox:b"] == CUISINE_COVERS["thai"] and picks["mapbox:c"] == CUISINE_COVERS["mediterranean"]
    assert picks["mapbox:f"] == CUISINE_COVERS["pizza"]
    assert {picks["mapbox:a"], picks["mapbox:d"]} >= {CUISINE_COVERS["sushi"]}      # one gets the sushi cover
    assert len(set(picks.values())) == 6


def test_cuisine_only_applies_to_restaurants_and_single_place_lookup_uses_it():
    assert key_for_place("food", "", "p1", "Sushi Tomi") == "cover_rest_sushi"
    assert key_for_place("food", "pizza", "p1") == "cover_rest_pizza"
    assert key_for_place("gas", "", "p1", "Pizza Gas") in cover_pool("gas")
    assert key_for_place("food", "", "p1", "Corner Table") in cover_pool("food")
    assert key_for_place("ev").startswith("cover_ev") and key_for_place("hotel").startswith("cover_hotel")


# ------------------------------------------------------------------------------ audit
def test_audit_place_lists_report_no_repeats_and_a_threshold():
    rep = audit.place_list_stats()
    assert rep["lists"] >= 200 and rep["max_repeat"] == 1 and rep["min_pool"] >= 8
    assert not rep["short_pools"] and not rep["missing_assets"]


# ------------------------------------------------------------------------------ junk POI filter
def fixture_features() -> dict:
    return json.loads((FIXTURES / "mapbox_pharmacy_palo_alto.json").read_text())


@pytest.mark.parametrize("name,junk", [
    ("Charesela Technologies Inc", True), ("Stanford Biotech Labs", True), ("Summit Capital Partners", True),
    ("Peninsula Holdings LLC", True), ("Acme Consulting", True), ("Orbit Software", True), ("Delta Corporation", True),
    ("Walgreens", False), ("Rite Aid", False), ("Joe's Pizza LLC", False), ("Shell Gas Station Inc", False),
    ("Corner Pharmacy Inc", False), ("Bar Underdog", False), ("Quick Mart LLC", False), ("Town and Country Rx", False),
])
def test_junk_names(name, junk):
    assert is_junk(name, ["pharmacy"], None, ["pharmacy"]) is junk


def test_a_branded_chain_in_the_searched_category_is_not_junk():
    assert is_junk("Acme Holdings", ["pharmacy"], "Acme", ["pharmacy"]) is False
    assert is_junk("Acme Holdings", ["office"], "Acme", ["pharmacy"]) is True


@pytest.fixture
async def http():
    h = Http()
    yield h
    await h.client.aclose()


@respx.mock
async def test_pharmacy_search_drops_junk_and_duplicates_and_backfills_to_six(http):
    respx.get(url__regex=r".*/searchbox/v1/list/category.*").mock(return_value=httpx.Response(200, json={
        "listItems": [{"canonical_id": "pharmacy"}]}))
    route = respx.get(url__regex=r".*/searchbox/v1/category/pharmacy.*").mock(
        return_value=httpx.Response(200, json=fixture_features()))
    prov = MapboxPoiProvider(MapboxClient(MapboxConfig(token=SecretStr("pk.test")), http))
    res = await prov.search(near=GeoPoint(lat=37.4419, lon=-122.1430), route=None, category="pharmacy", query=None,
                            limit=6)
    names = [p.name for p in res]
    assert "Charesela Technologies Inc" not in names and "Stanford Biotech Labs" not in names
    assert "Peninsula Holdings LLC" not in names and "Summit Capital Partners" not in names
    assert names.count("Walgreens") == 1                                   # duplicate 5 m away
    assert names.count("CVS Pharmacy") == 2                                # 30 m duplicate dropped, 2 km one kept
    assert len(res) >= 6 and len({p.merchant_id for p in res}) == len(res)
    assert int(route.calls.last.request.url.params["limit"]) > 6            # asked for extra candidates to back-fill
    assert all(p.extra["poi_categories"] for p in res)


def test_duplicates_need_the_same_name_and_to_be_close():
    feats = [{"id": "1", "name": "Joe's Cafe", "location": GeoPoint(lat=37.0, lon=-122.0), "categories": []},
             {"id": "2", "name": "Joe's  CAFE", "location": GeoPoint(lat=37.0002, lon=-122.0), "categories": []},   # ~22 m
             {"id": "3", "name": "Joe's Cafe", "location": GeoPoint(lat=37.01, lon=-122.0), "categories": []},      # 1.1 km
             {"id": "4", "name": "Other Cafe", "location": GeoPoint(lat=37.0, lon=-122.0), "categories": []}]
    assert [f["id"] for f in clean_features(feats, "coffee")] == ["1", "3", "4"]
