"""UI actions shared by every front-end (Qt bridge, WebSocket server). No GUI toolkit imports here.

A front-end calls ``start(name, arg, arg2)``; the action runs the same Nova coroutines the voice path uses.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..domain.errors import DriveOSError
from ..domain.models import GeoPoint

log = logging.getLogger("driveos.ui")


class UiActions:
    def __init__(self, locations: list[dict] | None = None):
        self.nova = None
        self.locations = locations or []
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._done)
        return t

    def _done(self, t: asyncio.Task) -> None:
        self._tasks.discard(t)
        exc = t.exception() if not t.cancelled() else None
        if isinstance(exc, DriveOSError):
            log.warning("UI action refused: %s (%s)", exc, exc.code)    # an expected outcome, not a crash
        elif exc is not None:
            log.error("UI action failed: %r", exc, exc_info=exc)

    def say(self, text: str) -> asyncio.Task | None:
        if self.nova and text.strip():
            return self.spawn(self.nova.handle_text(text.strip()))
        return None

    def start(self, name: str, arg: Any = None, arg2: Any = None) -> asyncio.Task | None:
        """Run the named action. Returns the spawned task for async actions, None for sync ones.
        Raises KeyError for an unknown action name."""
        n = self.nova
        if n is None:
            return None
        sh = n.shop
        actions = {
            "tab": lambda: n.show_tab(str(arg)),
            "discover": lambda: n.discover(str(arg or sh.category or "food"), None),
            "openPlace": lambda: n.open_place(place_id=str(arg)),
            "menuCategory": lambda: n.select_menu_category(str(arg)),
            "openItem": lambda: n.open_item(str(arg), speak=False),
            "quickAdd": lambda: n.add_item(str(arg), 1, speak=False),
            "itemQty": lambda: n.set_item_qty(int(arg)),
            "itemOption": lambda: n.set_item_option({str(arg): [str(arg2)]}),
            "addItem": lambda: n.add_item(sh.item.id, sh.item_qty, sh.item_sel, speak=False) if sh.item else None,
            "closeItem": n.back,
            "lineQty": lambda: n.set_line_qty(str(arg), int(arg2)),
            "viewCart": lambda: n.view_cart(speak=False),
            "clearCart": lambda: n.clear_cart(speak=False),
            "back": n.back,
            "checkout": lambda: n.checkout(source="screen"),
            "auth": lambda: n.choose_auth(str(arg)),
            "otpKey": lambda: n.otp_key(str(arg)),
            "faceReport": lambda: n.face_report(bool(arg)),
            "cancelCheckout": lambda: n.cancel_checkout(quiet=True),
            "cancelOrder": n.cancel_order,
            "orderStatus": n.order_status,
            "late": lambda: n.update_eta(int(arg or 10)),
            "imHere": n.order_arrived,
            "driveToPlace": lambda: n.drive_to(n._place_for(place_id=str(arg)), start=True)
            if n._place_for(place_id=str(arg)) else None,
            "driveOrder": lambda: n.drive_to(sh.order_place, start=True) if sh.order_place else None,
            "driveMenu": lambda: n.drive_to(sh.place, start=False) if sh.place else None,
            "startNav": lambda: n.start_nav(speak=True),
            "stopNav": n.stop_nav,
            "clearNav": n.clear_nav,
            "trip": lambda: self._trip(str(arg)),
            "setLocation": lambda: self._set_location(int(arg)),
            "vehicleMode": lambda: n.set_vehicle_mode(str(arg)),
            "endDrive": self._end,
            "repeat": lambda: n.say(n.last_spoken) if n.last_spoken else None,
        }
        fn = actions.get(name)
        if fn is None:
            raise KeyError(name)
        res = fn()
        if asyncio.iscoroutine(res) or isinstance(res, asyncio.Future):
            return self.spawn(res)
        return None

    async def _trip(self, merchant_id: str) -> None:
        n = self.nova
        try:
            place = n.registry.get("local").place(merchant_id)
        except Exception:
            return
        await n.drive_to(place, start=False)

    async def _set_location(self, idx: int) -> None:
        loc = self.locations[idx]
        await self.nova.set_location(GeoPoint(lat=loc["lat"], lon=loc["lon"]), loc["label"])
        self.nova.notify(f"Location: {loc['label'].split(',')[0]}")

    async def _end(self) -> None:
        n = self.nova
        await n.cancel_checkout(quiet=True)
        if n.nav.mode in ("navigating", "overview"):
            hold = getattr(n.vehicle, "hold", None)
            if hold:
                hold()
            n.vehicle.set_gear("P")
        n.clear_nav()
        n.shop.view = "idle" if not (n.shop.tx and not n.shop.tx.terminal) else "order"
        n.tab = "home"
        n.voice.text, n.voice.heard = "", ""
        n.publish()
        n.notify("Drive ended")
