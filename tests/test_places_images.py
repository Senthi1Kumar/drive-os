"""Real nearby places with sample menus, thumbnails, location switching and the snapshot fields."""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from driveos.config import LocationConfig, MapboxConfig
from driveos.core.http import Http
from driveos.domain.models import GeoPoint
from driveos.images.matcher import KEYS, key_for, key_for_place
from driveos.images.store import ImageStore, Thumb
from driveos.merchants.local_catalog import LocalCatalog
from driveos.merchants.mapbox_pois import MapboxPoiProvider
from driveos.merchants.sample_menus import KINDS, categories_for, kind_for, merchant_record
from driveos.nav.location import LocationManager, LocationPolicy
from driveos.nav.mapbox import MapboxClient

PALO_ALTO = GeoPoint(lat=37.4419, lon=-122.1430)
ASSETS = Path(__file__).resolve().parents[1] / "hmi_lvgl" / "assets" / "food"


def jpeg(w=400, h=300, color=(200, 30, 30)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


# ------------------------------------------------------------------ keyword matcher
@pytest.mark.parametrize("name,key", [
    ("Caramel Latte", "latte"), ("Iced Americano", "iced_coffee"), ("Cold Brew", "cold_brew"),
    ("Flat White", "flat_white"), ("Masala Chai", "masala_chai"), ("Chai Latte", "chai_latte"),
    ("Matcha Latte", "matcha"), ("Iced Tea", "iced_tea_glass"), ("Chicken Caesar Salad", "caesar_salad"),
    ("Margherita Pizza", "pizza_margherita"), ("Pepperoni Pizza", "pizza_pepperoni"),
    ("Garden Veggie Pizza", "pizza_veggie"), ("Pepperoni Slice", "pizza_slice"),
    ("Classic Cheeseburger", "burger_classic"), ("Bacon Burger", "burger_bacon"),
    ("Veggie Burger", "burger_veggie"), ("Pub Burger", "burger"), ("Onion Rings", "onion_rings"),
    ("Chicken Quesadilla", "quesadilla"), ("Loaded Nachos", "nachos"), ("Rice and Beans", "rice_beans"),
    ("Butter Chicken", "butter_chicken"), ("Paneer Tikka Masala", "paneer_tikka"), ("Dal Tadka", "dal_tadka"),
    ("Garlic Naan", "naan"), ("Mango Lassi", "smoothie"), ("Pork Gyoza", "dumplings"),
    ("California Roll", "california_roll"), ("Spicy Tuna Roll", "spicy_tuna_roll"),
    ("Dragon Roll", "dragon_roll"), ("Salmon Nigiri", "salmon_nigiri"), ("Sushi Combo", "sushi_combo"),
    ("Tonkotsu Ramen", "ramen"), ("Street Tacos", "taco"),
    ("Turkey Avocado Sandwich", "turkey_sandwich"), ("Italian Sub", "italian_sub"), ("Classic BLT", "blt"),
    ("Roast Chicken Plate", "roast_chicken"),
    ("Crowler · house IPA 32 oz", "beer"), ("Non-alcoholic Beer", "na_beer"),
    ("Allergy Relief · 30", "allergy_relief"), ("Sunscreen SPF 50", "sunscreen"),
    ("Hand Sanitizer", "sanitizer"), ("Protein Bars · 4", "protein_bars"),
    ("Top-up · 15 min", "ev_topup"), ("Reserve a stall", "ev_stall"), ("Premium 91", "fuel_premium"),
    ("Sparkling water 12-pack", "sparkling_can"),
    ("Ibuprofen 200 mg · 50", "pain_reliever"), ("USB-C charging cable", "phone_charger"),
    ("Fast charge · 30 min", "ev_charging"), ("Regular 87", "fuel_pump"), ("Spring water 1L", "water_bottle"),
    ("Blueberry Muffin", "muffin"), ("Sesame Bagel", "bagel"), ("Smoked wings", "chicken_wings"),
])
def test_matcher_names(name, key):
    assert key_for(name) == key


def test_matcher_falls_back_by_category_and_section():
    assert key_for("Chef's Special", "coffee") == "cappuccino"
    assert key_for("Chef's Special", "pharmacy") == "pain_reliever"
    assert key_for("Zzz", "", "Cold Drinks") == "drink_generic"
    assert key_for("Zzz") == "food_generic"
    assert key_for_place("food").startswith("cover_restaurant")
    assert key_for_place("food", "pizza") == "cover_rest_pizza"
    assert key_for_place("ev").startswith("cover_ev")
    assert key_for_place("hotel").startswith("cover_hotel")      # hotels have a cover now, not just an icon


def test_every_key_has_a_generated_asset_under_size():
    missing = [k for k in KEYS if not (ASSETS / f"{k}.jpg").is_file()]
    assert not missing, missing
    assert all((ASSETS / f"{k}.jpg").stat().st_size <= 30 * 1024 for k in KEYS)
    assert (ASSETS / "LICENSE.md").is_file()


# ------------------------------------------------------------------ sample menus: every distinct product its own image
def _items_by_kind():
    return {kind: [(section, it["name"]) for section, items in fn() for it in items]
            for kind, fn in KINDS.items()}


def test_sample_items_do_not_share_an_image_beyond_their_alternates_within_a_kind():
    from driveos.domain.models import MenuItem
    from driveos.images.matcher import assign_menu
    problems = []
    for kind, fn in KINDS.items():
        items = [MenuItem.model_validate(i) for _, cat in fn() for i in cat]
        picks = assign_menu(items, kind)
        by_key: dict[str, list[str]] = {}
        for it in items:
            by_key.setdefault(picks[it.id], []).append(it.name)
        problems += [f"{kind}: {k} -> {v}" for k, v in by_key.items() if len(v) > 2]
    assert not problems, problems


def test_every_sample_item_image_key_has_a_generated_asset():
    missing = {key_for(name, kind, section) for kind, entries in _items_by_kind().items()
               for section, name in entries}
    missing = {k for k in missing if not (ASSETS / f"{k}.jpg").is_file()}
    assert not missing, sorted(missing)


def test_sample_disclosure_is_exactly_sample_only_in_the_lvgl_source():
    src = Path(__file__).resolve().parents[1] / "hmi_lvgl" / "src"
    texts = {p.name: p.read_text() for p in list(src.glob("*.c")) + list((src / "screens").glob("*.c"))}
    joined = "\n".join(texts.values())
    assert "Sample menu" not in joined
    assert "real menu or prices" not in joined
    assert "Sample Only" in texts["home.c"]
    assert "Sample Only" in texts["shop.c"]


# ------------------------------------------------------------------ image store
async def test_store_downsizes_caches_with_private_modes(tmp_path):
    calls = []

    async def fetch(url):
        calls.append(url)
        return jpeg(640, 480)

    ready = []
    st = ImageStore(tmp_path / "images", fetch=fetch, on_ready=lambda: ready.append(1))
    url = "https://items-images-production-f.squarecdn.com/x.jpg"
    assert st.thumb(url) is None                      # first call queues, never blocks
    await st.settle()
    t = st.thumb(url)
    assert isinstance(t, Thumb) and t.small.startswith("img/") and ready
    with Image.open(tmp_path / "images" / t.small) as im:
        assert im.size == (160, 160)
    with Image.open(tmp_path / "images" / t.large) as im:
        assert im.size == (320, 320)
    for rel in (t.small, t.large):
        assert stat.S_IMODE(os.stat(tmp_path / "images" / rel).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "images" / "img").st_mode) == 0o700
    st.thumb(url)
    st.thumb(url)
    assert calls == [url]


async def test_store_rejects_non_https_and_remembers_failures(tmp_path):
    n = 0

    async def fetch(url):
        nonlocal n
        n += 1
        raise ValueError("boom")

    st = ImageStore(tmp_path / "i", fetch=fetch)
    assert st.thumb("http://insecure/x.jpg") is None and st.thumb(None) is None
    st.thumb("https://h/x.jpg")
    await st.settle()
    st.thumb("https://h/x.jpg")
    await st.settle()
    assert n == 1                                     # not retried inside the back-off window


async def test_store_size_cap_evicts_oldest(tmp_path):
    async def fetch(url):
        return jpeg(300, 300)

    st = ImageStore(tmp_path / "i", fetch=fetch, max_bytes=1)
    for i in range(3):
        st.thumb(f"https://h/{i}.jpg")
        await st.settle()
    assert st.size_bytes() <= 1 or len(list((tmp_path / "i" / "img").glob("*.jpg"))) < 6
    big = ImageStore(tmp_path / "j", fetch=fetch, max_bytes=10_000)
    for i in range(12):
        big.thumb(f"https://h/{i}.jpg")
        await big.settle()
    assert big.size_bytes() <= 10_000


def test_store_resamples_generated_images(tmp_path):
    gen = tmp_path / "gen"
    gen.mkdir()
    (gen / "latte.jpg").write_bytes(jpeg(512, 512))
    st = ImageStore(tmp_path / "i", generated_dir=gen)
    t = st.generated("latte")
    assert t == Thumb("food/latte_160.jpg", "food/latte_320.jpg")
    assert (tmp_path / "i" / t.large).is_file()
    assert st.generated("nope") is None


# ------------------------------------------------------------------ sample menus
def test_kind_inference():
    assert kind_for("food", "Tony's Pizzeria", ["restaurant"]) == "pizza"
    assert kind_for("food", "X", ["indian_restaurant"]) == "indian"
    assert kind_for("coffee", "Chai Wala", []) == "tea"
    assert kind_for("coffee", "Blue Bottle", ["coffee"]) == "coffee"
    assert kind_for("gas", "Pizza Gas", []) == "fuel"          # a gas search never yields a pizza menu
    assert kind_for("ev", "x", []) == "ev" and kind_for("pharmacy", "x", []) == "pharmacy"
    assert kind_for("hotel", "Hilton", ["hotel"]) is None


def test_every_kind_has_plausible_menu_with_modifiers():
    for kind in KINDS:
        cats = categories_for(kind)
        items = [i for c in cats for i in c["items"]]
        assert items and all(0 < i["base_price"]["amount"] < 10_000 for i in items), kind
        assert all(i["refs"].get("sample") == "1" for i in items)
    coffee = [i for c in categories_for("coffee") for i in c["items"] if i["name"] == "Latte"][0]
    assert {g["id"] for g in coffee["modifier_groups"]} == {"size", "temp", "milk", "syrup"}


def test_merchant_record_is_flagged_sample():
    f = {"id": "abc", "name": "Joe's Pizza", "address": "1 Main", "location": PALO_ALTO, "categories": ["pizza_restaurant"]}
    rec = merchant_record(f, "food")
    assert rec["sample"] is True and rec["sample_kind"] == "pizza" and rec["source"] == "mapbox"
    assert merchant_record({**f, "categories": ["hotel"]}, "hotel") is None


# ------------------------------------------------------------------ Mapbox category search + cache
def _feature(i, name, cats=("coffee",), dlat=0.001):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [PALO_ALTO.lon, PALO_ALTO.lat + dlat * i]},
            "properties": {"mapbox_id": f"id{i}", "name": name, "full_address": f"{i} University Ave",
                           "poi_category_ids": list(cats)}}


@respx.mock
async def test_category_request_params_cache_and_sample_places(tmp_path):
    cats = respx.get("https://api.mapbox.com/search/searchbox/v1/list/category").mock(
        return_value=httpx.Response(200, json={"listItems": [{"canonical_id": c} for c in
                                                             ("coffee", "cafe", "gas_station", "charging_station")]}))
    route = respx.get("https://api.mapbox.com/search/searchbox/v1/category/coffee").mock(
        return_value=httpx.Response(200, json={"features": [_feature(2, "Far Cafe"), _feature(1, "Near Cafe")]}))
    respx.get("https://api.mapbox.com/search/searchbox/v1/category/cafe").mock(
        return_value=httpx.Response(200, json={"features": []}))
    now = [0.0]
    http = Http()
    catalog = LocalCatalog(include_dataset=False)
    prov = MapboxPoiProvider(MapboxClient(MapboxConfig(token="tok"), http), catalog=catalog, sample_menus=True,
                             cache_ttl_s=100, clock=lambda: now[0])
    places = await prov.search(near=PALO_ALTO, route=None, category="coffee", query=None, limit=6)
    assert cats.called
    q = dict(route.calls[0].request.url.params)
    assert q["proximity"] == f"{PALO_ALTO.lon:.6f},{PALO_ALTO.lat:.6f}" and "bbox" in q and int(q["limit"]) >= 6
    assert [p.name for p in places] == ["Near Cafe", "Far Cafe"]            # nearest first
    p = places[0]
    assert p.provider == "local" and p.extra["sample_menu"] is True and p.extra["orderable"] is True
    menu = catalog.menu(p.merchant_id)
    assert menu.items and menu.item("c-latte")
    n = route.call_count
    await prov.search(near=GeoPoint(lat=PALO_ALTO.lat + 0.0005, lon=PALO_ALTO.lon), route=None, category="coffee",
                      query=None, limit=6)
    assert route.call_count == n                                              # same ~500 m cell: cached
    now[0] = 500.0
    await prov.search(near=PALO_ALTO, route=None, category="coffee", query=None, limit=6)
    assert route.call_count == n + 1                                          # TTL elapsed
    await http.aclose()


@respx.mock
async def test_without_sample_menus_places_are_directions_only():
    respx.get("https://api.mapbox.com/search/searchbox/v1/list/category").mock(
        return_value=httpx.Response(200, json={"listItems": [{"canonical_id": "hotel"}, {"canonical_id": "lodging"}]}))
    respx.get("https://api.mapbox.com/search/searchbox/v1/category/hotel").mock(
        return_value=httpx.Response(200, json={"features": [_feature(1, "Grand Hotel", ("hotel",))]}))
    respx.get("https://api.mapbox.com/search/searchbox/v1/category/lodging").mock(
        return_value=httpx.Response(200, json={"features": []}))
    http = Http()
    prov = MapboxPoiProvider(MapboxClient(MapboxConfig(token="tok"), http), catalog=LocalCatalog(include_dataset=False),
                             sample_menus=True)
    (p,) = await prov.search(near=PALO_ALTO, route=None, category="hotel", query=None, limit=4)
    assert p.provider == "mapbox" and p.extra["orderable"] is False and not p.extra["sample_menu"]
    await http.aclose()


# ------------------------------------------------------------------ location source switching
class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_policy_hysteresis_and_dwell():
    c = Clock()
    cfg = LocationConfig(acquire_s=5, lose_s=20, min_dwell_s=10)
    p = LocationPolicy(cfg, live=False, clock=c)
    assert p.update(True) is None                      # fix just appeared
    c.t += 4
    assert p.update(True) is None
    c.t += 2
    assert p.update(True) is True                      # held 6 s: live
    c.t += 1
    assert p.update(False) is None
    c.t += 19
    assert p.update(False) is None                     # 19 s without a fix: not yet
    c.t += 2
    assert p.update(False) is False                    # 21 s: back to simulated
    c.t += 1
    assert p.update(True) is None
    c.t += 4
    assert p.update(True) is None                      # held 4 s: not enough
    c.t += 5
    assert p.update(True) is True                      # held 9 s and 10 s since the last switch


def test_policy_dwell_holds_back_a_quick_return():
    c = Clock()
    p = LocationPolicy(LocationConfig(acquire_s=1, lose_s=1, min_dwell_s=30), live=False, clock=c)
    p.update(True)
    c.t += 2
    assert p.update(True) is True
    p.update(False)
    c.t += 2
    assert p.update(False) is None                     # lost for 2 s but we switched 2 s ago
    c.t += 30
    assert p.update(False) is False


def test_policy_blinking_fix_never_switches():
    c = Clock()
    p = LocationPolicy(LocationConfig(acquire_s=5, lose_s=20, min_dwell_s=30), live=False, clock=c)
    for i in range(40):
        c.t += 2
        assert p.update(i % 2 == 0) is None            # fix present only 2 s at a time


class FakeGps:
    status = "searching"
    spec = "serial:/dev/ttyUSB9"
    accuracy_m = 5.0
    started = 0

    async def start(self):
        self.started += 1


class FakeVehicle:
    def __init__(self):
        self.mode = "sim"
        self.gps = FakeGps()


class FakeNova:
    def __init__(self, vehicle):
        self.vehicle = vehicle
        self.location_label = "Los Gatos, California"
        self.calls = []

    def _pos(self):
        return PALO_ALTO

    async def set_location(self, point, label):
        self.calls.append(("loc", label))
        self.location_label = label

    async def set_vehicle_mode(self, mode):
        self.calls.append(("mode", mode))
        self.vehicle.mode = mode
        if mode == "sim":
            self.location_label = "Somewhere, CA"


async def test_manager_starts_simulated_in_palo_alto_then_goes_live_and_back():
    c = Clock()
    v = FakeVehicle()
    nova = FakeNova(v)
    cfg = LocationConfig(acquire_s=5, lose_s=20, min_dwell_s=10)
    refreshed = []

    async def refresh(pos):
        refreshed.append(pos)

    m = LocationManager(nova, v, cfg, refresh=refresh, clock=c)
    await m.start()
    m._task.cancel()
    assert nova.calls[0] == ("loc", "Palo Alto, California")
    assert m.source().label == "Simulated · Palo Alto" and not m.source().live and v.gps.started
    v.gps.status = "fix"
    await m.tick()
    c.t += 6
    await m.tick()
    assert v.mode == "gps" and m.source().label == "Live GPS"
    v.gps.status = "lost"
    await m.tick()
    c.t += 25
    await m.tick()
    assert v.mode == "sim" and nova.calls[-1] == ("loc", "Palo Alto, California")
    assert refreshed


async def test_manager_stops_auto_when_user_switches_mode():
    c = Clock()
    v = FakeVehicle()
    m = LocationManager(FakeNova(v), v, LocationConfig(), clock=c)
    await m.start()
    m._task.cancel()
    v.mode = "gps"                                      # the user flipped the Nav switch
    await m.tick()
    assert m.auto is False


# ------------------------------------------------------------------ snapshot
async def test_snapshot_fields_for_sample_place_menu_and_home(settings, tmp_path):
    from driveos.container import build_services
    from driveos.discovery.home import interleave
    from driveos.server.snapshot import build_state

    gen = tmp_path / "gen"
    gen.mkdir()
    for k in ("latte", "latte_2", "cappuccino", "cappuccino_2", "food_generic", "cover_cafe_1", "cover_cafe_2"):
        (gen / f"{k}.jpg").write_bytes(jpeg(256, 256))
    settings.images.generated_dir = gen
    s = build_services(settings)
    nova = s.nova
    rec = merchant_record({"id": "z1", "name": "Sample Cafe", "address": "1 Main", "location": PALO_ALTO,
                           "categories": ["coffee"]}, "coffee")
    s.registry.get("local").catalog.add_external(rec)
    place = s.registry.get("local").catalog.place(rec, provider="local")
    place.extra.update(distance_m=300.0, orderable=True, has_menu=True, sample_menu=True)
    nova.shop.places = [place]
    nova.home_feed.places = [place]
    await nova.open_place(place_id=place.id, speak=False)
    from driveos.viewmodel import build_view
    st = build_state(nova, build_view(nova), level=0.0, toast=None, update={})
    assert st["shop"]["sample"] is True and st["shop"]["places"][0]["sample"] is True
    latte = next(i for sec in st["shop"]["menu"] for i in sec["items"] if i["name"] == "Latte")
    assert latte["img"] in ("food/latte_160.jpg", "food/latte_2_160.jpg")
    assert latte["img_lg"] == latte["img"].replace("_160", "_320")
    assert st["shop"]["places"][0]["img"].startswith("food/")
    assert st["home"]["places"][0]["id"] == place.id and st["home"]["places"][0]["sample"] is True
    assert st["vehicle"]["source"] in ("sim", "gps") and "source_text" in st["vehicle"]
    assert place.id in {p.id for p in nova.home_nearby}                        # open_place can resolve it
    assert interleave([[place], [place]], 8) == [place]
    await s.aclose()


async def test_coarse_fix_does_not_count_as_live():
    c = Clock()
    v = FakeVehicle()
    m = LocationManager(FakeNova(v), v, LocationConfig(acquire_s=1, min_dwell_s=1), clock=c)
    await m.start()
    m._task.cancel()
    v.gps.status, v.gps.accuracy_m = "fix", 5000.0      # IP location
    for _ in range(5):
        c.t += 2
        await m.tick()
    assert v.mode == "sim"
    v.gps.accuracy_m = 8.0
    c.t += 2
    await m.tick()
    c.t += 2
    await m.tick()
    assert v.mode == "gps"
