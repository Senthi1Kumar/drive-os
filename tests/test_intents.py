from __future__ import annotations

import pytest

from driveos.agent.intents import Context, IntentParser, spoken_digits
from driveos.domain.categories import classify, detect
from driveos.voice.wake import split_wake

p = IntentParser()
CRUST = "r7-crust-sourdough-deli-los-gatos"
PLUR = "r3-plur-coffee"


@pytest.mark.parametrize("text,cat", [
    ("find me a coffee shop", "coffee"), ("I need gas", "gas"), ("any sandwich places nearby", "sandwich"),
    ("find a bar", "bar"), ("where can I charge the car", "ev"), ("I need a phone charger", "store"),
    ("get me some ibuprofen", "pharmacy"), ("I'm hungry", "food"), ("pick up groceries", "store"),
    ("sandwich bar", "sandwich"), ("fill up the tank", "gas"),
])
def test_category_detection(text, cat):
    assert detect(text)[0] == cat


def test_classify_restaurants():
    assert "coffee" in classify("Plur Coffee")
    assert "bar" in classify("Heritage Pub & Chop House")
    assert "bar" not in classify("Kenz Coffee Bar")
    assert "sandwich" in classify("CRUST Sourdough Deli")
    assert classify("Orange Bowl") == ["food"]


@pytest.mark.parametrize("text", ["Hey Nova find me a coffee", "find me a coffee", "coffee near me"])
def test_find(text):
    i = p.parse(text, Context())
    assert i.name == "find" and i.slots["category"] == "coffee"


def test_find_specific_item_keeps_query():
    i = p.parse("where can I get a latte", Context())
    assert i.name == "find" and i.slots["query"] == "latte"


def test_select_place():
    ctx = Context(view="places", places=["CRUST Sourdough Deli Los Gatos", "Orange Bowl", "The Posh Bagel"])
    assert p.parse("the first one", ctx).slots["index"] == 0
    assert p.parse("the posh bagel", ctx).slots["index"] == 2
    assert p.parse("number two", ctx).slots["index"] == 1
    assert p.parse("take me to the third one", ctx).name == "drive_to_place"


def test_items_on_real_menu(catalog):
    m = catalog.menu(CRUST)
    i = p.parse("can I get two pastrami sandwiches and a cookie", Context(view="menu", menu=m))
    assert i.name == "add_items"
    got = [(m.item(x["item_id"]).name, x["qty"]) for x in i.slots["items"]]
    assert got == [('6" Pastrami Sandwich', 2), ("Signature Cookies", 1)]
    i = p.parse("add a turkey sandwich", Context(view="menu", menu=m))
    assert m.item(i.slots["items"][0]["item_id"]).name == '6" Oven Roasted Turkey Sandwich'


def test_section_request_asks_which(catalog):
    m = catalog.menu(CRUST)
    i = p.parse("some chips", Context(view="menu", menu=m))
    assert i.name == "menu_category" and i.slots["category"] == "Chips" and i.slots["ask"]


def test_same_category_stays_on_open_menu(catalog):
    m = catalog.menu(PLUR)
    assert p.parse("get me a coffee", Context(view="menu", menu=m, place_tags=["coffee"])).name == "read_menu"
    assert p.parse("find another coffee shop", Context(view="menu", menu=m, place_tags=["coffee"])).name == "find"


@pytest.mark.parametrize("text", ["check out", "that's all", "place the order", "I'm done"])
def test_checkout(text):
    assert p.parse(text, Context(view="cart", cart_names=["Cafe Latte"])).name == "checkout"


def test_checkout_stage_is_strict():
    ctx = Context(view="checkout", checkout_stage="choose")
    assert p.parse("confirm", ctx).name == "approve"
    assert p.parse("don't place it", ctx).name != "approve"
    assert p.parse("use face id", ctx).slots["method"] == "face"
    assert p.parse("send me a code", ctx).slots["method"] == "otp"
    assert p.parse("never mind", ctx).name == "cancel_checkout"


@pytest.mark.parametrize("spoken,code", [("four seven one nine two oh", "471920"), ("471 920", "471920"),
                                          ("double five three one eight two", "553182")])
def test_otp_digits(spoken, code):
    assert spoken_digits(spoken) == code
    i = p.parse(spoken, Context(view="checkout", checkout_stage="otp"))
    assert i.name == "otp_code" and i.slots["code"] == code


def test_order_follow_ups():
    ctx = Context(view="order", has_order=True)
    assert p.parse("where's my order", ctx).name == "status"
    assert p.parse("I'll be there in 15 minutes", ctx).slots == {"minutes": 15}
    assert p.parse("cancel my order", ctx).name == "cancel_order"
    assert p.parse("take me there", ctx).name == "drive"


def test_navigate_saved_place():
    ctx = Context(saved_places={"home": "home", "work": "work", "office": "work"})
    assert p.parse("take me to the office", ctx).slots["place_id"] == "work"


@pytest.mark.parametrize("heard,woke,rest", [
    ("Hey Nova, find me a coffee.", True, "find me a coffee"), ("hey nova", True, ""),
    ("Hey Noah find gas", True, "find gas"), ("I told nova about it", False, ""),
])
def test_wake_phrase(heard, woke, rest):
    assert split_wake(heard, "hey nova", ["hey noah", "nova"]) == (woke, rest)
