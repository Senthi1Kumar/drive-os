"""Item name -> generated food image key.

Ordered keyword rules (most specific first), then a fallback by the place or menu category. Every key
has a file ``<key>.jpg`` in ``hmi_lvgl/assets/food/``.

Variety: a keyword maps to a small pool of look-alike keys (``POOLS``). ``assign_menu`` hands the pool
members out round-robin inside one menu, starting from a hash of the item id, so neighbouring distinct
items rarely share a picture. ``assign_covers`` hands the covers of a category out round-robin inside one list of
places (no repeats among the visible cards, cached per list); ``key_for_place`` picks one for a single place.
"""

from __future__ import annotations

import re
import zlib
from collections import Counter, OrderedDict
from functools import lru_cache

KEYS = (
    "espresso", "americano", "cappuccino", "latte", "flat_white", "mocha", "cold_brew", "iced_coffee",
    "chai_latte", "masala_chai", "green_tea", "black_tea", "matcha", "hot_chocolate", "smoothie",
    "orange_juice", "croissant", "muffin", "bagel", "donut", "cookie", "cake_slice", "sandwich", "wrap",
    "burger", "fries", "pizza_slice", "pasta", "salad", "sushi", "ramen", "burrito", "taco", "curry",
    "biryani", "naan", "dosa", "samosa", "steak", "chicken_wings", "soup", "ice_cream", "beer", "wine",
    "cocktail", "soda", "water_bottle", "energy_drink", "chips", "candy_bar", "pain_reliever", "bandage",
    "phone_charger", "ev_charging", "food_generic", "drink_generic", "fuel_pump", "grocery_bag",
    # specific products: distinct items inside one sample-menu kind get their own image
    "pizza_margherita", "pizza_pepperoni", "pizza_veggie", "burger_classic", "burger_bacon", "burger_veggie",
    "onion_rings", "quesadilla", "nachos", "rice_beans", "butter_chicken", "paneer_tikka", "dal_tadka",
    "california_roll", "spicy_tuna_roll", "dragon_roll", "salmon_nigiri", "sushi_combo", "roast_chicken",
    "turkey_sandwich", "italian_sub", "blt", "na_beer", "allergy_relief", "sunscreen", "sanitizer",
    "protein_bars", "ev_topup", "ev_stall", "fuel_premium",
    # dishes and cuisines
    "pad_thai", "pho", "gyro", "poke_bowl", "bibimbap", "falafel", "tiramisu", "boba_tea", "dumplings",
    "fried_rice", "hummus", "pancakes", "egg_sandwich", "hot_dog", "mac_cheese", "grilled_cheese", "baklava",
    "indian_dessert", "spring_rolls", "shrimp_plate", "grilled_salmon", "oatmeal", "parfait", "tamales",
    "guacamole", "caesar_salad", "avocado_toast", "lemonade", "agua_fresca", "breakfast_plate", "fruit_plate",
    "lo_mein", "chinese_chicken", "affogato", "cheesecake",
    # alternates for the most repeated keywords
    "latte_2", "cappuccino_2", "americano_2", "croissant_2", "cookie_2", "cake_2", "burrito_2", "salad_2",
    "beer_2", "sandwich_2", "burger_2", "tenders", "iced_tea_glass", "soup_2",
    # generic look-alike products (no logos); the brand is only in the item or option name
    "cola_can", "diet_cola_can", "zero_cola_can", "lemon_lime_can", "blue_cola_can", "maroon_soda_can",
    "energy_slim_can", "energy_black_can", "energy_white_can", "water_blue_bottle", "water_tall_bottle",
    "water_square_bottle", "sparkling_can", "tea_can", "tea_bottle", "chips_yellow_bag", "chips_orange_bag",
    "chips_kettle_bag", "candy_peanut_bar", "candy_wafer_bar", "candy_cookie_bar", "ibuprofen_generic",
    "ibuprofen_brand",
    # place covers
    "cover_cafe_1", "cover_cafe_2", "cover_restaurant_1", "cover_restaurant_2", "cover_restaurant_3",
    "cover_bar_1", "cover_bar_2", "cover_hotel_1", "cover_hotel_2", "cover_pharmacy_1", "cover_pharmacy_2",
    "cover_ev_1", "cover_ev_2", "cover_fuel_1", "cover_fuel_2", "cover_grocery_1", "cover_grocery_2",
    "cover_bakery_1", "cover_fastfood_1", "cover_fastfood_2", "cover_deli_1",
    # place covers: every category has at least eight distinct scenes
    *(f"cover_cafe_{i}" for i in range(3, 9)), *(f"cover_tea_{i}" for i in range(1, 9)),
    *(f"cover_bakery_{i}" for i in range(2, 9)), *(f"cover_deli_{i}" for i in range(2, 9)),
    *(f"cover_bar_{i}" for i in range(3, 9)), *(f"cover_fuel_{i}" for i in range(3, 9)),
    *(f"cover_ev_{i}" for i in range(3, 9)), *(f"cover_pharmacy_{i}" for i in range(3, 9)),
    *(f"cover_grocery_{i}" for i in range(3, 9)), *(f"cover_hotel_{i}" for i in range(3, 9)),
    "cover_rest_diner", "cover_rest_fine", "cover_rest_patio", "cover_rest_kitchen", "cover_rest_booth",
    "cover_rest_sushi", "cover_rest_thai", "cover_rest_indian", "cover_rest_pizza", "cover_rest_mexican",
    "cover_rest_med", "cover_rest_icecream", "cover_rest_salad", "cover_rest_chicken",
)
ALL_KEYS = KEYS

GENERIC_KEYS = frozenset({"food_generic", "drink_generic"})

_RULES: list[tuple[str, str]] = [
    # coffee and tea
    ("affogato", r"\b(affogato|shakerato)\b"),
    ("iced_coffee", r"\b(iced|ice) (coffee|latte|americano|mocha|espresso|macchiato|cappuccino|flat white|red eye)\b"),
    ("cold_brew", r"\b(cold brew|nitro)\b"),
    ("flat_white", r"\bflat white\b"),
    ("cappuccino", r"\bcappuccino\b"),
    ("masala_chai", r"\b(masala|kadak|karak) ?chai\b|\bchai tea\b"),
    ("chai_latte", r"\b(chai|milk tea|london fog|steamer|tea latte)\b"),
    ("matcha", r"\bmatcha\b"),
    ("mocha", r"\bmocha\b"),
    ("latte", r"\b(latte|macchiato|cortado|au lait)\b"),
    ("boba_tea", r"\b(boba|bubble tea|tapioca|brown sugar)\b"),
    ("americano", r"\b(americano|drip|brewed coffee|house coffee|black coffee|filter coffee|pour over|batch brew|"
                  r"red eye|coffee)\b"),
    ("espresso", r"\b(espresso|doppio|ristretto|shot)\b"),
    ("hot_chocolate", r"\b(hot chocolate|hot cocoa|cocoa)\b"),
    ("green_tea", r"\b(green tea|jasmine|sencha|genmaicha)\b"),
    ("iced_tea_glass", r"\b(iced tea|arnold palmer|sweet tea|tea\s*/\s*lem\w*|ice tea)\b"),
    ("black_tea", r"\b(tea|earl grey|oolong|chamomile|herbal|hibiscus)\b"),
    ("smoothie", r"\b(smoothie|lassi|shake|frappe|frappuccino|acai)\b"),
    ("agua_fresca", r"\b(agua fresca|horchata|jamaica|tamarindo|aguas?)\b"),
    ("lemonade", r"\b(lemonade|limeade)\b"),
    ("orange_juice", r"\b(juice|oj)\b"),
    # bakery and sweets
    ("croissant", r"\b(croissants?|danish|scone|pain au chocolat|bombolone|maritozzo|millefoglie)\b"),
    ("muffin", r"\b(muffins?|cupcakes?)\b"),
    ("bagel", r"\bbagels?\b"),
    ("donut", r"\b(donuts?|doughnuts?)\b"),
    ("cookie", r"\b(cookies?|biscotti|macarons?)\b"),
    ("tiramisu", r"\btiramisu\b"),
    ("cheesecake", r"\bcheesecake\b"),
    ("baklava", r"\b(baklava|sesame balls?)\b"),
    ("indian_dessert", r"\b(gulab jamun|kulfi|rasmalai|jalebi|halwa|kheer|panna cotta)\b"),
    ("cake_slice", r"\b(cake|brownies?|pie|tart|pastry|pastries|dessert|tres leches|cannoli|flan|churros?)\b"),
    ("ice_cream", r"\b(ice cream|gelato|sundae|frozen yogurt|sorbet|shave ice)\b"),
    ("parfait", r"\b(parfait|yogurt|granola)\b"),
    ("pancakes", r"\b(pancakes?|waffles?|french toast|crepes?)\b"),
    ("oatmeal", r"\b(oatmeal|porridge|oats|mylk)\b"),
    ("avocado_toast", r"\bavocado toast\b"),
    ("fruit_plate", r"\b(banana|apple|fruit|berries|pear)\b"),
    # breakfast
    ("egg_sandwich", r"\b(egg( ?&| and)? cheese|egg bites?|breakfast (sandwich|burrito)|brsandwich|sausage,? egg)\b"),
    # sandwiches and wraps
    ("grilled_cheese", r"\bgrilled cheese\b"),
    ("turkey_sandwich", r"\bturkey\b"),
    ("italian_sub", r"\bitalian (sub|combo)\b"),
    ("blt", r"\bb\.?l\.?t\b"),
    ("hot_dog", r"\b(hot dogs?|corn dogs?|franks?|bratwurst)\b"),
    ("gyro", r"\b(gyros?|shawarma|souvlaki|kebab|pita)\b"),
    ("falafel", r"\bfalafel\b"),
    ("hummus", r"\b(hummus|baba ganoush|tzatziki|raita|dip)\b"),
    ("sandwich", r"\b(sandwich|sub|panini|hoagie|club|reuben|melt|banh mi|toast|torta|baguette|ciabatta|"
                 r"sourdough|focaccia|the (classic|cali|cowboy|cowgirl|farmer|greek))\b"),
    ("wrap", r"\b(wrap)\b"),
    ("poke_bowl", r"\b(poke|donburi|chirashi)\b"),
    # mexican
    ("burrito", r"\b(burritos?|bowl)\b"),
    ("quesadilla", r"\bquesadillas?\b"),
    ("nachos", r"\bnachos?\b"),
    ("guacamole", r"\b(guacamole|guac|salsa|pico)\b"),
    ("tamales", r"\b(tamales?|flautas?|sopes?|enchiladas?|chile relleno|pozole|elote)\b"),
    ("rice_beans", r"\b(rice (and|&) beans|refried beans|beans)\b"),
    ("taco", r"\b(tacos?|fajitas?|al pastor|carnitas|carne asada|birria)\b"),
    # burgers, fries, pizza
    ("burger_classic", r"\b(classic (cheese)?burger|cheeseburger)\b"),
    ("burger_bacon", r"\bbacon (cheese)?burger\b"),
    ("burger_veggie", r"\bveggie burger\b"),
    ("burger", r"\b(burger|hamburger|patty|smash)\b"),
    ("onion_rings", r"\bonion rings?\b"),
    ("fries", r"\b(fries|tots|chips and queso|wedges)\b"),
    ("pizza_margherita", r"\b(margherita|naples|basilcata|tuscany|terra luna)\b"),
    ("pizza_pepperoni", r"\bpepperoni pizza\b"),
    ("pizza_veggie", r"\b(veggie|vegetable|garden|vegetariano|ultimate veggie) (pizza)?\b"),
    ("pizza_slice", r"\b(pizza|calzone|slice|stromboli)\b"),
    # asian
    ("pho", r"\bpho\b"),
    ("ramen", r"\b(ramen|udon|soba|noodle soup|laksa|tonkotsu)\b"),
    ("pad_thai", r"\b(pad thai|pad see ew|pad kee mao|drunken noodles?|thai (beef|noodles?))\b"),
    ("lo_mein", r"\b(lo mein|chow mein|chow fun|noodles?)\b"),
    ("bibimbap", r"\b(bibimbap|bulgogi|kimchi|korean|japchae)\b"),
    ("california_roll", r"\bcalifornia roll\b"),
    ("spicy_tuna_roll", r"\bspicy tuna\b"),
    ("dragon_roll", r"\bdragon roll\b"),
    ("salmon_nigiri", r"\bnigiri\b"),
    ("sushi_combo", r"\bsushi (combo|platter|set|boat)\b"),
    ("sushi", r"\b(sushi|sashimi|maki|temaki|edamame)\b|\b\w+ roll\b"),
    ("spring_rolls", r"\b(spring ?rolls?|egg rolls?|tempura|crab cakes?)\b"),
    ("dumplings", r"\b(dumplings?|gyoza|potstickers?|bao|momos?|wontons?|empanadas?|pierogi|scallion pancake)\b"),
    ("chinese_chicken", r"\b(orange chicken|kung pao|general tso|sesame chicken|sweet (and|&) sour|"
                        r"teriyaki|mongolian|szechuan)\b"),
    ("fried_rice", r"\b(fried rice|(side )?(of )?rice|pilaf|rice tray|rice bowl)\b"),
    # indian
    ("dosa", r"\b(dosa|idli|uttapam|vada|sambar)\b"),
    ("biryani", r"\b(biryani|pulao)\b"),
    ("naan", r"\b(naan|roti|paratha|chapati|garlic bread|flatbread|bread|cinnanaan)\b"),
    ("samosa", r"\b(samosas?|pakora|bhaji)\b"),
    ("butter_chicken", r"\bbutter chicken\b"),
    ("paneer_tikka", r"\bpaneer\b"),
    ("dal_tadka", r"\b(dal|daal|chana|lentil)\b"),
    ("curry", r"\b(curry|masala|tikka|korma|vindaloo|saag|chili oil)\b"),
    # mains
    ("mac_cheese", r"\bmac( ?(and|&) ?)?cheese\b"),
    ("pasta", r"\b(pasta|spaghetti|penne|lasagna|fettuccine|ravioli|gnocchi|carbonara|alfredo|linguine|"
              r"rigatoni|pavarotti|doccia)\b"),
    ("caesar_salad", r"\bcaesar\b"),
    ("salad", r"\b(salad|slaw|greens|caprese|brussels|broccoli)\b"),
    ("soup", r"\b(soup|chowder|bisque|stew|broth|chili)\b"),
    ("grilled_salmon", r"\b(salmon|fish|sea bass|cod|trout|tuna|mahi|halibut|seafood)\b"),
    ("shrimp_plate", r"\b(shrimp|prawns?|lobster|scallops?|crab|calamari)\b"),
    ("roast_chicken", r"\broast(ed)? chicken\b|\bchicken (plate|dinner|breast|thigh)\b"),
    ("chicken_wings", r"\b(wings?|drumsticks?)\b"),
    ("tenders", r"\b(chicken|nuggets|tenders|fingers|strips)\b"),
    ("steak", r"\b(steak|ribeye|filet|sirloin|ribs|brisket|bbq|pork|lamb|chop|roast|meatballs?|beef)\b"),
    ("breakfast_plate", r"\b(scrambled|omelet|omelette|breakfast|loco moco|chilaquiles|hash brown|bacon|sausage|"
                        r"eggs?|shrimp & grits|grits)\b"),
    # drinks
    ("na_beer", r"\bnon-? ?alcoholic\b"),
    ("beer", r"\b(beer|ipa|lager|ale|stout|pilsner|crowler|growler|pint|hazy)\b"),
    ("wine", r"\b(wine|merlot|chardonnay|cabernet|prosecco|champagne|rose|pinot)\b"),
    ("cocktail", r"\b(cocktail|margarita|mojito|martini|negroni|spritz|old fashioned|whiskey|vodka|gin|rum|"
                 r"mimosa|sangria|tequila|anejo|reposado|blanco|whisky|bourbon|scotch|mezcal|cognac|liqueur|brandy)\b"),
    ("sparkling_can", r"\b(perrier|la croix|pellegrino|sparkling|seltzer|club soda|tonic)\b"),
    ("water_bottle", r"\b(water|hydration)\b"),
    ("energy_drink", r"\b(energy|red ?bull|monster|rockstar|celsius|gatorade|powerade)\b"),
    ("soda", r"\b(soda|cola|coke|pepsi|sprite|fanta|jarritos|thums up|limca|ramune|calpico|aloha-?|dr pepper|ginger ale|ginger soda|"
             r"root beer|lemon lime|pop|fountain drink)\b"),
    # snacks and errands
    ("chips", r"\b(chips|crisps|popcorn|pretzels?|trail mix|nuts|jerky|snacks?|crackers)\b"),
    ("protein_bars", r"\bprotein bars?\b"),
    ("candy_bar", r"\b(candy|chocolate|granola bar|gum|mints|gummies|snickers|kitkat|twix)\b"),
    ("allergy_relief", r"\ballergy\b|\bantihistamine\b"),
    ("sunscreen", r"\b(sunscreen|spf)\b"),
    ("pain_reliever", r"\b(ibuprofen|advil|tylenol|acetaminophen|aspirin|allergy|antihistamine|pain|relief|"
                      r"vitamins?|medicine|tablets|capsules|cold|flu|cough|sunscreen)\b"),
    ("sanitizer", r"\bsaniti[sz]er\b"),
    ("bandage", r"\b(bandages?|band aid|first aid|sanitizer|wipes|gauze)\b"),
    ("phone_charger", r"\b(phone charger|charging cable|usb|cable|adapter|charger|earbuds|power bank)\b"),
    ("ev_topup", r"\btop ?-?up\b"),
    ("ev_stall", r"\b(stall|reserve)\b"),
    ("ev_charging", r"\b(fast charge|top up|top-up|kwh|stall|ev charging|charging session|charge)\b"),
    ("fuel_premium", r"\bpremium\b"),
    ("fuel_pump", r"\b(fuel|regular|premium|diesel|unleaded|gasoline|gas|car wash|wash)\b"),
    ("hummus", r"\b(sauce|ranch|ketchup|mayo|mustard|relish|dressing|queso|sour cream|honey|ponzu)\b"),
    ("salad_2", r"\b(lettuce|onion|peppers?|tomato|pickles?|cheese|veggies|spinach|tobiko|masago)\b"),
    ("grocery_bag", r"\b(groceries|grocery|flowers|bouquet|umbrella|produce|milk|eggs)\b"),
]
_COMPILED = [(k, re.compile(p)) for k, p in _RULES]

# keyword -> look-alike pool; the first member is the plain base image
POOLS: dict[str, tuple[str, ...]] = {
    "latte": ("latte", "latte_2"), "cappuccino": ("cappuccino", "cappuccino_2"),
    "americano": ("americano", "americano_2"), "croissant": ("croissant", "croissant_2"),
    "cookie": ("cookie", "cookie_2"), "cake_slice": ("cake_slice", "cake_2"),
    "burrito": ("burrito", "burrito_2"), "salad": ("salad", "salad_2"), "beer": ("beer", "beer_2"),
    "sandwich": ("sandwich", "sandwich_2"), "burger": ("burger", "burger_2"), "soup": ("soup", "soup_2"),
    "black_tea": ("black_tea", "iced_tea_glass"),
    "water_bottle": ("water_bottle", "water_blue_bottle", "water_tall_bottle", "water_square_bottle"),
    "soda": ("soda", "cola_can", "blue_cola_can", "maroon_soda_can", "lemon_lime_can"),
    "energy_drink": ("energy_drink", "energy_slim_can", "energy_black_can", "energy_white_can"),
    "chips": ("chips", "chips_yellow_bag", "chips_orange_bag", "chips_kettle_bag"),
    "candy_bar": ("candy_bar", "candy_peanut_bar", "candy_wafer_bar", "candy_cookie_bar"),
    "pain_reliever": ("pain_reliever", "ibuprofen_generic"),
    "sushi": ("sushi", "california_roll", "spicy_tuna_roll", "dragon_roll", "sushi_combo", "salmon_nigiri"),
    "hot_chocolate": ("hot_chocolate", "mocha"), "chai_latte": ("chai_latte", "masala_chai"),
    "cocktail": ("cocktail", "wine"), "pho": ("pho", "ramen"), "pad_thai": ("pad_thai", "lo_mein", "fried_rice"),
    "bar_generic": ("beer", "cocktail", "wine", "beer_2"),
    "coffee_generic": ("cappuccino", "latte_2", "americano_2", "cappuccino_2", "flat_white"),
    "food_generic": ("food_generic", "roast_chicken", "pasta", "fried_rice", "salad_2"),
    "drink_generic": ("drink_generic", "lemonade", "iced_tea_glass"),
}

# fallback by place category / sample kind
_BY_CATEGORY = {
    "coffee": "coffee_generic", "tea": "black_tea", "bakery": "croissant", "bar": "bar_generic", "gas": "fuel_pump",
    "fuel": "fuel_pump", "ev": "ev_charging", "store": "grocery_bag", "pharmacy": "pain_reliever",
    "sandwich": "sandwich", "food": "food_generic", "restaurant": "food_generic", "pizza": "pizza_slice",
    "burger": "burger", "mexican": "taco", "indian": "curry", "sushi": "sushi",
}
# fallback by menu section when no keyword matches (the section says more than the place)
_SECTION_KEYS = [
    ("breakfast_plate", r"\b(breakfast|brunch|eggs?)\b"),
    ("cake_slice", r"\b(desserts?|sweets?|bakery|pastr(y|ies)|treats?)\b"),
    ("salad", r"\bsalads?\b"),
    ("soup", r"\bsoups?\b"),
    ("sandwich", r"\b(sandwich(es)?|subs?|wraps?|melts?)\b"),
    ("pizza_slice", r"\bpizzas?\b"),
    ("pasta", r"\bpastas?\b"),
    ("burger", r"\bburgers?\b"),
    ("taco", r"\b(tacos?|mexican)\b"),
    ("curry", r"\b(curries|curry|indian)\b"),
    ("sushi", r"\b(sushi|rolls?|nigiri)\b"),
    ("fries", r"\b(sides?|extras?)\b"),
    ("spring_rolls", r"\b(appetizers?|starters?|small plates?|snacks?)\b"),
    ("steak", r"\b(entr[eé]es?|mains?|grill)\b"),
]
_SECTION_COMPILED = [(k, re.compile(p)) for k, p in _SECTION_KEYS]
_DRINK_SECTIONS = re.compile(r"\b(drinks?|beverages?|coffee|tea|juices?|sodas?|cold|refreshers?|smoothies?)\b")
_TYPOS = (("capuccino", "cappuccino"), ("icedlatte", "iced latte"), ("chailatte", "chai latte"),
          ("icedtea", "iced tea"), ("hotchocolate", "hot chocolate"), ("capucino", "cappuccino"))


@lru_cache(maxsize=8192)
def base_key(name: str, category: str = "", section: str = "") -> str:
    """The keyword image for a menu item, before any alternate is picked."""
    text = re.sub(r"[^\w' &/.-]", " ", name.lower().replace("·", " "))
    for bad, good in _TYPOS:
        text = text.replace(bad, good)
    for key, rx in _COMPILED:
        if rx.search(text):
            return key
    sec = section.lower()
    for key, rx in _SECTION_COMPILED:
        if rx.search(sec) and not _DRINK_SECTIONS.search(sec):
            return key
    if _DRINK_SECTIONS.search(sec):
        return "drink_generic"
    return _BY_CATEGORY.get(category, "food_generic")


def pool_for(base: str) -> tuple[str, ...]:
    return POOLS.get(base, (base,))


def _h(s: str) -> int:
    return zlib.crc32(s.encode("utf-8"))


def key_for(name: str, category: str = "", section: str = "", item_id: str = "") -> str:
    """Image key for a menu item. ``category`` is the place category or kind; ``section`` the menu section.
    With an ``item_id`` an alternate from the keyword's pool is chosen deterministically."""
    pool = pool_for(base_key(name, category, section))
    return pool[_h(item_id) % len(pool)] if item_id else pool[0]


def assign_menu(items, category: str = "") -> dict[str, str]:
    """Image key per item id for one menu: alternates are handed out so the same picture is not repeated while
    an unused look-alike exists. Items with a real photo get an empty key."""
    used: Counter[str] = Counter()
    out: dict[str, str] = {}
    for it in items:
        if getattr(it, "image_url", None):
            out[it.id] = ""
            continue
        pool = pool_for(base_key(it.name, category, it.category or ""))
        start = _h(it.id) % len(pool)
        order = pool[start:] + pool[:start]
        pick = min(order, key=lambda k: (used[k], order.index(k)))
        used[pick] += 1
        out[it.id] = pick
    return out


def _series(prefix: str, n: int = 8) -> tuple[str, ...]:
    return tuple(f"{prefix}_{i}" for i in range(1, n + 1))


_RESTAURANT_POOL = ("cover_restaurant_1", "cover_restaurant_2", "cover_restaurant_3", "cover_rest_diner",
                    "cover_rest_fine", "cover_rest_patio", "cover_rest_kitchen", "cover_rest_booth")
# one pool per place category or sample kind; every pool has at least eight different scenes
_COVERS: dict[str, tuple[str, ...]] = {
    "coffee": _series("cover_cafe"), "tea": _series("cover_tea"), "bakery": _series("cover_bakery"),
    "sandwich": _series("cover_deli"),
    "food": _RESTAURANT_POOL, "restaurant": _RESTAURANT_POOL, "casual": _RESTAURANT_POOL,
    "bar": _series("cover_bar"), "gas": _series("cover_fuel"), "fuel": _series("cover_fuel"),
    "ev": _series("cover_ev"), "store": _series("cover_grocery"), "convenience": _series("cover_grocery"),
    "pharmacy": _series("cover_pharmacy"), "hotel": _series("cover_hotel"),
}
# cuisine -> the cover for that cuisine, tried first for a restaurant when the POI category or name hints at it
CUISINE_COVERS = {
    "sushi": "cover_rest_sushi", "thai": "cover_rest_thai", "indian": "cover_rest_indian",
    "pizza": "cover_rest_pizza", "mexican": "cover_rest_mexican", "mediterranean": "cover_rest_med",
    "burger": "cover_fastfood_1", "icecream": "cover_rest_icecream", "salad": "cover_rest_salad",
    "chicken": "cover_rest_chicken",
}
_CUISINE_POI = (  # Mapbox poi_category ids, first match wins
    ("pizza", "pizza"), ("burger", "burger"), ("mexican", "mexican"), ("taco", "mexican"), ("burrito", "mexican"),
    ("indian", "indian"), ("thai", "thai"), ("vietnamese", "thai"), ("pho", "thai"),
    ("mediterranean", "mediterranean"), ("greek", "mediterranean"), ("middle_eastern", "mediterranean"),
    ("sushi", "sushi"), ("japanese", "sushi"), ("ramen", "sushi"), ("ice_cream", "icecream"), ("gelato", "icecream"),
    ("salad", "salad"), ("chicken", "chicken"), ("wings", "chicken"),
)
_CUISINE_NAME = (
    (r"\b(pizza|pizzeria)\b", "pizza"), (r"\b(burgers?|burger)\b", "burger"),
    (r"\b(taco|tacos|taqueria|burrito|mexican|cantina)\b", "mexican"),
    (r"\b(indian|tandoor|tandoori|curry|masala|biryani|dosa|tikka)\b", "indian"),
    (r"\b(thai|pho|vietnam\w*|pad)\b", "thai"),
    (r"\b(sushi|ramen|izakaya|teriyaki|sashimi|hibachi)\b", "sushi"),
    (r"\b(hummus|mediterranean|greek|gyros?|kebab|falafel|shawarma|pita)\b", "mediterranean"),
    (r"\b(ice cream|creamery|gelato|frozen yogurt|froyo|custard)\b", "icecream"),
    (r"\b(superfoods?|salads?|greens|poke)\b", "salad"),
    (r"\b(chicken|wings?|tenders)\b", "chicken"),
)
_CUISINE_NAME_RX = tuple((re.compile(p), c) for p, c in _CUISINE_NAME)
_CUISINE_CATEGORIES = frozenset({"food", "restaurant", "casual", ""})


def cuisine_for(name: str = "", kind: str = "", poi_categories=()) -> str:
    """Cuisine hinted by the sample kind, the Mapbox POI category ids or the place name ('' when none)."""
    if kind in CUISINE_COVERS:
        return kind
    cats = " ".join(poi_categories or ())
    for needle, cuisine in _CUISINE_POI:
        if needle in cats:
            return cuisine
    low = name.lower()
    for rx, cuisine in _CUISINE_NAME_RX:
        if rx.search(low):
            return cuisine
    return ""


def cover_pool(category: str, kind: str = "") -> tuple[str, ...]:
    return _COVERS.get(kind) or _COVERS.get(category) or _COVERS["food"]


def key_for_place(category: str, kind: str = "", place_id: str = "", name: str = "", poi_categories=()) -> str:
    """Cover image key for one place with no brand photo: the cuisine cover when the place hints at one, else one
    of the category's covers chosen by place id. Use ``assign_covers`` for a list so no cover repeats in it."""
    cuisine = cuisine_for(name, kind, poi_categories) if category in _CUISINE_CATEGORIES else ""
    if cuisine:
        return CUISINE_COVERS[cuisine]
    pool = cover_pool(category, kind)
    return pool[_h(place_id) % len(pool)] if place_id else pool[0]


# ----------------------------------------------------------------------------- covers for a whole list
VISIBLE_PLACES = 8            # the first cards of a list (Shop shows 6, the Home strip up to 8) never repeat a cover
_COVER_CACHE: OrderedDict[tuple, dict[str, str]] = OrderedDict()
_COVER_CACHE_MAX = 64


def _place_fields(p) -> tuple[str, str, str, tuple[str, ...]]:
    extra = getattr(p, "extra", None) or {}
    return (getattr(p, "category", "") or "", extra.get("sample_kind") or "", getattr(p, "name", "") or "",
            tuple(extra.get("poi_categories") or ()))


def assign_covers(places, kinds: dict[str, str] | None = None) -> dict[str, str]:
    """Cover key per place id for one list. Round-robin over each category's pool in the order of a stable hash of
    the place id, with a cuisine cover first for a restaurant that hints at one; no key repeats inside the first
    ``VISIBLE_PLACES`` places while a pool member is left. The result is cached per list (ids, categories, kinds,
    names), so a new snapshot of the same list keeps every place's picture."""
    items = []
    for p in places:
        cat, kind, name, pois = _place_fields(p)
        kind = (kinds or {}).get(p.id, kind)
        items.append((p.id, cat, kind, name, pois))
    key = tuple((i, c, k, n, pois) for i, c, k, n, pois in items)
    hit = _COVER_CACHE.get(key)
    if hit is not None:
        _COVER_CACHE.move_to_end(key)
        return dict(hit)
    out: dict[str, str] = {}
    used: Counter[str] = Counter()

    def pick(pid: str, cat: str, kind: str, name: str, pois) -> str:
        pool = cover_pool(cat, kind)
        order = list(pool[_h(pid) % len(pool):] + pool[:_h(pid) % len(pool)])
        cuisine = cuisine_for(name, kind, pois) if cat in _CUISINE_CATEGORIES else ""
        if cuisine:
            order.insert(0, CUISINE_COVERS[cuisine])
        return min(order, key=lambda k: (used[k], order.index(k)))

    ranked = sorted(items[:VISIBLE_PLACES], key=lambda t: (_h(t[0]), t[0])) + \
        sorted(items[VISIBLE_PLACES:], key=lambda t: (_h(t[0]), t[0]))
    for pid, cat, kind, name, pois in ranked:
        k = pick(pid, cat, kind, name, pois)
        used[k] += 1
        out[pid] = k
    _COVER_CACHE[key] = dict(out)
    while len(_COVER_CACHE) > _COVER_CACHE_MAX:
        _COVER_CACHE.popitem(last=False)
    return out
