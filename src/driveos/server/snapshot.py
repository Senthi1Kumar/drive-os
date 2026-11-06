"""Nova's session state -> the ``/v1/ui`` state snapshot (WS contract v1). Pure functions, no I/O."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..core.ids import utcnow
from ..images.view import cover_picks, item_image, menu_picks, name_image, option_image, place_image
from ..nav.guidance import lanes_at, route_congestion, route_polyline6

if TYPE_CHECKING:
    from ..agent.nova import Nova

VOICE_STATES = {"idle": "idle", "listening": "listening", "processing": "thinking", "speaking": "speaking"}
AUTH_METHODS = {"voice": "voice", "face": "face", "otp": "code"}
AUTH_METHOD_IN = {"voice": "voice", "face": "face", "code": "otp", "otp": "otp"}
CRUISE_MPS = 11.2
REROUTE_FLAG_S = 8.0
MENU_ITEM_CAP = 80


def _dollars(minor: int | None) -> float:
    return round((minor or 0) / 100, 2)


def _text(minor: int | None) -> str:
    return f"${_dollars(minor):,.2f}"


def _place(p, i: int, nova: Nova, view_place: dict[str, Any], picks: dict[str, str] | None = None) -> dict[str, Any]:
    d = p.extra.get("distance_m")
    if p.distance_along_route_m is not None and p.detour_s is not None:
        eta = max(1, round(p.detour_s / 60))
    elif d is not None:
        eta = max(1, round(d / CRUISE_MPS / 60))
    else:
        eta = None
    return {"id": p.id, "name": p.name, "category": p.category,
            "distance_m": round(d) if d is not None else None, "eta_min": eta, "rating": p.rating,
            "address": p.address, "distance_text": view_place.get("miles", ""),
            "orderable": view_place.get("orderable", True), "sample": bool(p.extra.get("sample_menu")),
            **place_image(getattr(nova, "images", None), p, picks)}


def _menu(nova: Nova) -> list[dict[str, Any]]:
    sh = nova.shop
    if not (sh.menu and sh.place):
        return []
    sections: dict[str, list[dict[str, Any]]] = {}
    store, cat = getattr(nova, "images", None), sh.place.category
    picks = menu_picks(sh.menu.items, cat)
    for it in sh.menu.items[:MENU_ITEM_CAP]:
        sections.setdefault(it.category or "Menu", []).append(
            {"id": it.id, "name": it.name, "price": _dollars(it.base_price.amount),
             "price_text": _text(it.base_price.amount), "desc": it.description or "", "addable": it.available,
             **item_image(store, it, category=cat, key=picks.get(it.id, ""))})
    order = [c for c in sh.menu.categories if c in sections] + [c for c in sections if c not in sh.menu.categories]
    return [{"section": c, "items": sections[c]} for c in order]


def _menu_item(nova: Nova, item_id: str):
    sh = nova.shop
    return sh.menu.item(item_id) if sh.menu else None


def _item(nova: Nova, view_item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not view_item:
        return None
    mi = _menu_item(nova, view_item["id"])
    store = getattr(nova, "images", None)
    cat = nova.shop.place.category if nova.shop.place else ""
    pic = item_image(store, mi, category=cat, key=menu_picks(nova.shop.menu.items, cat).get(mi.id, ""),
                     selections=nova.shop.item_sel) if mi else {}
    opt_by_id = {(g.id, o.id): o for g in (mi.modifier_groups if mi else []) for o in g.options}
    return {"id": view_item["id"], "name": view_item["name"], "desc": view_item["desc"], "qty": view_item["qty"],
            **pic,
            "price_text": view_item["price"], "total_text": view_item["total"],
            "groups": [{"id": g["id"], "name": g["name"],
                        "options": [{"id": o["id"], "name": o["name"], "delta": o["delta"], "on": o["on"],
                                     **(option_image(store, opt_by_id[(g["id"], o["id"])])
                                        if (g["id"], o["id"]) in opt_by_id else {})}
                                    for o in g["options"]]} for g in view_item["groups"]]}


def _short_options(text: str) -> str:
    """"Large 20 oz · Iced · Oat milk" -> "Large · Iced · Oat" for a cart row."""
    parts = []
    for p in text.split(" · "):
        p = re.sub(r"\s+\d+\s*(oz|in|ml|l)\b", "", p, flags=re.I)
        parts.append(re.sub(r"\s+milk$", "", p, flags=re.I))
    return " · ".join(x for x in parts if x)


def _line_options(item, selections: dict[str, list[str]]) -> str:
    """The chosen options of a cart line as a short line; defaults are left out except the brand."""
    parts = []
    for g in item.modifier_groups:
        for oid in selections.get(g.id, []):
            o = g.option(oid)
            if o and (g.id == "brand" or not o.default):
                parts.append(o.name)
    return _short_options(" · ".join(parts))


def _cart(nova: Nova) -> dict[str, Any]:
    sh = nova.shop
    cart = sh.cart
    store = getattr(nova, "images", None)
    cat = sh.cart_place.category if sh.cart_place else ""
    lines = []
    picks = menu_picks(sh.menu.items, cat) if sh.menu and cart and sh.menu.merchant_id == cart.merchant_id else {}
    for ln in (cart.lines if cart else []):
        mi = _menu_item(nova, ln.item_id) if sh.menu and sh.menu.merchant_id == cart.merchant_id else None
        pic = item_image(store, mi, category=cat, key=picks.get(mi.id, ""), selections=ln.selections) \
            if mi else name_image(store, ln.name, cat)
        opts = _line_options(mi, ln.selections) if mi else ""
        lines.append({"id": ln.line_id, "name": ln.name, "qty": ln.quantity, "price": _dollars(ln.total.amount),
                      "price_text": _text(ln.total.amount), "opts": opts, **pic})
    sub = cart.subtotal.amount if cart and cart.lines else 0
    tax = 0
    if sh.checkout:
        q = sh.checkout.req.quote
        sub, tax = q.subtotal.amount, q.tax.amount
        total = q.total.amount
    else:
        total = sub
    return {"lines": lines, "subtotal": _dollars(sub), "tax": _dollars(tax), "total": _dollars(total),
            "total_text": _text(total), "place": sh.cart_place.name if sh.cart_place else ""}


def _location(nova: Nova) -> dict[str, Any]:
    """Where the position comes from: live GPS, or the simulated place (shown in the HMI)."""
    mgr = getattr(nova, "location", None)
    if mgr is not None:
        src = mgr.source()
        return {"source": "gps" if src.live else "sim", "source_text": src.label}
    live = getattr(nova, "vehicle_mode", "sim") == "gps"
    return {"source": "gps" if live else "sim",
            "source_text": "Live GPS" if live else f"Simulated · {(nova.location_label or '').split(',')[0]}"}


def _home(nova: Nova) -> dict[str, Any]:
    """Nearby real places for the Home strip. Their ids must resolve for ``open_place``, so they are added to
    Nova's lookup pool (``home_nearby``)."""
    feed = getattr(nova, "home_feed", None)
    places = list(feed.places) if feed is not None else []
    known = {p.id for p in nova.home_nearby}
    nova.home_nearby.extend(p for p in places if p.id not in known)
    store = getattr(nova, "images", None)
    picks = cover_picks(places)
    out = []
    for p in places:
        d = p.extra.get("distance_m")
        out.append({"id": p.id, "name": p.name, "category": p.category,
                    "distance_m": round(d) if d is not None else None,
                    "distance_text": _miles_text(d), "orderable": bool(p.extra.get("orderable", True)),
                    "sample": bool(p.extra.get("sample_menu")), **place_image(store, p, picks)})
    return {"places": out}


def _miles_text(m: float | None) -> str:
    if m is None:
        return ""
    mi = m / 1609.344
    return "right here" if mi < 0.1 else f"{mi:.1f} mi"


def _sim(nova: Nova) -> bool:
    return bool(getattr(nova.face, "simulated", False))


def _auth(nova: Nova) -> dict[str, Any]:
    sh = nova.shop
    co = sh.checkout
    if co is None:
        stage = "accepted" if sh.view == "order" and sh.tx is not None else "none"
        return {"stage": stage, "challenge": None, "detail": "", "sim": _sim(nova)}
    challenge = None
    detail = co.error or ""
    extra = {"sim": _sim(nova), **({"risk": dict(co.risk)} if co.risk else {})}
    if co.bio is not None and co.stage not in ("otp", "processing", "failed"):
        return {**co.bio, **extra}
    if co.stage == "choose":
        stage = "challenge"
        challenge = "Choose how to approve"
    elif co.stage == "voice":
        stage, detail = "verifying", "Starting Voice ID"
    elif co.stage == "face":
        stage = "face"
    elif co.stage == "otp":
        stage = "otp"
        challenge = f"Code sent to {co.otp.destination}" if co.otp else None
    elif co.stage == "processing":
        stage = "verifying"
    else:
        stage = "rejected"
    return {"stage": stage, "challenge": challenge, "detail": detail, **extra}


def _auth_full(nova: Nova) -> dict[str, Any]:
    """``_auth`` plus the Face ID camera fields and the code-entry hint (hmi_lvgl/CHECKOUT_CONTRACT.md)."""
    co = nova.shop.checkout
    face = {"preview": False, "face_state": "none", "face_rect": None, "face_name": None}
    if co is not None and co.method == "face":
        face = nova.preview.snapshot()
    return {**_auth(nova), **face, "voice_entry": nova.otp_pending()}


def _checkout(nova: Nova) -> dict[str, Any]:
    co = nova.shop.checkout
    if co is None:
        return {"pending": False, "amount": 0.0, "methods": [],
                "auth": _auth_full(nova)}
    q = co.req.quote
    return {"pending": True, "amount": _dollars(q.total.amount), "amount_text": _text(q.total.amount),
            "merchant": q.merchant_name, "methods": [AUTH_METHODS.get(m, m) for m in co.allowed],
            "method_info": nova.method_info(), "auth": _auth_full(nova)}


def _order(nova: Nova, view_order: dict[str, Any] | None) -> dict[str, Any] | None:
    tx = nova.shop.tx
    if not tx or not view_order:
        return None
    eta = None
    if tx.ready_at:
        eta = max(0.0, round((tx.ready_at - utcnow()).total_seconds() / 60, 1))
    return {"id": tx.order.order_id if tx.order else tx.tx_id, "status": view_order["status"],
            "status_text": view_order["statusLabel"], "pickup_code": view_order["code"], "eta_min": eta,
            "merchant": view_order["merchant"], "terminal": view_order["terminal"],
            "timeline": [{"state": s["key"], "at": s["when"], "label": s["label"], "phase": s["state"]}
                         for s in view_order["steps"]]}


def _nav(nova: Nova) -> dict[str, Any]:
    n, pr = nova.nav, nova.progress
    active = n.mode == "navigating"
    maneuver = None
    if n.mode in ("navigating", "arrived") and pr is not None and pr.next_step is not None and active:
        step = pr.next_step
        maneuver = step.banner or step.maneuver.instruction
    elif n.mode == "arrived":
        maneuver = f"Arrived at {n.dest_name}"
    eta = round(pr.remaining_s / 60, 1) if (pr is not None and n.mode in ("navigating", "arrived")) else None
    out: dict[str, Any] = {"active": active, "dest": n.dest_name or None, "maneuver": maneuver, "eta_min": eta,
                           "mode": n.mode}
    out.update(_nav_map(nova))
    return out


def _nav_map(nova: Nova) -> dict[str, Any]:
    """Additive map/guidance fields (WS v1, see hmi_lvgl/NAV_CONTRACT.md). Empty when there is no route."""
    n, pr = nova.nav, nova.progress
    if n.mode not in ("overview", "navigating", "arrived") or n.route is None or not n.route.geometry:
        return {}
    route = n.route
    out: dict[str, Any] = {"route": {"polyline6": route_polyline6(route), "id": n.route_id, "reason": n.route_reason}, "congestion": route_congestion(route)}
    vs = nova.vstate
    if vs is not None:
        out["puck"] = {"lat": vs.position.lat, "lon": vs.position.lon, "heading": round(vs.heading, 1)}
    if n.dest is not None:
        out["dest_point"] = {"lat": n.dest.lat, "lon": n.dest.lon, "name": n.dest_name}
    if n.via:
        out["stops"] = [{"lat": p.lat, "lon": p.lon, "name": nm}
                        for p, nm in zip(n.via, n.via_names, strict=False)]
    out["reroutes"] = n.reroutes
    out["reroute"] = bool(n.reroutes and time.monotonic() - n.rerouted_at < REROUTE_FLAG_S)
    if pr is not None and n.mode in ("navigating", "arrived"):
        out["eta_s"] = round(pr.remaining_s)
        out["remaining_m"] = round(pr.remaining_m)
        steps = route.steps
        up = pr.next_step
        if up is not None and n.mode == "navigating":
            cur = steps[pr.step_index] if pr.step_index < len(steps) else None
            out["step"] = {"icon": up.icon or "straight",
                           "text": (cur.banner if cur and cur.banner else up.maneuver.instruction) or "",
                           "dist_m": round(pr.distance_to_next_m)}
            lanes = lanes_at(cur, pr.distance_to_next_m)
            if lanes:
                out["step"]["lanes"] = lanes
            if pr.step_index + 2 < len(steps):
                nx = steps[pr.step_index + 2]
                out["next_step"] = {"icon": nx.icon or "straight", "text": nx.maneuver.instruction or nx.banner or ""}
    return out


PHONE_NOTE_TTL_S = 300.0


def _phone(nova: Nova) -> dict[str, Any] | None:
    note = nova.phone_note
    if not note or time.time() - float(note.get("at", 0.0)) > PHONE_NOTE_TTL_S:
        return None
    code = note.get("code")
    purpose = note.get("purpose", "")
    text = f"{code} is your DriveOS code to approve {purpose}." if code else str(purpose)
    return {"title": "Messages", "text": text, "code": code or None,
            "at": datetime.fromtimestamp(float(note["at"]), tz=UTC).isoformat()}


def build_state(nova: Nova, view: dict[str, Any], *, level: float, toast: dict[str, str] | None,
                update: dict[str, Any], enroll: dict[str, Any] | None = None,
                enroll_face: dict[str, Any] | None = None) -> dict[str, Any]:
    vo = nova.voice
    vs = nova.vstate
    vshop = view["shop"]
    sh = nova.shop
    picks = cover_picks(sh.places)
    places = [_place(p, i, nova, vshop["places"][i] if i < len(vshop["places"]) else {}, picks)
              for i, p in enumerate(sh.places)]
    return {
        "tab": nova.tab,
        "voice": {"state": VOICE_STATES.get(vo.mode, "idle"), "level": round(level, 2), "partial": "",
                  "heard": vo.heard, "reply": vo.text, "available": vo.available, "wake": vo.wake,
                  "phrase": vo.phrase,
                  "agent": {"route": vo.route, "step": vo.step, "offline": vo.offline}},
        "vehicle": {"speed_mps": round(vs.speed_mps, 2) if vs else 0.0, "parked": bool(vs.parked) if vs else True,
                    "lat": vs.position.lat if vs else nova.s.vehicle.start.lat,
                    "lon": vs.position.lon if vs else nova.s.vehicle.start.lon,
                    "label": nova.location_label, "heading": round(vs.heading, 1) if vs else 0.0,
                    **_location(nova), "t_ms": vs.t_ms if vs else 0.0},
        "shop": {"view": sh.view, "places": places,
                 "place": {"id": sh.place.id, "name": sh.place.name} if sh.place else None,
                 "sample": bool(sh.place and sh.place.extra.get("sample_menu")),
                 "menu": _menu(nova), "item": _item(nova, vshop.get("item")), "cart": _cart(nova),
                 "checkout": _checkout(nova), "order": _order(nova, vshop.get("order")),
                 "status": sh.status},
        "home": _home(nova),
        "nav": _nav(nova),
        "toast": toast,
        "update": update,
        "phone": _phone(nova),
        "enroll": enroll,
        "enroll_face": enroll_face,
    }
