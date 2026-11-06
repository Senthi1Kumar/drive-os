"""What people can ask for, and how each kind of stop is found, shown and fulfilled.

One table drives the voice parser (keywords), discovery (tags, Mapbox / Google category ids), the UI
(labels, icons) and fulfilment (pickup mode).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Category:
    id: str
    label: str            # screen title, e.g. "Coffee"
    one: str              # spoken singular, e.g. "coffee shop"
    many: str             # spoken plural
    icon: str             # Icon.qml glyph name
    keywords: tuple[str, ...]
    mapbox: tuple[str, ...] = ()       # Mapbox Search Box canonical category ids
    google: str = ""                   # Google Places (New) includedType
    pickup: str = "walk_in"
    parents: tuple[str, ...] = field(default_factory=tuple)  # broader categories that also match


CATEGORIES: dict[str, Category] = {c.id: c for c in [
    Category("coffee", "Coffee", "coffee shop", "coffee shops", "coffee",
             ("coffee", "latte", "espresso", "cappuccino", "cafe", "café", "mocha", "cold brew", "tea", "chai", "matcha"),
             ("coffee", "cafe"), "coffee_shop", "curbside"),
    Category("sandwich", "Sandwiches", "sandwich place", "sandwich places", "sandwich",
             ("sandwich", "sandwiches", "sub", "subs", "deli", "hoagie", "panini", "wrap", "bagel", "bagels", "sourdough"),
             ("sandwich_shop", "deli"), "sandwich_shop", "curbside", ("food",)),
    Category("food", "Food", "place to eat", "places to eat", "food",
             ("food", "eat", "lunch", "dinner", "breakfast", "brunch", "hungry", "meal", "restaurant", "burger",
              "pizza", "tacos", "taco", "sushi", "chinese", "thai", "indian", "mexican", "noodles", "ramen", "salad",
              "bowl", "chicken", "pasta", "bbq", "pho", "poke", "snack"),
             ("restaurant", "fast_food", "food"), "restaurant", "curbside"),
    Category("bakery", "Bakery", "bakery", "bakeries", "bakery",
             ("bakery", "pastry", "pastries", "croissant", "donut", "donuts", "doughnut", "bread", "cake", "muffin"),
             ("bakery",), "bakery", "curbside", ("food",)),
    Category("bar", "Bars", "bar", "bars", "bar",
             ("bar", "bars", "pub", "brewery", "taproom", "beer", "wine", "cocktail", "cocktails", "drinks"),
             ("bar", "pub", "brewery"), "bar", "walk_in"),
    Category("gas", "Fuel", "gas station", "gas stations", "fuel",
             ("gas", "gas station", "fuel", "petrol", "diesel", "fill up", "fill the tank", "refuel", "unleaded"),
             ("gas_station",), "gas_station", "pump"),
    Category("ev", "EV charging", "charger", "chargers", "bolt",
             ("charger", "chargers", "charging", "charge the car", "ev", "supercharger", "plug in", "electric"),
             ("charging_station",), "electric_vehicle_charging_station", "charger"),
    Category("store", "Store", "store", "stores", "bag",
             ("store", "market", "grocery", "groceries", "convenience", "supermarket", "water", "snacks",
              "phone charger", "charging cable", "essentials", "milk", "eggs", "flowers", "gift"),
             ("convenience_store", "supermarket", "grocery"), "convenience_store", "in_store"),
    Category("hotel", "Hotels", "hotel", "hotels", "bed",
             ("hotel", "hotels", "motel", "lodging"), ("hotel", "lodging"), "lodging", "walk_in"),
    Category("pharmacy", "Pharmacy", "pharmacy", "pharmacies", "pill",
             ("pharmacy", "pharmacies", "drugstore", "drugstores", "medicine", "ibuprofen", "advil", "tylenol", "allergy", "bandage", "cvs",
              "walgreens"),
             ("pharmacy",), "pharmacy", "in_store", ("store",)),
]}

# tie-break when two keywords are equally long: specific before general
_MATCH_ORDER = ["store", "pharmacy", "hotel", "gas", "ev", "bar", "coffee", "bakery", "sandwich", "food"]


def detect(text: str) -> tuple[str | None, str | None]:
    """Category id and the keyword that matched (used as a search hint), or (None, None).

    The longest matching keyword wins, so "phone charger" is a store errand, not EV charging,
    "gas station" beats "station", and "sandwich bar" is a sandwich place.
    """
    t = " " + re.sub(r"[^\w' ]", " ", text.lower()) + " "
    best: tuple[int, int, str, str] | None = None
    for rank, cid in enumerate(_MATCH_ORDER):
        for kw in CATEGORIES[cid].keywords:
            if re.search(r"(?<![\w'])" + re.escape(kw) + r"(?![\w'])", t):
                key = (len(kw), -rank, cid, kw)
                if best is None or key[:2] > best[:2]:
                    best = key
    return (best[2], best[3]) if best else (None, None)


def matches(place_tags: list[str], wanted: str) -> bool:
    if wanted in ("", "any"):
        return True
    if wanted in place_tags:
        return True
    cat = CATEGORIES.get(wanted)
    # asking for "food" also finds sandwich shops and bakeries
    return any(wanted in CATEGORIES[t].parents for t in place_tags if t in CATEGORIES) if cat else False


_TAG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("coffee", ("coffee", "espresso", "latte", "cafe", "café", "roaster", "tea house", "boba")),
    ("sandwich", ("sandwich", "deli", "sub ", "subs", "hoagie", "panini", "bagel", "sourdough")),
    ("bakery", ("bakery", "bake", "pastry", "patisserie", "donut", "doughnut", "croissant", "bread")),
    ("bar", ("bar", "pub", "tavern", "brew", "taproom", "saloon", "cocktail", "wine", "chop house", "lounge")),
]


def classify(name: str, menu_words: list[str] | None = None) -> list[str]:
    """Tags for a restaurant from its name and menu category names. Every restaurant is also "food"."""
    hay = " " + name.lower() + " "
    words = " ".join(menu_words or []).lower()
    tags: list[str] = []
    for tag, kws in _TAG_RULES:
        if any(k in hay for k in kws):
            tags.append(tag)
    if "bar" in tags and any(x in hay for x in ("coffee bar", "juice bar", "salad bar", "espresso bar", "health bar", "bar & grill")):
        tags.remove("bar")
    # a menu that is mostly coffee drinks makes a coffee shop even without "coffee" in the name
    if "coffee" not in tags and sum(w in words for w in ("latte", "espresso", "cappuccino", "americano")) >= 2:
        tags.append("coffee")
    if "sandwich" not in tags and sum(w in words for w in ("sandwich", "sandwiches", "deli", "bagel")) >= 1:
        tags.append("sandwich")
    if "food" not in tags:
        tags.append("food")
    return tags
