"""Demo venues for errands beyond restaurants: fuel, EV charging, convenience, pharmacy, a taproom.

They are generated around the car (bearing + distance), so the full flow — find, choose, add to cart,
authorise, pay, drive there — works anywhere in demo mode. Names are fictional on purpose; in live mode
real stations and stores come from Mapbox search and are navigate-only unless an ordering backend
(MCP server or Square seller) covers them.
"""

from __future__ import annotations

import math
from typing import Any

from ..domain.models import GeoPoint
from . import variants as vr

MI = 1609.344


def _at(anchor: GeoPoint, miles: float, bearing: float) -> dict[str, float]:
    d = miles * MI
    b = math.radians(bearing)
    return {"lat": anchor.lat + d * math.cos(b) / 111_320.0,
            "lon": anchor.lon + d * math.sin(b) / (111_320.0 * math.cos(math.radians(anchor.lat)))}


def _i(iid: str, name: str, cents: int, cat: str, desc: str = "", groups: list | None = None,
       glyph: str = "") -> dict[str, Any]:
    return {"id": iid, "name": name, "description": desc, "category": cat,
            "base_price": {"amount": cents, "currency": "USD"}, "image_url": None,
            "modifier_groups": groups or [], "aliases": [], "available": True, "refs": {"glyph": glyph}}


def _opt(oid: str, name: str, delta: int = 0, default: bool = False, aliases: tuple[str, ...] = ()) -> dict:
    return {"id": oid, "name": name, "price_delta": {"amount": delta, "currency": "USD"}, "default": default,
            "aliases": [name.lower(), *aliases]}


def _grp(gid: str, name: str, opts: list[dict], aliases: tuple[str, ...] = ()) -> dict:
    return {"id": gid, "name": name, "min_select": 1, "max_select": 1, "options": opts, "aliases": list(aliases)}


AMOUNT = _grp("amount", "Prepay", [
    _opt("a20", "$20", 0, aliases=("twenty", "20 dollars")),
    _opt("a40", "$40", 2000, True, aliases=("forty", "40 dollars")),
    _opt("a60", "$60", 4000, aliases=("sixty", "60 dollars")),
    _opt("a80", "$80", 6000, aliases=("eighty", "80 dollars", "fill up", "fill it up", "full tank")),
], ("amount", "prepay"))


def _fuel_items(regular_per_gal: str) -> list[dict]:
    return [
        _i("fuel-regular", "Regular 87", 2000, "Fuel", f"{regular_per_gal}/gal · pump unlocks for the prepaid amount",
           [AMOUNT], "fuel"),
        _i("fuel-premium", "Premium 91", 2000, "Fuel", "+$0.70/gal over regular", [AMOUNT], "fuel"),
        _i("fuel-diesel", "Diesel", 2000, "Fuel", "Prepay, unused balance released", [AMOUNT], "fuel"),
        _i("wash-basic", "Car wash · Express", 1200, "Car wash", "Touchless, 5 minutes", glyph="wash"),
        _i("wash-deluxe", "Car wash · Deluxe", 1800, "Car wash", "Undercarriage, wax, tyre shine", glyph="wash"),
        _i("store-water", "Water", 299, "Quick grab", "Pick your brand", [vr.WATER_BRAND], "water"),
        _i("store-coffee", "Fresh drip coffee", 249, "Quick grab", "16 oz", glyph="coffee"),
        _i("store-chips", "Chips", 349, "Quick grab", "Pick your brand", [vr.CHIPS_BRAND], "bag"),
        _i("store-soda", "Soda", 249, "Quick grab", "Pick your brand", [vr.SODA_BRAND], "glass"),
        _i("store-energy", "Energy drink", 349, "Quick grab", "Pick your brand", [vr.ENERGY_BRAND], "glass"),
    ]


SIZE = _grp("size", "Size", [
    _opt("s12", "Small 12 oz", 0, aliases=("small", "12 ounce")),
    _opt("s16", "Medium 16 oz", 60, True, aliases=("medium", "regular", "16 ounce")),
    _opt("s20", "Large 20 oz", 110, aliases=("large", "20 ounce", "big")),
], ("size",))


def _coffee_bar(add) -> None:
    add("v-coffee-1", "Daybreak Coffee Bar", "restaurant", ["coffee", "bakery", "food"], 0.7, 75, "Main St",
        [("Coffee", [_i("db-latte", "Latte", 525, "Coffee", "Double shot, steamed milk", [SIZE, vr.TEMP, vr.MILK, vr.SYRUP],
                        "coffee"),
                     _i("db-caramel", "Caramel Latte", 595, "Coffee", "House caramel, double shot",
                        [SIZE, vr.TEMP, vr.MILK], "coffee"),
                     _i("db-cold-brew", "Cold Brew", 475, "Coffee", "Steeped 18 hours", [SIZE], "coffee"),
                     _i("db-drip", "Drip Coffee", 325, "Coffee", "Single origin, rotating", [SIZE], "coffee"),
                     _i("db-chai", "Chai Latte", 545, "Coffee", "Spiced black tea", [SIZE, vr.TEMP, vr.MILK], "coffee")]),
         ("Bakery", [_i("db-croissant", "Butter Croissant", 395, "Bakery", "Baked this morning", glyph="croissant"),
                     _i("db-muffin", "Blueberry Muffin", 375, "Bakery", glyph="croissant"),
                     _i("db-cookie", "Chocolate Chip Cookie", 295, "Bakery", glyph="croissant")])],
        ["curbside", "walk_in"], "Curbside spot 2", 6, "6:00 AM - 6:00 PM")


def _deli(add) -> None:
    add("v-deli-1", "Fieldstone Deli", "restaurant", ["sandwich", "food"], 1.2, 330, "Center St",
        [("Sandwiches", [_i("fd-turkey", "Turkey Avocado", 1195, "Sandwiches", "Sourdough, provolone, sprouts",
                            glyph="sandwich"),
                         _i("fd-italian", "Italian Combo", 1295, "Sandwiches", "Salami, mortadella, pepperoncini",
                            glyph="sandwich"),
                         _i("fd-caprese", "Caprese", 1095, "Sandwiches", "Fresh mozzarella, basil, tomato",
                            glyph="sandwich"),
                         _i("fd-blt", "Classic BLT", 1050, "Sandwiches", glyph="sandwich")]),
         ("Sides", [_i("fd-chips", "Chips", 250, "Sides", "Pick your brand", [vr.CHIPS_BRAND], "bag"),
                    _i("fd-salad", "Pasta Salad", 495, "Sides", glyph="food"),
                    _i("fd-lemonade", "Fresh Lemonade", 395, "Sides", glyph="glass"),
                    _i("fd-soda", "Soda", 295, "Sides", "Pick your brand", [vr.SODA_BRAND], "glass")])],
        ["curbside", "walk_in"], "Curbside pickup", 10, "8:00 AM - 7:00 PM")


def build(anchor: GeoPoint, extras: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Errand venues around ``anchor``; ``extras`` adds a coffee bar and/or a deli where the restaurant
    data set has none (live GPS anywhere outside its 20 cities)."""
    base = {"approx": False, "source": "venue", "tax_rate": "0.0925", "hours": "Open 24 hours", "cover": "",
            "description": "", "rating": None, "unit_token": "", "city": "", "state": ""}
    v: list[dict[str, Any]] = []

    def add(vid: str, name: str, kind: str, tags: list[str], miles: float, bearing: float, address: str,
            cats: list[tuple[str, list[dict]]], modes: list[str], spot: str, prep: int, hours: str | None = None):
        v.append({**base, "id": vid, "name": name, "kind": kind, "tags": tags, "address": address,
                  "location": _at(anchor, miles, bearing),
                  "categories": [{"name": c, "items": items} for c, items in cats], "pickup_modes": modes,
                  "pickup_spot": spot, "prep_minutes": prep, **({"hours": hours} if hours else {})})

    fuel = _fuel_items("$4.89")
    add("v-fuel-1", "Summit Fuel & Wash", "fuel", ["gas", "store"], 0.8, 35, "Blossom Hill Rd",
        [("Fuel", fuel[:3]), ("Car wash", fuel[3:5]), ("Quick grab", fuel[5:])], ["pump"], "Pump 4", 1)
    fuel2 = _fuel_items("$4.79")
    add("v-fuel-2", "Valley Gas Stop", "fuel", ["gas"], 1.9, 290, "Winchester Blvd",
        [("Fuel", fuel2[:3]), ("Quick grab", fuel2[5:])], ["pump"], "Pump 7", 1)

    add("v-ev-1", "Village EV Hub", "ev", ["ev"], 1.1, 160, "N Santa Cruz Ave",
        [("Charging", [
            _i("ev-30", "Fast charge · 30 min", 1450, "Charging", "Up to 150 kW · about 120 mi of range", glyph="bolt"),
            _i("ev-15", "Top-up · 15 min", 790, "Charging", "Up to 150 kW · about 60 mi of range", glyph="bolt"),
            _i("ev-idle", "Reserve a stall", 300, "Charging", "Held for 20 minutes, credited to the session",
               glyph="pin"),
        ])], ["charger"], "Stall 3", 1)

    add("v-store-1", "Corner Market", "store", ["store"], 0.6, 210, "Los Gatos Blvd",
        [("Drinks", [_i("cm-water", "Water", 299, "Drinks", "Pick your brand", [vr.WATER_BRAND], "water"),
                     _i("cm-soda", "Soda", 249, "Drinks", "Pick your brand", [vr.SODA_BRAND], "glass"),
                     _i("cm-tea", "Iced tea", 299, "Drinks", "Pick your brand", [vr.TEA_BRAND], "glass"),
                     _i("cm-energy", "Energy drink", 349, "Drinks", "Pick your brand", [vr.ENERGY_BRAND], "glass"),
                     _i("cm-coldbrew", "Cold brew 4-pack", 1199, "Drinks", glyph="coffee")]),
         ("Snacks", [_i("cm-chips", "Chips", 349, "Snacks", "Pick your brand", [vr.CHIPS_BRAND], "bag"),
                     _i("cm-candy", "Candy bar", 229, "Snacks", "Pick your brand", [vr.CANDY_BRAND], "bag"),
                     _i("cm-trail", "Trail mix", 549, "Snacks", glyph="bag"),
                     _i("cm-bar", "Protein bars · 4", 799, "Snacks", glyph="bag"),
                     _i("cm-fruit", "Fresh fruit cup", 499, "Snacks", glyph="bag")]),
         ("Essentials", [_i("cm-cable", "USB-C charging cable", 1499, "Essentials", "1 m, fast charge",
                            glyph="plug"),
                         _i("cm-wipes", "Wet wipes", 399, "Essentials", glyph="bag"),
                         _i("cm-umbrella", "Compact umbrella", 1299, "Essentials", glyph="bag"),
                         _i("cm-flowers", "Fresh flowers", 1499, "Essentials", "Seasonal bouquet", glyph="flower")])],
        ["in_store"], "Pickup counter", 6, "6:00 AM - 11:00 PM")

    add("v-rx-1", "Main Street Pharmacy", "pharmacy", ["pharmacy", "store"], 1.3, 255, "E Main St",
        [("Health", [_i("rx-ibu", "Ibuprofen 200 mg · 50", 899, "Health", "Pain and fever", [vr.IBUPROFEN_BRAND], "pill"),
                     _i("rx-apap", "Acetaminophen 500 mg · 50", 799, "Health", "Pain and fever", [vr.ACETAMINOPHEN_BRAND],
                        "pill"),
                     _i("rx-allergy", "Allergy relief · 30", 1599, "Health", "24 hour antihistamine",
                        [vr.ALLERGY_BRAND], "pill"),
                     _i("rx-band", "Bandages · assorted", 549, "Health", glyph="pill"),
                     _i("rx-sanitizer", "Hand sanitizer", 349, "Health", glyph="pill")]),
         ("Travel", [_i("rx-mints", "Mints", 249, "Travel", glyph="bag"),
                     _i("rx-sunscreen", "Sunscreen SPF 50", 1299, "Travel", glyph="bag"),
                     _i("rx-water", "Water", 299, "Travel", "Pick your brand", [vr.WATER_BRAND], "water")])],
        ["in_store"], "Pharmacy counter", 10, "8:00 AM - 9:00 PM")

    add("v-bar-1", "Taproom 17", "bar", ["bar", "food"], 1.6, 120, "University Ave",
        [("Food", [_i("tr-pretzel", "Bavarian pretzel", 1100, "Food", "Beer cheese, mustard", glyph="food"),
                   _i("tr-burger", "Taproom burger", 1800, "Food", "Cheddar, onion jam, fries", glyph="food"),
                   _i("tr-wings", "Smoked wings", 1500, "Food", "Six, dry rub", glyph="food")]),
         ("Non-alcoholic", [_i("tr-na-ipa", "NA hazy IPA · 4-pack", 1499, "Non-alcoholic", glyph="glass"),
                            _i("tr-soda", "House ginger soda", 500, "Non-alcoholic", glyph="glass")]),
         ("To go · ID at pickup", [_i("tr-crowler", "Crowler · house IPA 32 oz", 1600, "To go · ID at pickup",
                                      "Sealed, 21+ — ID checked at pickup", glyph="glass")])],
        ["walk_in"], "Front counter", 12, "11:30 AM - 11:00 PM")
    if "coffee" in extras:
        _coffee_bar(add)
    if "sandwich" in extras:
        _deli(add)
    return v
