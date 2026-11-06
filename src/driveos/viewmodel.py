"""Nova's state -> one plain dict for QML (``app.state``). No logic the UI would need to recompute."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import fmt
from .core.ids import utcnow
from .domain.categories import CATEGORIES
from .domain.models import OrderStatus, PickupMode, Place
from .merchants.base import make_line

if TYPE_CHECKING:
    from .agent.nova import Nova

GLYPH = {"coffee": "coffee", "food": "utensils", "sandwich": "sandwich", "bakery": "croissant", "bar": "glass",
         "gas": "fuel", "ev": "bolt", "store": "bag", "pharmacy": "pill"}
KIND_GLYPH = {"fuel": "fuel", "ev": "bolt", "store": "bag", "pharmacy": "pill", "bar": "glass"}
STATUS_STEPS = {
    "restaurant": [("placed", "Order placed and paid"), ("accepted", "Accepted"), ("preparing", "Being made"),
                   ("ready", "Ready for pickup"), ("picked_up", "Picked up")],
    "store": [("placed", "Order placed and paid"), ("accepted", "Accepted"), ("preparing", "Being picked"),
              ("ready", "Ready at the counter"), ("picked_up", "Picked up")],
    "fuel": [("placed", "Prepaid"), ("accepted", "Pump reserved"), ("ready", "Pump unlocked"),
             ("picked_up", "Fueling complete")],
    "ev": [("placed", "Paid"), ("accepted", "Stall reserved"), ("ready", "Charger ready"),
           ("picked_up", "Session started")],
}
ORDER_RANK = {"placed": 0, "accepted": 1, "preparing": 2, "ready": 3, "picked_up": 4}


def _money(cents: int, cur: str = "USD") -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def _miles(m: float | None) -> str:
    if m is None:
        return ""
    mi = m / 1609.344
    return f"{mi:.1f} mi" if mi >= 0.1 else f"{max(50, round(m * 3.28084 / 50) * 50)} ft"


def _clock(dt: datetime | None) -> str:
    if not dt:
        return "—"
    d = dt.astimezone()
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def _place(p: Place, i: int, nova: Nova) -> dict[str, Any]:
    tags = p.extra.get("tags") or [p.category]
    kind = p.extra.get("kind", "restaurant")
    d = p.extra.get("distance_m")
    detour = p.detour_s if p.distance_along_route_m is not None else None
    return {
        "id": p.id, "letter": chr(65 + i), "name": p.name, "address": p.address,
        "miles": _miles(d), "hours": fmt_hours(p.extra.get("hours", "")),
        "cover": p.photo_url or "", "glyph": KIND_GLYPH.get(kind) or GLYPH.get(tags[0], "utensils"),
        "orderable": bool(p.extra.get("orderable", True)), "hasMenu": bool(p.extra.get("has_menu", True)),
        "approx": bool(p.extra.get("approx")), "tags": [CATEGORIES[t].label for t in tags if t in CATEGORIES][:2],
        "detour": f"+{fmt.minutes(detour)} min" if detour is not None else "",
        "pickup": _pickup_label(p), "matched": p.extra.get("matched_item", ""),
        "source": p.extra.get("source", p.provider),
    }


def _pickup_label(p: Place) -> str:
    if not p.pickup_modes:
        return ""
    return {PickupMode.CURBSIDE: "Curbside", PickupMode.DRIVE_THRU: "Drive-thru", PickupMode.WALK_IN: "Counter",
            PickupMode.WINDOW: "Window", PickupMode.PUMP: "Pump prepay", PickupMode.CHARGER: "Reserve stall",
            PickupMode.IN_STORE: "In-store pickup"}.get(p.pickup_modes[0], "")


def fmt_hours(raw: str) -> str:
    """'11:00 AM - 3:00 PM, 4:00 PM - 8:30 PM' -> '11a–3p · 4–8:30p' (as in the DriveOS web app)."""
    import re

    if not raw:
        return ""
    out = []
    for part in re.split(r"\s*,\s*", raw.strip()):
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$", part, re.I)
        if not m:
            out.append(part)
            continue

        def c(h, mi, mer):
            return f"{int(h)}{':' + mi if mi and mi != '00' else ''}{mer[0].lower()}"

        out.append(f"{c(m[1], m[2], m[3])}–{c(m[4], m[5], m[6])}")
    return " · ".join(out)


def _item_glyph(item, place: Place | None) -> str:
    g = (item.refs or {}).get("glyph")
    if g:
        return g
    if place:
        kind = place.extra.get("kind", "restaurant")
        tags = place.extra.get("tags") or [place.category]
        return KIND_GLYPH.get(kind) or GLYPH.get(tags[0], "utensils")
    return "utensils"


def build_view(nova: Nova) -> dict[str, Any]:
    now = datetime.now().astimezone()
    sh = nova.shop
    v: dict[str, Any] = {
        "tab": nova.tab,
        "clock": f"{now.hour % 12 or 12}:{now.minute:02d}",
        "clockFull": now.strftime("%I:%M %p").lstrip("0"),
        "ampm": "AM" if now.hour < 12 else "PM",
        "weekday": now.strftime("%a"), "date": now.strftime("%b %-d"), "tz": now.strftime("%Z"),
        "location": nova.location_label,
        "toast": {"msg": nova.toast, "seq": nova.toast_seq},
        "phone": nova.phone_note,
        "transcripts": [{"id": m.id, "role": m.role, "text": m.text} for m in nova.transcripts[-40:]],
        "doc": nova.doc,
        "weather": getattr(nova, "weather", None),
    }
    v["gps"] = nova.gps_info()
    v["voice"] = _voice(nova)
    v["nav"] = _nav(nova)
    v["shop"] = _shop(nova)
    cart = sh.cart
    count = cart.item_count if cart else 0
    v["top"] = {
        "cartCount": count,
        "route": (f"{v['nav']['eta']} · {v['nav']['dest']}" if nova.nav.mode == "navigating" else
                  f"{v['nav']['tripEta']} · route ready" if nova.nav.mode == "overview" else ""),
        "guiding": nova.nav.mode == "navigating",
        "speed": v["nav"]["speed"],
        "status": sh.status if sh.searching else "",
        "order": _order_chip(nova),
    }
    v["home"] = _home(nova, v)
    return v


def _voice(nova: Nova) -> dict[str, Any]:
    vo = nova.voice
    tone = {"listening": "listening", "processing": "thinking", "speaking": "speaking"}.get(vo.mode, "ready")
    if not vo.available:
        tone = "muted"
    phrase = vo.phrase.title() if vo.phrase else "Hey Nova"
    if not vo.available:
        prompt = "Voice is off — press / to type"
    elif vo.wake:
        prompt = f"Say “{phrase}”, or hold the voice button"
    else:
        prompt = "Hold the voice button (Space) and talk"
    return {"mode": vo.mode, "label": vo.label, "text": vo.text, "heard": vo.heard, "tone": tone,
            "prompt": prompt, "phrase": phrase, "available": vo.available, "wake": vo.wake}


def _nav(nova: Nova) -> dict[str, Any]:
    n = nova.nav
    pr = nova.progress
    vs = nova.vstate
    out: dict[str, Any] = {"mode": n.mode, "dest": n.dest_name, "estimated": n.estimated, "maneuver": None,
                           "arrive": "—", "eta": "—", "left": "—", "progress": 0.0, "tripEta": "—",
                           "tripMeta": "", "speed": fmt.speed(vs.speed_mps, nova.units) if vs else "—",
                           "parked": bool(vs and vs.parked), "destKind": n.dest_kind}
    if n.route:
        out["tripEta"] = fmt.duration(n.route.duration_s)
        arrive = utcnow() + timedelta(seconds=n.route.duration_s)
        out["tripMeta"] = (f"{fmt.trip_distance(n.route.distance_m, nova.units)} · arrive {_clock(arrive)}"
                           + (" · estimated" if n.estimated else ""))
    if pr and n.mode in ("navigating", "arrived"):
        out["arrive"] = _clock(pr.eta)
        out["eta"] = fmt.duration(pr.remaining_s)
        out["left"] = fmt.trip_distance(pr.remaining_m, nova.units)
        out["progress"] = round(pr.fraction, 4)
        step = pr.next_step
        if step and n.mode == "navigating":
            val, unit = fmt.distance(pr.distance_to_next_m, nova.units)
            mod = (step.maneuver.modifier or "").replace(" ", "-")
            icon = "arrive" if step.maneuver.type == "arrive" else (f"turn-{mod}" if mod else "straight")
            out["maneuver"] = {"icon": icon, "dist": f"{val} {unit}", "instr": step.banner or step.maneuver.instruction,
                               "street": step.name if step.maneuver.type != "arrive" else ""}
    if n.mode == "arrived":
        out["maneuver"] = {"icon": "arrive", "dist": "Arrived", "instr": n.dest_name, "street": ""}
    return out


def _order_chip(nova: Nova) -> str:
    tx = nova.shop.tx
    if not tx or tx.terminal:
        return ""
    st = tx.order_status.value if tx.order_status else "placed"
    words = {"placed": "Order placed", "accepted": "Accepted", "preparing": "Being made", "ready": "Ready"}
    return f"{words.get(st, 'Order')} · {tx.quote.merchant_name}"


def _home(nova: Nova, v: dict[str, Any]) -> dict[str, Any]:
    sh = nova.shop
    n = v["nav"]
    vo = v["voice"]
    sub = ("Listening" if vo["mode"] == "listening" else
           f"Say “{vo['phrase']}”" if vo["wake"] else "Voice button to talk" if vo["available"] else
           "Voice off")
    home: dict[str, Any] = {
        "title": "On the way." if nova.nav.mode == "navigating" else "Ready when you are.",
        "sub": f"{sub} · {nova.location_label}",
        "route": None, "cart": None, "order": None,
        "nearby": [_place(p, i, nova) for i, p in enumerate(nova.home_nearby[:4])],
        "trips": _trips(nova),
    }
    if nova.nav.mode in ("overview", "navigating", "arrived") and nova.nav.route:
        home["route"] = {"eta": n["eta"] if nova.nav.mode == "navigating" else n["tripEta"],
                         "meta": n["tripMeta"] if nova.nav.mode != "navigating" else
                         f"{n['left']} · arrive {n['arrive']} · {n['dest']}",
                         "progress": n["progress"], "guiding": nova.nav.mode == "navigating",
                         "overview": nova.nav.mode == "overview"}
    if sh.cart and sh.cart.lines:
        home["cart"] = {"total": _money(sh.cart.subtotal.amount), "meta":
                        f"{sh.cart.item_count} item{'s' if sh.cart.item_count != 1 else ''} from "
                        f"{sh.cart_place.name if sh.cart_place else ''}"}
    if sh.tx and not sh.tx.terminal:
        home["order"] = {"title": v["top"]["order"], "code": sh.tx.order.pickup_code if sh.tx.order else "",
                         "ready": _clock(sh.tx.ready_at)}
    return home


def _trips(nova: Nova) -> list[dict[str, Any]]:
    """Demo calendar (as in the DriveOS web app): upcoming stops you can drive to with one tap."""
    now = datetime.now().astimezone()
    out = []
    for title, mid, h, m in ([("Coffee pickup", "r3-plur-coffee", 9, 30),
                                            ("Client lunch", "r7-crust-sourdough-deli-los-gatos", 12, 15),
                                            ("Dinner", "r11-orange-bowl", 17, 0)]):
        when = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        mins = int((when - now).total_seconds() // 60)
        primary = f"{max(1, mins)}m" if mins < 60 else f"{when.hour % 12 or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
        secondary = "away" if mins < 60 else ("today" if when.date() == now.date() else when.strftime("%a"))
        try:
            name = nova.registry.get("local").catalog.merchant(mid)["name"]  # type: ignore[attr-defined]
        except Exception:
            name = mid
        out.append({"id": mid, "title": title, "place": name, "primary": primary, "secondary": secondary,
                    "sort": mins})
    out.sort(key=lambda t: t["sort"])
    return out


def _shop(nova: Nova) -> dict[str, Any]:
    sh = nova.shop
    cat = CATEGORIES.get(sh.category)
    out: dict[str, Any] = {
        "view": sh.view, "status": sh.status, "searching": sh.searching,
        "title": cat.label if cat else "Nearby", "category": sh.category,
        "places": [_place(p, i, nova) for i, p in enumerate(sh.places)],
        "awaiting": sh.awaiting,
    }
    cart = sh.cart
    out["cart"] = {
        "count": cart.item_count if cart else 0,
        "subtotal": _money(cart.subtotal.amount) if cart and cart.lines else "$0.00",
        "place": sh.cart_place.name if sh.cart_place else "",
        "lines": [{"id": ln.line_id, "name": ln.name, "qty": ln.quantity, "price": _money(ln.total.amount),
                   "option": ", ".join(o for o in _sel_names(nova, ln) if o and o.lower() != "regular"),
                   "image": _line_image(nova, ln), "glyph": "utensils"} for ln in (cart.lines if cart else [])],
    }
    if sh.menu and sh.place:
        items = [it for it in sh.menu.items if not sh.menu_category or it.category == sh.menu_category]
        out["menu"] = {
            "id": sh.place.id, "name": sh.place.name, "address": sh.place.address,
            "hours": fmt_hours(sh.place.extra.get("hours", "")), "pickup": _pickup_label(sh.place),
            "miles": _miles(sh.place.extra.get("distance_m")), "categories": sh.menu.categories,
            "active": sh.menu_category or "",
            "items": [{"id": it.id, "name": it.name, "desc": it.description, "price": _money(it.base_price.amount),
                       "image": it.image_url or "", "addable": it.available, "glyph": _item_glyph(it, sh.place),
                       "options": len(it.modifier_groups[0].options) if it.modifier_groups else 0,
                       "inCart": sum(ln.quantity for ln in (cart.lines if cart else []) if ln.item_id == it.id)}
                      for it in items[:60]],
            "cover": sh.place.photo_url or "", "glyph": _item_glyph(type("X", (), {"refs": {}})(), sh.place),
        }
    else:
        out["menu"] = None
    if sh.item and sh.menu:
        it = sh.item
        line = make_line("x", it, sh.item_sel, sh.item_qty)
        out["item"] = {
            "id": it.id, "name": it.name, "desc": it.description or "No description on the menu.",
            "category": it.category, "place": sh.place.name if sh.place else "",
            "price": _money(line.unit_price.amount), "total": _money(line.total.amount), "qty": sh.item_qty,
            "image": it.image_url or "", "glyph": _item_glyph(it, sh.place), "addable": it.available,
            "groups": [{"id": g.id, "name": g.name, "options": [
                {"id": o.id, "name": o.name, "delta": (f"+{_money(o.price_delta.amount)}" if o.price_delta.amount
                                                       else ""), "on": o.id in sh.item_sel.get(g.id, [])}
                for o in g.options]} for g in it.modifier_groups],
        }
    else:
        out["item"] = None
    out["checkout"] = _checkout(nova)
    out["order"] = _order(nova)
    return out


def _sel_names(nova: Nova, ln) -> list[str]:
    menu = nova.shop.menu
    if not menu or nova.shop.cart_place is None or menu.merchant_id != nova.shop.cart_place.merchant_id:
        return []
    item = menu.item(ln.item_id)
    if not item:
        return []
    names = []
    for g in item.modifier_groups:
        for oid in ln.selections.get(g.id, []):
            o = g.option(oid)
            if o:
                names.append(o.name)
    return names


def _line_image(nova: Nova, ln) -> str:
    menu = nova.shop.menu
    if menu and nova.shop.cart_place and menu.merchant_id == nova.shop.cart_place.merchant_id:
        it = menu.item(ln.item_id)
        return (it.image_url or "") if it else ""
    try:
        m = nova.registry.get("local").catalog.menu(nova.shop.cart_place.merchant_id)  # type: ignore[attr-defined]
        it = m.item(ln.item_id)
        return (it.image_url or "") if it else ""
    except Exception:
        return ""


def _checkout(nova: Nova) -> dict[str, Any] | None:
    co = nova.shop.checkout
    if not co:
        return None
    q = co.req.quote
    payer = nova._payer()
    lines = [{"name": ln.name, "qty": ln.quantity, "desc": ln.description if ln.description.lower() != "regular" else "",
              "total": _money(ln.total.amount)} for ln in q.lines]
    return {
        "stage": co.stage, "method": co.method, "allowed": co.allowed, "error": co.error,
        "merchant": q.merchant_name, "lines": lines, "subtotal": _money(q.subtotal.amount),
        "tax": _money(q.tax.amount), "fees": _money(q.fees.amount) if q.fees.amount else "",
        "total": _money(q.total.amount), "payer": payer.display, "payerLabel": payer.label,
        "brand": payer.brand, "tier": co.req.tier.value,
        "voiceOk": "voice" in co.allowed,
        "reason": ("Amounts over " + _money(nova.s.policy.voice_only_max_minor) + " need Face ID or a code"
                   if "voice" not in co.allowed else ""),
        "otp": ({"dest": co.otp.destination, "length": co.otp.length, "entry": co.otp_entry,
                 "attempts": co.otp.attempts_left} if co.otp else None),
    }


def _order(nova: Nova) -> dict[str, Any] | None:
    sh = nova.shop
    tx = sh.tx
    if not tx:
        return None
    place = sh.order_place
    kind = place.extra.get("kind", "restaurant") if place else "restaurant"
    steps_def = STATUS_STEPS.get("store" if kind == "pharmacy" else kind if kind in STATUS_STEPS else "restaurant")
    st = tx.order_status.value if tx.order_status else "placed"
    rank = ORDER_RANK.get(st, 0)
    if tx.state == "completed":
        rank = 99
    steps = []
    for key, label in steps_def:
        r = ORDER_RANK[key]
        state = "done" if r < rank or (r == rank and key in ("placed",)) or rank == 99 else (
            "now" if r == rank else "next")
        if key == "picked_up" and rank == 99:
            state = "done"
        when = ""
        if key == "placed" and tx.placed_at:
            when = _clock(tx.placed_at)
        elif key == "ready" and tx.ready_at and r > rank:
            when = f"~{_clock(tx.ready_at)}"
        steps.append({"key": key, "label": label, "state": state, "when": when})
    remaining = max(0, int(((tx.ready_at or utcnow()) - utcnow()).total_seconds())) if tx.ready_at else 0
    terminal_msg = {"completed": "Done — enjoy!", "voided": "Canceled · hold released",
                    "refunded": "Canceled · refunded", "declined": "Card declined", "failed": "Order failed",
                    "abandoned": "Not placed"}.get(tx.state, "")
    driving_there = nova.nav.mode in ("navigating", "arrived") and nova.nav.dest_kind == "order"
    pr = nova.progress
    return {
        "merchant": tx.quote.merchant_name, "code": tx.order.pickup_code if tx.order else "",
        "spot": tx.order.pickup_spot if tx.order else "", "instructions": tx.order.pickup_instructions if tx.order else "",
        "status": st, "statusLabel": next((lbl for k, lbl in steps_def if k == st), "Placed"),
        "steps": steps, "readyIn": f"{fmt.minutes(remaining)} min" if remaining > 30 and st != "ready" else
        ("Ready" if st == "ready" else "Soon"),
        "readyAt": _clock(tx.ready_at),
        "total": _money(tx.quote.total.amount), "payer": nova._payer().display,
        "lines": [{"name": ln.name, "qty": ln.quantity} for ln in tx.quote.lines],
        "terminal": tx.terminal, "terminalMsg": terminal_msg, "canCancel": nova.orch.can_cancel(tx),
        "arrived": sh.arrived, "drivingThere": driving_there,
        "driveEta": fmt.duration(pr.remaining_s) if (driving_there and pr) else "",
        "kind": kind, "glyph": KIND_GLYPH.get(kind, "utensils"),
        "late": tx.ready_at and tx.order_status != OrderStatus.READY,
    }
