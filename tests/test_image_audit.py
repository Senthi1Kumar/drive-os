"""Coverage thresholds for the generated-image matcher over every menu and place category the app can show."""

from __future__ import annotations

import pytest

from driveos.images import audit
from driveos.images.matcher import ALL_KEYS, GENERIC_KEYS, assign_menu, key_for_place
from tests.conftest import requires_dataset


@pytest.fixture(scope="module")
def sample():
    return audit.sample_menu_stats()


@pytest.fixture(scope="module")
def venue():
    return audit.venue_stats()


@pytest.fixture(scope="module")
def catalog():
    return audit.catalog_stats()


def test_every_referenced_asset_exists(sample, venue, catalog):
    missing = {k for rows in (sample, venue, catalog) for r in rows for k in r["missing_assets"]}
    assert not missing, sorted(missing)
    assert audit.place_cover_stats()["missing_assets"] == []


def test_no_item_is_left_without_an_image(sample, venue, catalog):
    for rows in (sample, venue, catalog):
        for r in rows:
            assert all(k for _, k in r["names"].values()), r["merchant"]


def test_own_menus_repeat_an_image_at_most_twice(sample, venue):
    worst = [(r["merchant"], k, n) for r in sample + venue for k, n in r["keys"].items() if n > 2]
    # a handful of plain refuel/convenience items legitimately share the fuel pump picture
    assert all(k in ("fuel_pump",) for _, k, _ in worst), worst


def test_generic_fallback_is_rare_in_own_menus(sample, venue):
    assert sum(r["generic"] for r in sample + venue) <= 2


@requires_dataset
def test_crawled_menus_use_many_distinct_images(catalog):
    rows = [r for r in catalog if r["items"] >= 12]
    assert rows
    unique = sum(r["unique"] for r in rows) / sum(r["items"] for r in rows)
    assert unique >= 0.30                       # was well under 0.1 with one image per keyword
    assert audit.summarise(catalog)["unique_keys"] >= 100


def test_no_single_image_dominates_a_crawled_menu(catalog):
    for r in catalog:
        if r["items"] >= 10:
            assert r["max_repeat"] <= max(5, int(r["items"] * 0.5)), (r["merchant"], r["keys"])


def test_every_place_category_has_a_cover_and_variety():
    covers = audit.place_cover_stats()["covers"]
    assert covers["hotel"] and not audit.place_cover_stats()["none"]
    for cat in ("coffee", "tea", "food", "bar", "hotel", "pharmacy", "ev", "gas", "store", "bakery", "sandwich"):
        assert len(covers[cat]) >= 8, cat


def test_place_cover_is_deterministic_per_place_and_varies_across_places():
    assert key_for_place("food", "", "p1") == key_for_place("food", "", "p1")
    assert len({key_for_place("food", "", f"p{i}") for i in range(20)}) >= 2


def test_adjacent_distinct_items_get_alternates():
    from driveos.domain.models import MenuItem, Money
    items = [MenuItem(id=f"i{n}", name=name, base_price=Money(amount=300, currency="USD"))
             for n, name in enumerate(["Cafe Latte", "Vanilla Latte", "Oat Latte", "Mocha", "Americano", "Drip Coffee"])]
    picks = assign_menu(items, "coffee")
    assert len({picks["i0"], picks["i1"], picks["i2"]}) == 2      # two latte pictures, handed out evenly
    assert len({picks["i4"], picks["i5"]}) == 2


def test_dish_and_cuisine_keywords():
    from driveos.images.matcher import key_for
    for name, key in {"Pad Thai": "pad_thai", "Beef Pho": "pho", "Lamb Gyro": "gyro", "Poke Bowl": "poke_bowl",
                      "Bibimbap": "bibimbap", "Falafel Plate": "falafel", "Tiramisu": "tiramisu",
                      "Brown Sugar Boba": "boba_tea", "Capuccino": "cappuccino", "icedlatte": "iced_coffee",
                      "Orange Chicken": "chinese_chicken", "Hot Dog": "hot_dog", "Mac & Cheese": "mac_cheese",
                      "Red Bull": "energy_drink", "Perrier": "sparkling_can", "Fanta": "soda"}.items():
        assert key_for(name) == key, name


def test_keys_are_unique_and_pools_resolve_to_known_keys():
    from driveos.images.matcher import POOLS
    assert len(ALL_KEYS) == len(set(ALL_KEYS))
    for base, pool in POOLS.items():
        assert all(k in ALL_KEYS or k in GENERIC_KEYS for k in pool), (base, pool)


def test_a_path_whose_file_vanished_is_never_sent(tmp_path):
    from pathlib import Path

    from PIL import Image

    from driveos.images.store import ImageStore

    gen = tmp_path / "gen"
    gen.mkdir()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(gen / "latte.jpg", "JPEG")
    store = ImageStore(tmp_path / "root", generated_dir=gen)
    t = store.generated("latte")
    assert t and (store.root / t.small).is_file()
    (store.root / t.small).unlink()                       # cache directory cleaned while the app runs
    again = store.generated("latte")
    assert again and (store.root / again.small).is_file()   # rebuilt from the shipped asset
    for p in (store.root / again.large, store.root / again.small):
        Path(p).write_bytes(b"")                          # truncated file
    assert store.generated("latte") and (store.root / again.large).stat().st_size > 0
    assert store.generated("missing_key") is None
