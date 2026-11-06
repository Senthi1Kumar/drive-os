"""``driveos demo`` — a hands-off, real-time walkthrough of the whole flow.

A scripted driver talks to Nova exactly as a person would (the words go through the same parser, so
what you see is the product, not a mock-up):

    "Hey Nova, find me a coffee"  → nearby options on screen
    "the first one"               → the menu
    "add a …"                     → cart
    "check out" / "confirm"       → authorisation (voice for small amounts, Face ID above the cap)
    "yes" (take me there)         → the car drives the route in real time; arrival is detected,
                                     the order is handed over and completes
    "I need gas" … "take me home" → a second errand, then home

With ``--gps`` the walkthrough starts wherever your GPS (or IP location) says you are; demo venues
are generated around you, so it works anywhere. ``--loop`` repeats it for a showroom screen.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .domain.money import Money

if TYPE_CHECKING:
    from .agent.nova import Nova
    from .container import Services

log = logging.getLogger("driveos.demo")
VOICE_CAP = 2500


class Demo:
    def __init__(self, services: Services, *, pace: float = 1.0, echo: Callable[[str], None] | None = None):
        self.s = services
        self.n: Nova = services.nova
        self.pace = max(0.05, pace)          # <1 is faster (tests), >1 slower (presentations)
        self.echo = echo or (lambda line: None)
        self._seen = 0

    # ------------------------------------------------------------------ helpers
    async def pause(self, seconds: float) -> None:
        await asyncio.sleep(seconds * self.pace)

    async def until(self, cond: Callable[[], bool], timeout: float = 30.0, what: str = "") -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            await asyncio.sleep(0.1)
        log.warning("demo: timed out waiting for %s", what or "condition")
        return False

    def _flush(self) -> None:
        for m in self.n.transcripts[self._seen:]:
            self.echo(f"{'Driver' if m.role == 'you' else 'Nova  '}: {m.text}")
        self._seen = len(self.n.transcripts)

    async def quiet(self) -> None:
        """Wait for Nova to finish talking (and the turn to finish)."""
        await asyncio.sleep(0.15)
        await self.until(lambda: not self.n._lock.locked() and self.n._pending_speech == 0
                         and self.n.voice.mode != "speaking", 60, "Nova to finish")
        self._flush()

    async def driver(self, text: str, *, wake: bool = False) -> None:
        """The driver speaks: the voice float shows the words arriving, then Nova handles them."""
        n = self.n
        if wake:
            await n.wake("")
            n.voice.heard = "Hey Nova"
            n.publish()
            await self.pause(0.7)
        n.set_voice("listening")
        words = text.split()
        for i in range(1, len(words) + 1):
            n.voice.heard = " ".join(words[:i])
            n.publish()
            await asyncio.sleep(0.16 * self.pace)
        await self.pause(0.35)
        await n.handle_text(text)
        await self.quiet()

    # ------------------------------------------------------------------ scenes
    def _pick_item(self) -> str | None:
        """Something reasonably priced with no required choices, so the scene doesn't stall."""
        menu = self.n.shop.menu
        if not menu:
            return None
        items = [i for i in menu.items if i.available]
        first = menu.categories[0] if menu.categories else None
        signature = [i for i in items if i.category == first] or items
        simple = [i for i in signature if not any(g.min_select and not any(o.default for o in g.options)
                                                  for g in i.modifier_groups)]
        pool = simple or signature
        order = {i.id: k for k, i in enumerate(items)}
        pool.sort(key=lambda i: (abs(i.base_price.amount - 650), order.get(i.id, 0)))
        return pool[0].name if pool else None

    async def approve(self) -> None:
        n = self.n
        await self.driver("check out")
        co = n.shop.checkout
        if co is None:
            return
        total: Money = co.req.quote.total
        await self.pause(1.2)
        if total.amount <= VOICE_CAP and "voice" in (co.allowed or []):
            await self.driver("confirm")
        else:
            await self.driver("use face id")
        await self.until(lambda: n.shop.view == "order" and n.shop.tx is not None, 20, "order placed")
        self._flush()

    async def drive_and_pickup(self) -> None:
        n = self.n
        if n.shop.awaiting == "drive_offer":
            await self.driver("yes")
        elif n.shop.order_place is not None:
            await n.drive_to(n.shop.order_place, start=True)
        tx = n.shop.tx
        await self.until(lambda: n.nav.mode in ("arrived", "idle") or (tx and tx.tx_id in n.active
                                                                           and n.active[tx.tx_id]["arrived"]),
                         600, "arrival")
        await self.quiet()
        if tx is not None:
            await self.until(lambda: tx.terminal, 120, "pickup")
            self._flush()

    async def errand(self, ask: str, *, wake: bool = True) -> bool:
        n = self.n
        await self.driver(ask, wake=wake)
        if not n.shop.places:
            return False
        await self.pause(2.0)
        await self.driver("the first one")
        await self.until(lambda: n.shop.menu is not None, 15, "menu")
        await self.pause(1.6)
        item = self._pick_item()
        if not item:
            return False
        await self.driver(f"add {item}")
        await self.pause(1.2)
        await self.approve()
        await self.pause(1.5)
        await self.drive_and_pickup()
        return True

    async def run_once(self) -> None:
        n = self.n
        n.tab = "home"
        n.publish()
        await self.pause(2.5)
        await self.errand("find me a coffee")
        await self.pause(3.0)
        await self.errand("I need gas")
        await self.pause(2.5)
        if n.shop.tx and not n.shop.tx.terminal:
            await self.driver("where's my order", wake=True)
        await self.driver("take me home", wake=True)
        await self.until(lambda: n.nav.mode in ("arrived", "idle"), 600, "home")
        await self.quiet()

    async def run(self, *, loop: bool = False) -> None:
        start = self.n._pos()
        label = self.n.location_label
        while True:
            await self.run_once()
            if not loop:
                return
            await self.pause(6.0)
            await self.n.set_location(start, label)
            self.n.transcripts.clear()
            self._seen = 0


async def start_from_gps(services: Services, timeout: float = 25.0, echo: Callable[[str], None] = print) -> bool:
    """Take one GPS fix, then simulate the drive from there (so the demo can still drive itself)."""
    n = services.nova
    setter = getattr(n.vehicle, "set_mode", None)
    if setter is None:
        return False
    await n.set_vehicle_mode("gps")
    gps = n.vehicle.gps
    echo(f"Waiting for a GPS fix ({gps.spec})…")
    end = time.monotonic() + timeout
    while time.monotonic() < end and gps.status != "fix":  # noqa: ASYNC110 - polling a status field
        await asyncio.sleep(0.2)
    if gps.status != "fix":
        echo(f"No GPS fix ({gps.error or 'timed out'}) — starting from {n.location_label}.")
        await n.set_vehicle_mode("sim")
        return False
    pos = gps.state.position
    await n._relocate(pos)
    await n.set_vehicle_mode("sim")            # the simulator takes over from the real position
    echo(f"GPS fix: {pos.lat:.5f}, {pos.lon:.5f} ({gps.label}, {n.gps_info()['accuracy'] or 'accuracy n/a'}) "
         f"— {n.location_label}")
    return True


async def run_headless(services: Services, *, loop: bool, gps: bool, pace: float, echo=print) -> int:
    await services.start()
    try:
        if gps:
            await start_from_gps(services, echo=echo)
        echo(f"DriveOS demo — starting at {services.nova.location_label}")
        await Demo(services, pace=pace, echo=echo).run(loop=loop)
        done = list(services.orchestrator.transactions.values())
        echo(f"Done — {len(done)} order(s): " + ", ".join(f"{t.quote.merchant_name} {t.state}" for t in done))
        return 0
    finally:
        await services.aclose()


__all__ = ["Demo", "run_headless", "start_from_gps"]
