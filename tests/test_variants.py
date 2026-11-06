"""Item variants: brand sub-products, priced choices, voice ordering of both, and the option image in the snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from driveos.agent.intents import Context, IntentParser
from driveos.domain.models import GeoPoint, Menu, MenuItem
from driveos.merchants import sample_menus, venues
from driveos.merchants.base import make_line

ASSETS = Path(__file__).resolve().parents[1] / "hmi_lvgl" / "assets" / "food"
p = IntentParser()
ALL_KINDS = {k: [MenuItem.model_validate(i) for _, cat in [(c["name"], c["items"]) for c in sample_menus.categories_for(k)]
                 for i in cat] for k in sample_menus.KINDS}


def _menu(kind: str) -> Menu:
    return Menu(merchant_id=f"t-{kind}", items=ALL_KINDS[kind], categories=[])


def _item(kind: str, iid: str) -> MenuItem:
    return next(i for i in ALL_KINDS[kind] if i.id == iid)


def _venue_menu() -> Menu:
    items = []
    for v in venues.build(GeoPoint(lat=37.236, lon=-121.97), {"coffee", "sandwich"}):
        items += [MenuItem.model_validate(i) for c in v["categories"] for i in c["items"]]
    return Menu(merchant_id="venues", items=items, categories=[])


# ------------------------------------------------------------------ the variant groups
def test_soda_is_a_required_brand_choice_with_six_brands_and_their_images():
    soda = _item("convenience", "v-soda")
    g = soda.group("brand")
    assert g and g.min_select == 1 and g.max_select == 1
    assert [o.name for o in g.options] == ["Coca-Cola", "Diet Coke", "Coke Zero", "Sprite", "Pepsi", "Dr Pepper"]
    assert all(o.image and (ASSETS / f"{o.image}.jpg").is_file() for o in g.options)
    assert len({o.image for o in g.options}) == 6
    assert len(g.default_ids()) == 1


@pytest.mark.parametrize("iid,brands", [
    ("v-energy", ["Red Bull", "Monster", "Celsius"]),
    ("v-water", ["Dasani", "Smartwater", "Fiji", "Sparkling"]),
    ("v-tea", ["Arizona", "Pure Leaf"]),
    ("v-chips", ["Lay's", "Doritos", "Kettle"]),
    ("v-candy", ["Snickers", "KitKat", "Twix"]),
])
def test_convenience_brand_groups(iid, brands):
    g = _item("convenience", iid).group("brand")
    assert [o.name for o in g.options] == brands and all(o.image for o in g.options)


def test_pharmacy_otc_has_generic_and_brand_equivalents():
    ibu = _item("pharmacy", "h-ibu").group("brand")
    assert [o.name for o in ibu.options] == ["Generic ibuprofen", "Advil"]
    assert ibu.option("advil").price_delta.amount > ibu.option("generic").price_delta.amount == 0
    assert [o.name for o in _item("pharmacy", "h-apap").group("brand").options][1] == "Tylenol"


def test_coffee_has_size_temperature_milk_and_syrup_with_prices():
    latte = _item("coffee", "c-latte")
    ids = [g.id for g in latte.modifier_groups]
    assert ids == ["size", "temp", "milk", "syrup"]
    assert {o.id for o in latte.group("milk").options} >= {"whole", "oat", "almond", "soy"}
    assert {o.id for o in latte.group("syrup").options} == {"vanilla", "caramel", "hazelnut"}
    assert latte.group("syrup").min_select == 0 and latte.group("temp").min_select == 1
    base = make_line("x", latte, {}).unit_price.amount
    big = make_line("x", latte, {"size": ["s20"], "temp": ["iced"], "milk": ["oat"], "syrup": ["vanilla"]}).unit_price
    assert big.amount == base + (110 - 60) + 30 + 70 + 50


def test_boba_toppings_pizza_burger_and_spice_groups():
    boba = _item("tea", "t-boba").group("toppings")
    assert boba.max_select == 2 and boba.min_select == 0
    assert [g.id for g in _item("pizza", "p-pepperoni").modifier_groups] == ["size", "extras"]
    assert _item("pizza", "p-pepperoni").group("size").option("p18").price_delta.amount > 0
    assert _item("burger", "g-bacon").group("addons").max_select == 4
    assert _item("indian", "i-butter").group("spice") and _item("thai", "th-padthai").group("spice")


def test_real_catalogue_items_keep_their_real_modifiers(catalog):
    for mid, m in catalog.merchants.items():
        if m["source"] != "catalog":
            continue
        for it in catalog.menu(mid).items:
            for g in it.modifier_groups:
                assert g.id != "brand" and all(not o.image for o in g.options), (mid, it.name)


def test_option_image_keys_all_have_assets():
    for items in [*ALL_KINDS.values(), _venue_menu().items]:
        for it in items:
            for g in it.modifier_groups:
                for o in g.options:
                    assert not o.image or (ASSETS / f"{o.image}.jpg").is_file(), (it.name, o.name)


# ------------------------------------------------------------------ voice
def _add(text: str, menu: Menu):
    i = p.parse(text, Context(view="menu", menu=menu))
    assert i.name in ("add_items", "open_item"), (text, i.name)
    return [(menu.item(x["item_id"]), x["qty"], x["selections"]) for x in i.slots["items"]]


def test_two_diet_cokes():
    (item, qty, sel), = _add("two Diet Cokes", _menu("convenience"))
    assert item.name == "Soda" and qty == 2 and sel["brand"] == ["diet"]


def test_a_red_bull_and_a_snickers():
    got = _add("a Red Bull and a Snickers", _menu("convenience"))
    assert [(i.name, q, s["brand"]) for i, q, s in got] == [("Energy Drink", 1, ["redbull"]), ("Candy Bar", 1, ["snickers"])]


def test_brand_aliases_pick_the_longest_match():
    for text, brand in {"a coke zero": ["zero"], "a coke": ["coke"], "a dr pepper": ["drpepper"],
                        "a pepsi": ["pepsi"], "a sprite": ["sprite"]}.items():
        (item, _, sel), = _add(text, _menu("convenience"))
        assert item.name == "Soda" and sel["brand"] == brand, text


def test_a_smartwater_and_an_advil():
    (item, _, sel), = _add("a smartwater", _menu("convenience"))
    assert item.name == "Water" and sel["brand"] == ["smartwater"]
    (item, _, sel), = _add("add Advil", _menu("pharmacy"))
    assert item.name.startswith("Ibuprofen") and sel["brand"] == ["advil"]


def test_large_iced_oat_latte_with_vanilla():
    (item, qty, sel), = _add("a large iced oat latte with vanilla", _menu("coffee"))
    assert item.name in ("Latte", "Iced Latte") and qty == 1
    assert sel["size"] == ["s20"] and sel["milk"] == ["oat"] and sel["syrup"] == ["vanilla"]
    if item.name == "Latte":
        assert sel["temp"] == ["iced"]


def test_pizza_size_burger_addons_and_boba_toppings():
    (item, _, sel), = _add("a large pepperoni pizza", _menu("pizza"))
    assert item.name == "Pepperoni Pizza" and sel["size"] == ["p18"]
    (item, _, sel), = _add("a bacon burger with avocado", _menu("burger"))
    assert item.name == "Bacon Burger" and sel["addons"] == ["avocado"]
    (item, _, sel), = _add("a brown sugar milk tea with pearls and lychee", _menu("tea"))
    assert item.id == "t-boba"


def test_spice_level_by_voice():
    (item, _, sel), = _add("a hot butter chicken", _menu("indian"))
    assert item.name == "Butter Chicken" and sel["spice"] == ["hot"]


def test_quantity_vs_money_and_prepay_stay_safe():
    fuel = _menu("fuel")
    (item, qty, sel), = _add("regular, eighty dollars", fuel)
    assert qty == 1 and sel["amount"] == ["a80"]
    (item, qty, _), = _add("two diet cokes", _menu("convenience"))
    assert qty == 2


def test_line_price_follows_the_brand_delta():
    water = _item("convenience", "v-water")
    assert make_line("x", water, {"brand": ["fiji"]}).unit_price.amount == water.base_price.amount + 150


# ------------------------------------------------------------------ snapshot: option image and cart options
async def test_snapshot_item_photo_follows_the_selected_brand(settings, tmp_path):
    from driveos.container import build_services
    from driveos.server.snapshot import build_state
    from driveos.viewmodel import build_view

    gen = tmp_path / "gen"
    gen.mkdir()
    for k in ("soda", "cola_can", "diet_cola_can", "zero_cola_can", "lemon_lime_can", "blue_cola_can",
              "maroon_soda_can", "cover_grocery_1", "cover_grocery_2"):
        Image.new("RGB", (64, 64), (120, 20, 20)).save(gen / f"{k}.jpg", "JPEG")
    settings.images.generated_dir = gen
    s = build_services(settings)
    nova = s.nova
    rec = sample_menus.merchant_record({"id": "z9", "name": "Sample Market", "address": "1 Main",
                                        "location": GeoPoint(lat=37.44, lon=-122.14), "categories": ["convenience_store"]},
                                       "store")
    s.registry.get("local").catalog.add_external(rec)
    place = s.registry.get("local").catalog.place(rec, provider="local")
    place.extra.update(distance_m=300.0, orderable=True, has_menu=True, sample_menu=True)
    nova.shop.places = [place]
    await nova.open_place(place_id=place.id, speak=False)
    soda = next(i for i in nova.shop.menu.items if i.name == "Soda")
    await nova.open_item(soda.id, speak=False)

    def item_state():
        return build_state(nova, build_view(nova), level=0.0, toast=None, update={})["shop"]["item"]

    first = item_state()
    brand = next(g for g in first["groups"] if g["id"] == "brand")
    assert [o["name"] for o in brand["options"]][:3] == ["Coca-Cola", "Diet Coke", "Coke Zero"]
    assert all(o["img"].startswith("food/") for o in brand["options"])
    assert first["img_lg"] == "food/cola_can_320.jpg"
    await nova.set_item_option({"brand": ["zero"]})
    assert item_state()["img_lg"] == "food/zero_cola_can_320.jpg"
    assert next(o for o in next(g for g in item_state()["groups"] if g["id"] == "brand")["options"] if o["on"])["id"] == "zero"
    await nova.add_item(soda.id, 2, {"brand": ["diet"]}, speak=False)
    cart = build_state(nova, build_view(nova), level=0.0, toast=None, update={})["shop"]["cart"]
    assert cart["lines"][0]["opts"] == "Diet Coke" and cart["lines"][0]["img"] == "food/diet_cola_can_160.jpg"


async def test_cart_line_shows_the_chosen_options_short(settings):
    from driveos.container import build_services
    from driveos.server.snapshot import build_state
    from driveos.viewmodel import build_view

    s = build_services(settings)
    nova = s.nova
    rec = sample_menus.merchant_record({"id": "z8", "name": "Sample Cafe", "address": "1 Main",
                                        "location": GeoPoint(lat=37.44, lon=-122.14), "categories": ["coffee"]}, "coffee")
    s.registry.get("local").catalog.add_external(rec)
    place = s.registry.get("local").catalog.place(rec, provider="local")
    place.extra.update(distance_m=300.0, orderable=True, has_menu=True, sample_menu=True)
    nova.shop.places = [place]
    await nova.open_place(place_id=place.id, speak=False)
    latte = next(i for i in nova.shop.menu.items if i.name == "Latte")
    await nova.add_item(latte.id, 1, {"size": ["s20"], "temp": ["iced"], "milk": ["oat"], "syrup": ["vanilla"]}, speak=False)
    line = build_state(nova, build_view(nova), level=0.0, toast=None, update={})["shop"]["cart"]["lines"][0]
    assert line["opts"] == "Large · Iced · Oat · Vanilla"


async def test_set_option_command_picks_toggles_and_the_sheet_add_sends_the_selection(settings):
    from driveos.container import build_services
    from driveos.server.commands import CommandError, Commands
    from driveos.ui.actions import UiActions

    s = build_services(settings)
    nova = s.nova
    actions = UiActions()
    actions.nova = nova
    cmds = Commands(actions, nova)
    rec = sample_menus.merchant_record({"id": "z7", "name": "Sample Tea", "address": "1 Main",
                                        "location": GeoPoint(lat=37.44, lon=-122.14), "categories": ["coffee"]}, "coffee")
    s.registry.get("local").catalog.add_external(rec)
    place = s.registry.get("local").catalog.place(rec, provider="local")
    place.extra.update(distance_m=300.0, orderable=True, has_menu=True, sample_menu=True)
    nova.shop.places = [place]
    await nova.open_place(place_id=place.id, speak=False)
    latte = next(i for i in nova.shop.menu.items if i.name == "Latte")
    await nova.open_item(latte.id, speak=False)

    async def run(name, **args):
        await cmds.run(name, args)

    await run("set_option", group="milk", option="oat")
    await run("set_option", group="syrup", option="vanilla")
    assert nova.shop.item_sel["milk"] == ["oat"] and nova.shop.item_sel["syrup"] == ["vanilla"]
    await run("set_option", group="syrup", option="vanilla")           # an optional group clears on a second tap
    assert "syrup" not in nova.shop.item_sel
    with pytest.raises(CommandError):
        await run("set_option", group="milk", option="nope")
    await run("add_item", id=latte.id, qty=1)                           # the sheet sends only id and qty
    assert nova.shop.cart.lines[0].selections["milk"] == ["oat"]
