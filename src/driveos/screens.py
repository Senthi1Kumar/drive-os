"""Scripted walkthrough that renders every HMI screen to PNG (QA, design review, release notes).

    driveos screenshots --out ./shots        # works headless: QT_QPA_PLATFORM=offscreen
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger("driveos.screens")


def _window(engine):
    import shiboken6
    from PySide6.QtQuick import QQuickWindow

    obj = engine.rootObjects()[0]
    return shiboken6.wrapInstance(shiboken6.getCppPointer(obj)[0], QQuickWindow)


async def _wait(cond, timeout: float = 20.0) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.1)
    return False


async def capture_flow(engine, services, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    win = _window(engine)
    nova = services.nova
    shots: list[Path] = []

    async def grab(name: str, settle: float = 0.9) -> None:
        await asyncio.sleep(settle)
        img = win.grabWindow()
        p = out / f"{name}.png"
        img.save(str(p))
        shots.append(p)
        log.info("captured %s (tab=%s view=%s)", p.name, nova.tab, nova.shop.view)

    def bg(coro):
        return asyncio.ensure_future(coro)

    # make the kitchen and the car quick so the walkthrough doesn't take real minutes
    local = services.registry.get("local")
    local.time_scale = 40
    local.accept_delay_s = 0.3
    sim = getattr(services.vehicle, "sim", services.vehicle)
    if hasattr(sim, "scale"):
        sim.scale = 12
    nova.set_voice_available(True, wake=True, phrase="hey nova")

    await _wait(lambda: bool(nova.home_nearby), 10)
    await grab("01-home-ready", 2.0)

    await nova.wake("")
    await grab("02-wake-listening", 0.6)

    nova.voice.heard = "Find me a sandwich place nearby"
    t = bg(nova.handle_text("Find me a sandwich place nearby"))
    await t
    await grab("03-shop-options", 0.8)

    await nova.handle_text("the first one")
    await grab("04-menu", 1.6)

    first = next(it for it in nova.shop.menu.items if it.available)
    await nova.open_item(first.id, qty=2)
    await grab("05-item-sheet", 1.2)
    await nova.add_item(first.id, 2, speak=False)
    await nova.handle_text("add a chocolate chip cookie")
    await nova.view_cart(speak=False)
    await grab("06-cart", 1.0)

    t = bg(nova.handle_text("check out"))
    await _wait(lambda: nova.shop.view == "checkout")
    await t
    await grab("07-approve-choose", 0.8)

    face = services.face
    if hasattr(face, "scan_s"):
        face.scan_s = 3.0
    t = bg(nova.handle_text("use face id"))
    await _wait(lambda: nova.shop.checkout is not None and nova.shop.checkout.stage == "face")
    await grab("08-approve-faceid", 0.8)
    await t
    await _wait(lambda: nova.shop.view == "order")
    await _wait(lambda: nova.shop.tx is not None and nova.shop.tx.order_status is not None
                and nova.shop.tx.order_status.value in ("preparing", "accepted"), 10)
    await grab("09-order-status", 1.2)

    t = bg(nova.handle_text("yes, take me there"))
    await t
    await _wait(lambda: nova.progress is not None and nova.progress.fraction > 0.25, 30)
    await grab("10-navigating", 0.6)
    nova.show_tab("home")
    await grab("11-home-on-the-way", 0.8)

    await _wait(lambda: nova.nav.mode == "arrived", 60)
    await grab("12-arrived-pickup", 1.5)
    await _wait(lambda: nova.shop.tx is not None and nova.shop.tx.terminal, 30)

    # second errand: fuel prepay above the voice cap -> one-time code
    await nova.handle_text("I need gas")
    await grab("13-gas-options", 0.8)
    await nova.handle_text("the first one")
    await nova.handle_text("add regular 87")
    t = bg(nova.handle_text("check out"))
    await _wait(lambda: nova.shop.view == "checkout")
    await t
    await nova.handle_text("send me a code")
    await _wait(lambda: nova.phone_note is not None)
    code = nova.phone_note["code"]
    await nova.otp_key(code[0])
    await nova.otp_key(code[1])
    await nova.otp_key(code[2])
    await grab("14-approve-code", 0.8)
    for ch in code[3:]:
        await nova.otp_key(ch)
    await _wait(lambda: nova.shop.view == "order")
    await grab("15-fuel-reserved", 1.0)

    await nova.help()
    await grab("16-agent-help", 1.0)

    nova.doc = ""
    nova.show_tab("agent")
    nova.set_voice("listening")
    await grab("17-agent-listening", 0.8)
    nova.set_voice("idle")

    # live GPS: the recorded Los Gatos drive through the real NMEA pipeline
    gps = getattr(services.vehicle, "gps", None)
    if gps is not None:
        nova.clear_nav()
        nova.show_tab("nav")
        gps.spec = "replay@4"
        await nova.set_vehicle_mode("gps")
        await _wait(lambda: nova.vstate is not None and nova.vstate.source == "gps" and nova.vstate.speed_mps > 8, 20)
        await nova.handle_text("take me to work")
        await nova.start_nav()
        await grab("18-nav-live-gps", 2.0)
        await nova.set_vehicle_mode("sim")
    return shots
