"""Sample menus for real places found on the map.

Mapbox knows where a business is, not what it sells. For a demo of ordering and paying, a real place is
given a plausible menu for its kind of business. These menus are never the business's own: every record is
flagged ``sample`` and the UI labels it "Sample Only". Prices are made-up US dollars.
"""

from __future__ import annotations

import re
from typing import Any

from ..domain.categories import CATEGORIES
from . import variants as v

SAMPLE_LABEL = "Sample Only"
SAMPLE_NOTE = "Sample Only"


_opt, _grp = v.opt, v.grp
SIZE, MILK, TEA_SIZE, TEMP, SYRUP, BOBA = v.SIZE, v.MILK, v.TEA_SIZE, v.TEMP, v.SYRUP, v.BOBA_TOPPINGS
PIZZA_SIZE, PIZZA_TOP, BURGER_ADDONS, SPICE = v.PIZZA_SIZE, v.PIZZA_TOP, v.BURGER_ADDONS, v.SPICE
FUEL_AMOUNT = _grp("amount", "Prepay", [
    _opt("a20", "$20", 0, aliases=("twenty",)), _opt("a40", "$40", 2000, True, aliases=("forty",)),
    _opt("a60", "$60", 4000, aliases=("sixty",)), _opt("a80", "$80", 6000, aliases=("eighty", "fill up"))],
    ("amount", "prepay"))


def _i(iid: str, name: str, cents: int, cat: str, desc: str = "", groups: list | None = None) -> dict[str, Any]:
    return {"id": iid, "name": name, "description": desc, "category": cat,
            "base_price": {"amount": cents, "currency": "USD"}, "image_url": None,
            "modifier_groups": groups or [], "aliases": [], "available": True, "refs": {"sample": "1"}}


def _coffee() -> list:
    return [("Coffee", [_i("c-latte", "Latte", 525, "Coffee", "Espresso and steamed milk", [SIZE, TEMP, MILK, SYRUP]),
                        _i("c-cappuccino", "Cappuccino", 495, "Coffee", "Espresso, steamed milk, deep foam",
                           [SIZE, TEMP, MILK, SYRUP]),
                        _i("c-flat", "Flat White", 505, "Coffee", "Double ristretto, microfoam", [MILK]),
                        _i("c-americano", "Americano", 395, "Coffee", "Espresso and hot water", [SIZE, TEMP]),
                        _i("c-mocha", "Mocha", 575, "Coffee", "Espresso, chocolate, steamed milk", [SIZE, TEMP, MILK]),
                        _i("c-cold", "Cold Brew", 475, "Coffee", "Steeped 18 hours, over ice", [SIZE]),
                        _i("c-iced-latte", "Iced Latte", 545, "Coffee", "Over ice", [SIZE, MILK, SYRUP])]),
            ("Tea", [_i("c-chai", "Chai Latte", 545, "Tea", "Spiced black tea, steamed milk", [SIZE, TEMP, MILK]),
                     _i("c-matcha", "Matcha Latte", 585, "Tea", "Ceremonial grade matcha", [SIZE, TEMP, MILK])]),
            ("Bakery", [_i("c-croissant", "Butter Croissant", 395, "Bakery"),
                        _i("c-muffin", "Blueberry Muffin", 375, "Bakery"),
                        _i("c-cookie", "Chocolate Chip Cookie", 295, "Bakery")])]


def _tea() -> list:
    return [("Tea", [_i("t-green", "Jasmine Green Tea", 425, "Tea", "Hot or iced", [TEA_SIZE, TEMP]),
                     _i("t-black", "English Breakfast Tea", 395, "Tea", "Strong black tea", [TEA_SIZE, TEMP]),
                     _i("t-masala", "Masala Chai", 475, "Tea", "Simmered with milk and spices", [TEA_SIZE, TEMP]),
                     _i("t-matcha", "Matcha Latte", 585, "Tea", "Ceremonial grade", [TEA_SIZE, TEMP, MILK]),
                     _i("t-boba", "Brown Sugar Milk Tea", 650, "Tea", "With tapioca pearls", [TEA_SIZE, MILK, BOBA])]),
            ("Cold drinks", [_i("t-smoothie", "Mango Smoothie", 625, "Cold drinks"),
                             _i("t-oj", "Fresh Orange Juice", 495, "Cold drinks")]),
            ("Bites", [_i("t-cookie", "Sesame Cookie", 295, "Bites")])]


def _bakery() -> list:
    return [("Pastries", [_i("b-croissant", "Butter Croissant", 395, "Pastries", "Baked this morning"),
                          _i("b-muffin", "Blueberry Muffin", 375, "Pastries"),
                          _i("b-donut", "Glazed Donut", 295, "Pastries"),
                          _i("b-cookie", "Chocolate Chip Cookie", 295, "Pastries"),
                          _i("b-cake", "Chocolate Cake Slice", 595, "Pastries")]),
            ("Bread", [_i("b-bagel", "Sesame Bagel", 275, "Bread", "Cream cheese +$1"),
                       _i("b-sandwich", "Turkey Sandwich", 995, "Bread", "On house sourdough")]),
            ("Drinks", [_i("b-drip", "Drip Coffee", 325, "Drinks", groups=[SIZE]),
                        _i("b-latte", "Latte", 525, "Drinks", groups=[SIZE, TEMP, MILK, SYRUP])])]


def _pizza() -> list:
    return [("Pizza", [_i("p-margherita", "Margherita Pizza", 1495, "Pizza", "Tomato, mozzarella, basil",
                          [PIZZA_SIZE, PIZZA_TOP]),
                       _i("p-pepperoni", "Pepperoni Pizza", 1695, "Pizza", "", [PIZZA_SIZE, PIZZA_TOP]),
                       _i("p-veggie", "Garden Veggie Pizza", 1595, "Pizza", "", [PIZZA_SIZE, PIZZA_TOP]),
                       _i("p-slice", "Pepperoni Slice", 450, "Pizza", "Single slice")]),
            ("Sides", [_i("p-wings", "Chicken Wings", 1195, "Sides", "Six pieces"),
                       _i("p-salad", "House Salad", 795, "Sides"),
                       _i("p-garlic", "Garlic Bread", 595, "Sides")]),
            ("Drinks", [_i("p-soda", "Soda", 295, "Drinks", "Pick your brand", [v.SODA_BRAND]),
                       _i("p-water", "Sparkling Water", 295, "Drinks")])]


def _burger() -> list:
    return [("Burgers", [_i("g-classic", "Classic Cheeseburger", 1195, "Burgers", "Cheddar, pickles, house sauce",
                            [BURGER_ADDONS]),
                         _i("g-bacon", "Bacon Burger", 1395, "Burgers", "", [BURGER_ADDONS]),
                         _i("g-veg", "Veggie Burger", 1295, "Burgers", "", [BURGER_ADDONS])]),
            ("Sides", [_i("g-fries", "French Fries", 495, "Sides"), _i("g-rings", "Onion Rings", 595, "Sides"),
                       _i("g-wings", "Chicken Wings", 1195, "Sides")]),
            ("Drinks", [_i("g-shake", "Vanilla Milkshake", 695, "Drinks"), _i("g-soda", "Soda", 295, "Drinks", "Pick your brand", [v.SODA_BRAND])])]


def _mexican() -> list:
    return [("Mains", [_i("m-burrito", "Carne Asada Burrito", 1295, "Mains", "Rice, beans, salsa"),
                       _i("m-tacos", "Street Tacos", 1095, "Mains", "Three corn tortillas", [
                           _grp("filling", "Filling", [_opt("asada", "Carne asada", 0, True),
                                                       _opt("pollo", "Pollo", 0), _opt("veg", "Veggie", 0)])]),
                       _i("m-quesadilla", "Chicken Quesadilla", 1195, "Mains"),
                       _i("m-nachos", "Loaded Nachos", 1095, "Mains")]),
            ("Sides", [_i("m-chips", "Chips and Salsa", 495, "Sides"), _i("m-rice", "Rice and Beans", 395, "Sides")]),
            ("Drinks", [_i("m-soda", "Mexican Cola", 350, "Drinks"), _i("m-lemonade", "Fresh Limeade", 395, "Drinks")])]


def _indian() -> list:
    return [("Curries", [_i("i-butter", "Butter Chicken", 1695, "Curries", "Creamy tomato sauce", [SPICE]),
                         _i("i-paneer", "Paneer Tikka Masala", 1595, "Curries", "", [SPICE]),
                         _i("i-dal", "Dal Tadka", 1295, "Curries", "", [SPICE])]),
            ("Rice and bread", [_i("i-biryani", "Chicken Biryani", 1695, "Rice and bread", "", [SPICE]),
                                _i("i-naan", "Garlic Naan", 395, "Rice and bread")]),
            ("Starters", [_i("i-samosa", "Vegetable Samosas", 695, "Starters", "Two pieces"),
                          _i("i-dosa", "Masala Dosa", 1195, "Starters")]),
            ("Drinks", [_i("i-chai", "Masala Chai", 395, "Drinks", "", [TEMP]), _i("i-lassi", "Mango Lassi", 495, "Drinks")])]


def _sushi() -> list:
    return [("Rolls", [_i("s-cali", "California Roll", 895, "Rolls", "Eight pieces"),
                       _i("s-spicy", "Spicy Tuna Roll", 1095, "Rolls"),
                       _i("s-dragon", "Dragon Roll", 1595, "Rolls")]),
            ("Nigiri", [_i("s-salmon", "Salmon Nigiri", 795, "Nigiri", "Two pieces"),
                        _i("s-combo", "Sushi Combo", 2195, "Nigiri", "Chef's selection")]),
            ("Hot", [_i("s-ramen", "Tonkotsu Ramen", 1595, "Hot", "", [SPICE]), _i("s-gyoza", "Pork Gyoza", 795, "Hot"),
                    _i("s-poke", "Poke Bowl", 1495, "Hot", "Tuna, avocado, edamame, rice")]),
            ("Drinks", [_i("s-green", "Green Tea", 295, "Drinks")])]


def _thai() -> list:
    return [("Noodles and rice", [_i("th-padthai", "Pad Thai", 1495, "Noodles and rice", "Rice noodles, shrimp, peanuts",
                                     [SPICE]),
                                  _i("th-pho", "Beef Pho", 1395, "Noodles and rice", "Rice noodle soup, herbs", [SPICE]),
                                  _i("th-fried", "Pineapple Fried Rice", 1395, "Noodles and rice", "", [SPICE])]),
            ("Curries", [_i("th-green", "Green Curry", 1595, "Curries", "Coconut milk, basil", [SPICE]),
                         _i("th-tomyum", "Tom Yum Soup", 895, "Curries", "Hot and sour", [SPICE])]),
            ("Starters", [_i("th-rolls", "Fresh Spring Rolls", 795, "Starters", "Two pieces"),
                          _i("th-dumpling", "Steamed Dumplings", 895, "Starters")]),
            ("Drinks", [_i("th-tea", "Thai Iced Tea", 495, "Drinks", "Sweet, with milk"),
                        _i("th-boba", "Taro Milk Tea", 625, "Drinks", "", [v.TEA_SIZE, BOBA])])]


def _mediterranean() -> list:
    return [("Plates", [_i("md-gyro", "Lamb Gyro", 1395, "Plates", "Pita, tzatziki, tomato, onion"),
                        _i("md-falafel", "Falafel Plate", 1295, "Plates", "Hummus, salad, pita"),
                        _i("md-salmon", "Grilled Salmon", 1995, "Plates", "Lemon, asparagus"),
                        _i("md-shrimp", "Grilled Shrimp", 1895, "Plates")]),
            ("Starters", [_i("md-hummus", "Hummus and Pita", 895, "Starters"),
                          _i("md-salad", "Greek Salad", 995, "Starters")]),
            ("Dessert", [_i("md-baklava", "Baklava", 595, "Dessert", "Pistachio and honey")]),
            ("Drinks", [_i("md-lemonade", "Fresh Lemonade", 395, "Drinks"),
                        _i("md-soda", "Soda", 295, "Drinks", "Pick your brand", [v.SODA_BRAND])])]


def _sandwich() -> list:
    return [("Sandwiches", [_i("w-turkey", "Turkey Avocado Sandwich", 1195, "Sandwiches", "Sourdough, provolone"),
                            _i("w-italian", "Italian Sub", 1295, "Sandwiches"),
                            _i("w-blt", "Classic BLT", 1050, "Sandwiches"),
                            _i("w-wrap", "Chicken Caesar Wrap", 1150, "Sandwiches")]),
            ("Sides", [_i("w-chips", "Chips", 250, "Sides", "Pick your brand", [v.CHIPS_BRAND]), _i("w-salad", "Garden Salad", 595, "Sides"),
                       _i("w-soup", "Tomato Soup", 495, "Sides")]),
            ("Drinks", [_i("w-lemonade", "Fresh Lemonade", 395, "Drinks"), _i("w-soda", "Soda", 295, "Drinks", "Pick your brand", [v.SODA_BRAND]),
                       _i("w-tea", "Iced Tea", 325, "Drinks", "Pick your brand", [v.TEA_BRAND])])]


def _casual() -> list:
    return [("Mains", [_i("r-burger", "Cheeseburger", 1395, "Mains"), _i("r-pasta", "Spaghetti Marinara", 1495, "Mains"),
                       _i("r-steak", "Grilled Steak", 2495, "Mains"),
                       _i("r-chicken", "Roast Chicken Plate", 1695, "Mains")]),
            ("Starters", [_i("r-soup", "Soup of the Day", 695, "Starters"), _i("r-salad", "House Salad", 895, "Starters"),
                          _i("r-wings", "Chicken Wings", 1295, "Starters")]),
            ("Dessert", [_i("r-cake", "Chocolate Cake", 795, "Dessert"), _i("r-ice", "Ice Cream", 595, "Dessert"),
                        _i("r-tira", "Tiramisu", 850, "Dessert"), _i("r-cheese", "New York Cheesecake", 825, "Dessert")]),
            ("Drinks", [_i("r-soda", "Soda", 295, "Drinks", "Pick your brand", [v.SODA_BRAND]),
                        _i("r-water", "Sparkling Water", 295, "Drinks")])]


def _bar() -> list:
    return [("Food", [_i("a-wings", "Smoked Wings", 1500, "Food", "Six, dry rub"),
                      _i("a-burger", "Pub Burger", 1800, "Food", "Cheddar, onion jam, fries"),
                      _i("a-pretzel", "Soft Pretzel", 1100, "Food", "Beer cheese"),
                      _i("a-fries", "Truffle Fries", 900, "Food")]),
            ("Non-alcoholic", [_i("a-soda", "House Ginger Soda", 500, "Non-alcoholic"),
                               _i("a-na", "Non-alcoholic Beer", 600, "Non-alcoholic")]),
            ("To go · ID at pickup", [_i("a-crowler", "Crowler · house IPA 32 oz", 1600, "To go · ID at pickup",
                                         "Sealed, 21+. ID checked at pickup")])]


def _pharmacy() -> list:
    return [("Health", [_i("h-ibu", "Ibuprofen 200 mg · 50", 899, "Health", "Pain and fever", [v.IBUPROFEN_BRAND]),
                        _i("h-apap", "Acetaminophen 500 mg · 50", 799, "Health", "Pain and fever", [v.ACETAMINOPHEN_BRAND]),
                        _i("h-allergy", "Allergy Relief · 30", 1599, "Health", "24 hour antihistamine",
                           [v.ALLERGY_BRAND]),
                        _i("h-band", "Bandages · assorted", 549, "Health"),
                        _i("h-sanitizer", "Hand Sanitizer", 349, "Health")]),
            ("Travel", [_i("h-mints", "Mints", 249, "Travel"), _i("h-sunscreen", "Sunscreen SPF 50", 1299, "Travel"),
                        _i("h-water", "Spring Water 1L", 299, "Travel", "Pick your brand", [v.WATER_BRAND])])]


def _convenience() -> list:
    return [("Drinks", [_i("v-water", "Water", 299, "Drinks", "Pick your brand", [v.WATER_BRAND]),
                        _i("v-soda", "Soda", 249, "Drinks", "Pick your brand", [v.SODA_BRAND]),
                        _i("v-energy", "Energy Drink", 349, "Drinks", "Pick your brand", [v.ENERGY_BRAND]),
                        _i("v-tea", "Iced Tea", 299, "Drinks", "Pick your brand", [v.TEA_BRAND])]),
            ("Snacks", [_i("v-chips", "Chips", 349, "Snacks", "Pick your brand", [v.CHIPS_BRAND]),
                        _i("v-bar", "Protein Bars · 4", 799, "Snacks"),
                        _i("v-candy", "Candy Bar", 229, "Snacks", "Pick your brand", [v.CANDY_BRAND])]),
            ("Essentials", [_i("v-cable", "USB-C Charging Cable", 1499, "Essentials"),
                            _i("v-wipes", "Wet Wipes", 399, "Essentials"),
                            _i("v-bag", "Grocery Basics Bag", 1299, "Essentials")])]


def _ev() -> list:
    return [("Charging", [_i("ev-30", "Fast charge · 30 min", 1450, "Charging", "Up to 150 kW"),
                          _i("ev-15", "Top-up · 15 min", 790, "Charging", "Up to 150 kW"),
                          _i("ev-idle", "Reserve a stall", 300, "Charging", "Held for 20 minutes")]),
            ("While you charge", [_i("e-coffee", "Drip Coffee", 325, "While you charge", groups=[SIZE]),
                                  _i("e-water", "Water", 299, "While you charge", "Pick your brand", [v.WATER_BRAND]),
                                  _i("e-chips", "Chips", 349, "While you charge", "Pick your brand",
                                     [v.CHIPS_BRAND])])]


def _fuel() -> list:
    return [("Fuel", [_i("fuel-regular", "Regular 87", 2000, "Fuel", "Pump unlocks for the prepaid amount",
                         [FUEL_AMOUNT]),
                      _i("fuel-premium", "Premium 91", 2000, "Fuel", "", [FUEL_AMOUNT])]),
            ("Convenience", [_i("f-water", "Water", 299, "Convenience", "Pick your brand", [v.WATER_BRAND]),
                             _i("f-coffee", "Fresh Drip Coffee", 249, "Convenience", groups=[SIZE]),
                             _i("f-chips", "Chips", 349, "Convenience", "Pick your brand", [v.CHIPS_BRAND]),
                             _i("f-energy", "Energy Drink", 349, "Convenience", "Pick your brand", [v.ENERGY_BRAND]),
                             _i("f-soda", "Soda", 249, "Convenience", "Pick your brand", [v.SODA_BRAND])])]


KINDS = {"coffee": _coffee, "tea": _tea, "bakery": _bakery, "pizza": _pizza, "burger": _burger,
         "mexican": _mexican, "indian": _indian, "sushi": _sushi, "thai": _thai, "mediterranean": _mediterranean, "sandwich": _sandwich, "casual": _casual,
         "bar": _bar, "pharmacy": _pharmacy, "convenience": _convenience, "ev": _ev, "fuel": _fuel}

_POI_KIND = [  # Mapbox poi_category ids, first match wins
    ("pizza", "pizza"), ("burger", "burger"), ("mexican", "mexican"), ("taco", "mexican"), ("burrito", "mexican"),
    ("indian", "indian"), ("thai", "thai"), ("vietnamese", "thai"), ("pho", "thai"), ("mediterranean", "mediterranean"),
    ("greek", "mediterranean"), ("middle_eastern", "mediterranean"), ("sushi", "sushi"), ("japanese", "sushi"), ("ramen", "sushi"),
    ("teahouse", "tea"), ("bubble_tea", "tea"), ("tea", "tea"), ("bakery", "bakery"), ("donut", "bakery"),
    ("sandwich", "sandwich"), ("deli", "sandwich"), ("coffee", "coffee"), ("cafe", "coffee"),
    ("brewery", "bar"), ("pub", "bar"), ("bar", "bar"), ("pharmacy", "pharmacy"), ("drugstore", "pharmacy"),
    ("charging_station", "ev"), ("gas_station", "fuel"), ("grocery", "convenience"),
    ("convenience", "convenience"), ("supermarket", "convenience"),
]
_NAME_KIND = [
    (r"\bpizza|pizzeria\b", "pizza"), (r"\bburger|grill house\b", "burger"), (r"\btaco|taqueria|burrito|mexican\b", "mexican"),
    (r"\bindian|tandoor|curry|masala|biryani|dosa\b", "indian"), (r"\bthai|pho\b|vietnam|noodle house", "thai"),
    (r"\bgyro|greek|mediterranean|kebab|falafel|shawarma|hummus\b", "mediterranean"),
    (r"\bsushi|ramen|izakaya|teriyaki\b", "sushi"),
    (r"\btea|boba|chai\b", "tea"), (r"\bbakery|bake|donut|doughnut|patisserie\b", "bakery"),
    (r"\bdeli|sandwich|subs?\b", "sandwich"), (r"\bcoffee|cafe|café|espresso|roast\b", "coffee"),
    (r"\bpub|tavern|brew|taproom|bar\b", "bar"),
]
_CATEGORY_KIND = {"coffee": "coffee", "sandwich": "sandwich", "food": "casual", "bakery": "bakery", "bar": "bar",
                  "gas": "fuel", "ev": "ev", "store": "convenience", "pharmacy": "pharmacy"}


def kind_for(category: str, name: str = "", poi_categories: list[str] | None = None) -> str | None:
    """The sample-menu kind for a place, or None when the category has nothing to order (hotels)."""
    if category not in _CATEGORY_KIND:
        return None
    cats = " ".join(poi_categories or [])
    for needle, kind in _POI_KIND:
        if needle in cats and _compatible(category, kind):
            return kind
    low = name.lower()
    for rx, kind in _NAME_KIND:
        if re.search(rx, low) and _compatible(category, kind):
            return kind
    return _CATEGORY_KIND[category]


def _compatible(category: str, kind: str) -> bool:
    """A search for gas stations never yields a pizza menu, whatever the name says."""
    if category in ("gas", "ev", "pharmacy", "store"):
        return kind == _CATEGORY_KIND[category]
    if category == "bar":
        return kind in ("bar",)
    return True


def categories_for(kind: str) -> list[dict[str, Any]]:
    return [{"name": c, "items": items} for c, items in KINDS[kind]()]


def merchant_record(feature: dict[str, Any], category: str, *, tax_rate: str = "0.0925") -> dict[str, Any] | None:
    """A catalogue merchant (``LocalCatalog`` shape) for a Mapbox feature, with a flagged sample menu."""
    kind = kind_for(category, feature.get("name", ""), feature.get("categories"))
    if kind is None:
        return None
    cat = CATEGORIES.get(category)
    fulfilment = {"fuel": "fuel", "ev": "ev", "pharmacy": "pharmacy", "convenience": "store"}.get(kind, "restaurant")
    modes = [cat.pickup] if cat else ["walk_in"]
    spot = {"fuel": "Pump 1", "ev": "Stall 1", "pharmacy": "Pharmacy counter", "convenience": "Pickup counter"}.get(
        kind, "Front counter" if "walk_in" in modes else "Curbside pickup")
    f = feature["location"]
    tags = [category, *(["food"] if category in ("coffee", "sandwich", "bakery") else [])]
    return {
        "id": f"mbx-{feature['id']}", "name": feature["name"], "address": feature.get("address") or "",
        "address_key": (feature.get("address") or feature["name"]).lower(), "city": "", "state": "",
        "location": {"lat": f.lat, "lon": f.lon}, "approx": False, "hours": "", "description": "", "cover": "",
        "tags": tags, "kind": fulfilment, "categories": categories_for(kind), "tax_rate": tax_rate,
        "prep_minutes": {"fuel": 1, "ev": 1}.get(kind, 8), "source": "mapbox", "pickup_modes": modes,
        "pickup_spot": spot, "unit_token": "", "sample": True, "sample_kind": kind, "rating": feature.get("rating"),
        "brand": feature.get("brand") or "",
    }
