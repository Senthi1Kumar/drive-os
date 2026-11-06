#!/usr/bin/env python3
"""Write the shop/checkout/order/nav fixtures (contract v1 snapshots) into fixtures/."""
import copy
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fixtures"
base = json.load(open(OUT / "home.json"))
hello = base["hello"]

def make(tab="shop", **over):
    d = copy.deepcopy(base)
    d["state"]["tab"] = tab
    for k, v in over.items():
        d["state"]["shop" if k.startswith("shop_") else k] if False else None
    return d

def save(name, d):
    d["hello"] = hello
    json.dump(d, open(OUT / f"{name}.json", "w"), indent=1, ensure_ascii=False)

PLACES = [
    {"id": "local:r6-kenz-coffee-bar", "name": "Kenz Coffee Bar", "category": "coffee", "distance_m": 1311, "eta_min": 2, "rating": None,
     "address": "445 Hershner Dr, Los Gatos, CA", "distance_text": "0.8 mi", "orderable": True},
    {"id": "local:r3-plur-coffee", "name": "Plur Coffee", "category": "coffee", "distance_m": 3168, "eta_min": 5, "rating": None,
     "address": "2425 Samaritan Dr, Main Entrance, Los Gatos, CA", "distance_text": "2.0 mi", "orderable": True},
    {"id": "local:r2-purple-onion-cafe", "name": "Purple Onion Cafe", "category": "coffee", "distance_m": 1608, "eta_min": 2, "rating": None,
     "address": "26 E Main St, Los Gatos, CA", "distance_text": "1.0 mi", "orderable": False},
    {"id": "local:cielito", "name": "cielito health bar & café", "category": "coffee", "distance_m": 5305, "eta_min": 8, "rating": None,
     "address": "1979 Harris Ave Apt 1, Los Gatos, CA", "distance_text": "3.3 mi", "orderable": False},
]
def items(names):
    return [{"id": f"I{i}", "name": n, "price": p, "price_text": f"${p:,.2f}", "desc": dsc, "addable": True} for i, (n, p, dsc) in enumerate(names)]
MENU = [
    {"section": "Latte", "items": items([("Cafe Latte", 5.25, "Double espresso with steamed milk"), ("Caramel Latte", 6.25, "House caramel, whole milk"),
                                          ("Chai Latte", 5.99, ""), ("Matcha Latte", 5.99, "Ceremonial grade matcha"), ("Vanilla Cafe Latte", 6.25, ""),
                                          ("Tiramisu Latte", 6.25, "Mascarpone foam, cocoa")])},
    {"section": "Water", "items": items([("Saratoga® Natural Spring Water 12 Oz Glass Bottle", 3.0, ""), ("Sparkling Saratoga water 12oz", 3.0, "")])},
    {"section": "Bakery", "items": items([("Butter Croissant", 4.25, "Baked this morning")])},
]
LINES = [{"id": "L1", "name": "Cafe Latte", "qty": 2, "price": 10.5, "price_text": "$10.50"},
         {"id": "L2", "name": "Butter Croissant", "qty": 1, "price": 4.25, "price_text": "$4.25"}]
CART = {"lines": LINES, "subtotal": 14.75, "tax": 0.0, "total": 14.75, "total_text": "$14.75", "place": "Plur Coffee"}

def shop(d, **kw):
    d["state"]["shop"].update(kw)
    return d

d = make(); shop(d, view="places", status="4 coffee shops nearby", places=PLACES); save("shop_places", d)
d = make(); shop(d, view="places", status="", places=[]); save("shop_empty", d)
d = make(); shop(d, view="menu", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, menu=MENU); save("shop_menu", d)
d = make(); shop(d, view="menu", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, menu=MENU, cart=CART); save("shop_menu_cart", d)
item = {"id": "I0", "name": "Cafe Latte", "desc": "Double espresso with steamed milk.", "qty": 1, "price_text": "$5.25", "total_text": "$5.25",
        "groups": [{"id": "size", "name": "Size", "options": [{"id": "s", "name": "Small", "delta": "", "on": False}, {"id": "m", "name": "Medium", "delta": "+$0.50", "on": True}, {"id": "l", "name": "Large", "delta": "+$1.00", "on": False}]},
                   {"id": "milk", "name": "Milk", "options": [{"id": "w", "name": "Whole", "delta": "", "on": True}, {"id": "o", "name": "Oat", "delta": "+$0.75", "on": False}]}]}
d = make(); shop(d, view="item", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, menu=MENU, item=item); save("shop_item", d)
d = make(); shop(d, view="item", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, menu=MENU,
                 item={**item, "img_lg": "food/latte_320.jpg"}); save("shop_item_img", d)
d["hello"] = {**d["hello"], "image_root": "fixtures/assets"}     # latte_320.jpg lives there; run from hmi_lvgl/
json.dump(d, open(OUT / "shop_item_img.json", "w"), indent=1, ensure_ascii=False)
d = make(); shop(d, view="cart", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, menu=MENU, cart=CART); save("shop_cart", d)
d = make(); shop(d, view="cart", cart={"lines": [], "subtotal": 0, "tax": 0, "total": 0, "total_text": "$0.00", "place": ""}); save("shop_cart_empty", d)

def img_items(rows):
    return [{"id": f"I{i}", "name": n, "price": p, "price_text": f"${p:,.2f}", "desc": dsc, "addable": True,
             "img": f"food/{k}_160.jpg", "img_lg": f"food/{k}_320.jpg"} for i, (n, p, dsc, k) in enumerate(rows)]

def sample_place(pid, name, cat, dist, eta, addr):
    return {"id": pid, "name": name, "category": cat, "distance_m": dist, "eta_min": eta, "rating": None,
            "address": addr, "distance_text": f"{dist / 1609:.1f} mi", "orderable": True, "sample": True}

def save_with_images(name, d):
    save(name, d)
    d["hello"] = {**d["hello"], "image_root": "fixtures/assets"}     # food/*.jpg live there; run from hmi_lvgl/
    json.dump(d, open(OUT / f"{name}.json", "w"), indent=1, ensure_ascii=False)

PIZZA = sample_place("local:tonys-slice-house", "Tony's Slice House", "food", 1800, 4, "12 Main St, Los Gatos, CA")
PIZZA_MENU = [
    {"section": "Pizza", "items": img_items([("Margherita Pizza", 14.95, "Tomato, mozzarella, basil", "pizza_margherita"),
                                             ("Pepperoni Pizza", 16.95, "", "pizza_pepperoni"),
                                             ("Garden Veggie Pizza", 15.95, "", "pizza_veggie"),
                                             ("Pepperoni Slice", 4.50, "Single slice", "pizza_slice")])},
    {"section": "Sides", "items": img_items([("Chicken Wings", 11.95, "Six pieces", "chicken_wings"),
                                             ("House Salad", 7.95, "", "salad"), ("Garlic Bread", 5.95, "", "naan")])},
    {"section": "Drinks", "items": img_items([("Soda", 2.95, "", "soda"), ("Sparkling Water", 2.95, "", "water_bottle")])},
]
d = make(); shop(d, view="menu", status="", places=[PIZZA], place={"id": PIZZA["id"], "name": PIZZA["name"]},
                 menu=PIZZA_MENU, sample=True); save_with_images("shop_menu_pizza", d)

BURGER = sample_place("local:stack-burger-co", "Stack Burger Co", "food", 2400, 6, "88 University Ave, Los Gatos, CA")
BURGER_MENU = [
    {"section": "Burgers", "items": img_items([("Classic Cheeseburger", 11.95, "Cheddar, pickles, house sauce", "burger_classic"),
                                               ("Bacon Burger", 13.95, "", "burger_bacon"),
                                               ("Veggie Burger", 12.95, "", "burger_veggie")])},
    {"section": "Sides", "items": img_items([("French Fries", 4.95, "", "fries"), ("Onion Rings", 5.95, "", "onion_rings"),
                                             ("Chicken Wings", 11.95, "", "chicken_wings")])},
    {"section": "Drinks", "items": img_items([("Vanilla Milkshake", 6.95, "", "smoothie"), ("Soda", 2.95, "", "soda")])},
]
d = make(); shop(d, view="menu", status="", places=[BURGER], place={"id": BURGER["id"], "name": BURGER["name"]},
                 menu=BURGER_MENU, sample=True); save_with_images("shop_menu_burger", d)

def checkout(name, stage, challenge=None, detail="", **auth_extra):
    d = make()
    cart = copy.deepcopy(CART); cart["tax"] = 1.33; cart["total"] = 16.08; cart["total_text"] = "$16.08"
    shop(d, view="checkout", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, menu=MENU, cart=cart,
         checkout={"pending": True, "amount": 16.08, "amount_text": "$16.08", "merchant": "Plur Coffee", "methods": ["voice", "face", "code"],
                   "auth": {"stage": stage, "challenge": challenge, "detail": detail, **auth_extra}})
    if stage == "challenge": d["state"]["voice"].update(state="listening", level=0.6)
    save(name, d)
checkout("checkout_choose", "challenge", "Choose how to approve")
checkout("checkout_voice", "challenge", "Say confirm to pay")
checkout("checkout_digits", "challenge", "4 7 2 9")
checkout("checkout_verifying", "verifying")
checkout("checkout_face", "face")
checkout("checkout_otp", "otp", "Code sent to +1 ••• 4821", voice_entry=True)
checkout("checkout_face_preview", "face", preview=True, face_state="scanning", face_rect=None, face_name=None)
checkout("checkout_face_verified", "face", preview=False, face_state="verified", face_rect=None, face_name="alex")
checkout("checkout_face_failed", "rejected", None, "Face didn’t match", preview=False, face_state="failed", face_rect=None, face_name=None)
checkout("checkout_accepted", "accepted")
checkout("checkout_rejected", "rejected", None, "Voice didn’t match")

d = make()
shop(d, view="order", places=PLACES, place={"id": PLACES[1]["id"], "name": "Plur Coffee"}, cart=CART,
     order={"id": "o-1", "status": "preparing", "status_text": "Preparing", "pickup_code": "A-482", "eta_min": 7.0, "merchant": "Plur Coffee", "terminal": False,
            "timeline": [{"state": "placed", "at": "10:41 AM", "label": "Order placed", "phase": "done"},
                         {"state": "accepted", "at": "10:41 AM", "label": "Accepted by Plur Coffee", "phase": "done"},
                         {"state": "preparing", "at": "now", "label": "Being prepared", "phase": "now"},
                         {"state": "ready", "at": "~10:49 AM", "label": "Ready for pickup", "phase": "next"},
                         {"state": "picked_up", "at": "", "label": "Picked up", "phase": "next"}]})
save("order_preparing", d)

d = make("nav"); d["state"]["nav"] = {"active": True, "dest": "Plur Coffee", "maneuver": "Turn right onto Hershner Dr", "eta_min": 5.0, "mode": "navigating"}
d["state"]["vehicle"].update(speed_mps=11.2, parked=False, heading=40.0); save("nav_navigating", d)
d = make("nav"); save("nav_idle", d)
d = make("nav"); d["state"]["nav"] = {"active": False, "dest": "Plur Coffee", "maneuver": "Arrived at Plur Coffee", "eta_min": 0.0, "mode": "arrived"}; save("nav_arrived", d)
