"""Modifier groups shared by the sample menus and the demo venues.

Two kinds of variants, both expressed with the ordinary ``ModifierGroup`` / ``ModifierOption`` model:

* choices with a price: size, hot or iced, milk, syrup, toppings, pizza size, burger add-ons, spice level;
* brand sub-products for drinks, snacks and over-the-counter items. The group id is ``brand``; each option
  carries an ``image`` key of a generic look-alike product picture (no logos or packaging; the brand is text
  only) so the item photo follows the selection, and aliases so a spoken brand ("two Diet Cokes") finds the item.

Real catalogue items (Square) keep their real modifiers; none of this is applied to them.
"""

from __future__ import annotations


def opt(oid: str, name: str, delta: int = 0, default: bool = False, aliases: tuple[str, ...] = (),
        image: str = "") -> dict:
    return {"id": oid, "name": name, "price_delta": {"amount": delta, "currency": "USD"}, "default": default,
            "aliases": [name.lower(), *aliases], "image": image}


def grp(gid: str, name: str, opts: list[dict], aliases: tuple[str, ...] = (), *, max_select: int = 1,
        min_select: int = 1) -> dict:
    return {"id": gid, "name": name, "min_select": min_select, "max_select": max_select, "options": opts,
            "aliases": list(aliases)}


# ---------------------------------------------------------------- choices with a price
SIZE = grp("size", "Size", [opt("s12", "Small 12 oz", 0, aliases=("small",)),
                            opt("s16", "Medium 16 oz", 60, True, aliases=("medium", "regular")),
                            opt("s20", "Large 20 oz", 110, aliases=("large",))], ("size",))
TEA_SIZE = grp("size", "Size", [opt("s12", "Regular 12 oz", 0, True, aliases=("small", "regular")),
                                opt("s16", "Large 16 oz", 80, aliases=("large",))], ("size",))
TEMP = grp("temp", "Hot or iced", [opt("hot", "Hot", 0, True, aliases=("steamed",)),
                                   opt("iced", "Iced", 30, aliases=("ice", "cold", "over ice"))],
           ("temperature", "hot or iced"))
MILK = grp("milk", "Milk", [opt("whole", "Whole milk", 0, True, aliases=("whole",)),
                            opt("oat", "Oat milk", 70, aliases=("oat",)),
                            opt("almond", "Almond milk", 70, aliases=("almond",)),
                            opt("soy", "Soy milk", 60, aliases=("soy",)),
                            opt("skim", "Skim milk", 0, aliases=("skim", "nonfat"))], ("milk",))
SYRUP = grp("syrup", "Syrup", [opt("vanilla", "Vanilla", 50), opt("caramel", "Caramel", 50),
                               opt("hazelnut", "Hazelnut", 50)], ("syrup", "flavor", "flavour"), min_select=0)
BOBA_TOPPINGS = grp("toppings", "Toppings", [opt("pearls", "Tapioca pearls", 75, aliases=("pearls", "boba", "tapioca")),
                                             opt("lychee", "Lychee jelly", 75, aliases=("lychee",)),
                                             opt("pudding", "Egg pudding", 75, aliases=("pudding",))],
                    ("toppings", "topping"), max_select=2, min_select=0)
PIZZA_SIZE = grp("size", "Size", [opt("p10", "Personal 10 in", 0, aliases=("personal", "small")),
                                  opt("p14", "Medium 14 in", 600, True, aliases=("medium",)),
                                  opt("p18", "Large 18 in", 1100, aliases=("large",))], ("size",))
PIZZA_TOP = grp("extras", "Extra toppings", [opt("pep", "Pepperoni", 150, aliases=("extra pepperoni",)),
                                             opt("mush", "Mushrooms", 120, aliases=("mushroom",)),
                                             opt("olive", "Olives", 100, aliases=("olive",))],
                ("toppings", "extra"), max_select=3, min_select=0)
BURGER_ADDONS = grp("addons", "Add-ons", [opt("bacon", "Bacon", 200), opt("patty", "Extra patty", 300, aliases=("double",)),
                                          opt("avocado", "Avocado", 150), opt("egg", "Fried egg", 120)],
                    ("add-ons", "extras", "add"), max_select=4, min_select=0)
SPICE = grp("spice", "Spice", [opt("mild", "Mild", 0), opt("med", "Medium", 0, True), opt("hot", "Hot", 0, aliases=("spicy",)),
                               opt("thai", "Thai hot", 0, aliases=("extra hot", "very spicy"))],
            ("spice", "heat", "spicy"))

# ---------------------------------------------------------------- brand sub-products (generic look-alike images)
SODA_BRAND = grp("brand", "Brand", [
    opt("coke", "Coca-Cola", 0, True, ("coke", "coca cola", "cola"), "cola_can"),
    opt("diet", "Diet Coke", 0, False, ("diet cola", "diet"), "diet_cola_can"),
    opt("zero", "Coke Zero", 0, False, ("coke zero", "zero"), "zero_cola_can"),
    opt("sprite", "Sprite", 0, False, ("lemon lime",), "lemon_lime_can"),
    opt("pepsi", "Pepsi", 0, False, (), "blue_cola_can"),
    opt("drpepper", "Dr Pepper", 0, False, ("dr. pepper", "doctor pepper"), "maroon_soda_can"),
], ("brand", "kind", "flavor"))
ENERGY_BRAND = grp("brand", "Brand", [
    opt("redbull", "Red Bull", 0, True, ("redbull",), "energy_slim_can"),
    opt("monster", "Monster", 0, False, ("monster energy",), "energy_black_can"),
    opt("celsius", "Celsius", 50, False, (), "energy_white_can"),
], ("brand", "kind"))
WATER_BRAND = grp("brand", "Brand", [
    opt("dasani", "Dasani", 0, True, (), "water_blue_bottle"),
    opt("smartwater", "Smartwater", 100, False, ("smart water",), "water_tall_bottle"),
    opt("fiji", "Fiji", 150, False, (), "water_square_bottle"),
    opt("sparkling", "Sparkling", 0, False, ("sparkling water", "seltzer"), "sparkling_can"),
], ("brand", "kind"))
TEA_BRAND = grp("brand", "Brand", [
    opt("arizona", "Arizona", 0, True, ("arizona iced tea",), "tea_can"),
    opt("pureleaf", "Pure Leaf", 100, False, ("pure leaf iced tea",), "tea_bottle"),
], ("brand", "kind"))
CHIPS_BRAND = grp("brand", "Brand", [
    opt("lays", "Lay's", 0, True, ("lays", "lay's classic", "potato chips"), "chips_yellow_bag"),
    opt("doritos", "Doritos", 0, False, ("dorito", "tortilla chips"), "chips_orange_bag"),
    opt("kettle", "Kettle", 40, False, ("kettle chips", "kettle brand"), "chips_kettle_bag"),
], ("brand", "kind"))
CANDY_BRAND = grp("brand", "Brand", [
    opt("snickers", "Snickers", 0, True, (), "candy_peanut_bar"),
    opt("kitkat", "KitKat", 0, False, ("kit kat",), "candy_wafer_bar"),
    opt("twix", "Twix", 0, False, (), "candy_cookie_bar"),
], ("brand", "kind"))
IBUPROFEN_BRAND = grp("brand", "Brand", [
    opt("generic", "Generic ibuprofen", 0, True, ("generic",), "ibuprofen_generic"),
    opt("advil", "Advil", 300, False, (), "ibuprofen_brand"),
], ("brand", "kind"))
ACETAMINOPHEN_BRAND = grp("brand", "Brand", [
    opt("generic", "Generic acetaminophen", 0, True, ("generic",), "pain_reliever"),
    opt("tylenol", "Tylenol", 300, False, (), "ibuprofen_generic"),
], ("brand", "kind"))
ALLERGY_BRAND = grp("brand", "Brand", [
    opt("generic", "Generic loratadine", 0, True, ("generic",), "allergy_relief"),
    opt("claritin", "Claritin", 400, False, (), "ibuprofen_brand"),
    opt("zyrtec", "Zyrtec", 500, False, (), "pain_reliever"),
], ("brand", "kind"))
