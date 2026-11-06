"""End to end: the merchant MCP server over stdio, complete voice journeys, and a headless UI render."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from driveos.config import McpServerConfig
from driveos.domain.models import Cart, GeoPoint
from driveos.merchants.base import make_line
from driveos.merchants.mcp_provider import McpMerchantProvider

LOS_GATOS = GeoPoint(lat=37.2358, lon=-121.9746)


async def _wait(cond, timeout=20.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_mcp_stdio_merchant_server():
    mcp = McpMerchantProvider(McpServerConfig(name="t", command=sys.executable, args=["-m", "driveos.mcp_server"]),
                              extra_env={"DRIVEOS_TIME_SCALE": "200", "DRIVEOS_MCP_DEMO": "1"})
    try:
        places = await mcp.search(near=LOS_GATOS, route=None, category="coffee", query=None, limit=5)
        plur = next(p for p in places if p.name == "Plur Coffee")
        menu = await mcp.get_menu(plur.merchant_id)
        latte = next(i for i in menu.items if i.name == "Cafe Latte")
        assert latte.image_url and latte.image_url.startswith("https://")
        q = await mcp.quote(Cart(merchant_id=plur.merchant_id, lines=[make_line("l1", latte, {}, 2)]))
        assert q.total.amount == 1147 and q.provider == mcp.source
    finally:
        await mcp.aclose()


class _Sink:
    def __init__(self):
        self.last: dict = {}
        self.views: list[str] = []

    def ui(self, s):
        self.last = s
        v = s["shop"]["view"]
        if not self.views or self.views[-1] != v:
            self.views.append(v)

    def route(self, *a): ...
    def markers(self, *a): ...
    def vehicle(self, *a): ...
    def camera(self, *a): ...


@pytest.fixture
async def svc(settings):
    from driveos.container import build_services

    settings.vehicle.simulator_time_scale = 25
    sink = _Sink()
    s = build_services(settings, sink=sink)
    local = s.registry.get("local")
    local.time_scale, local.accept_delay_s = 120, 0.05
    s.face.scan_s = 0.05
    await s.start()
    s.sink = sink
    yield s
    await s.aclose()


async def test_voice_journey_sandwich_face_id_drive_and_pickup(svc):
    n, sink = svc.nova, svc.sink
    await n.wake("")
    assert n.voice.mode == "listening"
    await n.handle_text("Hey Nova, find me a sandwich place nearby")
    assert n.tab == "shop" and sink.last["shop"]["view"] == "places"
    assert sink.last["shop"]["places"][0]["name"].startswith("CRUST")
    await n.handle_text("the first one")
    assert n.shop.view == "menu"
    await n.handle_text("can I get two pastrami sandwiches and a cookie")
    assert n.shop.cart.item_count == 3
    await n.handle_text("check out")
    co = sink.last["shop"]["checkout"]
    assert co["total"] == "$26.19" and co["allowed"] == ["face", "otp"]    # above the $25 voice cap
    await n.handle_text("confirm")                                           # voice isn't enough here
    assert n.shop.view == "checkout"
    await n.handle_text("use face id")
    assert n.shop.view == "order" and n.shop.tx.state in ("placed", "accepted", "captured")
    await n.handle_text("yes")                                               # "want me to take you there?"
    assert n.nav.mode == "navigating" and n.tab == "nav"
    assert await _wait(lambda: n.shop.arrived, 60)
    assert await _wait(lambda: n.shop.tx.state == "completed", 30)
    rows = svc.ledger.recent_transactions(1)
    assert rows[0]["state"] == "completed" and rows[0]["total_minor"] == 2619
    assert svc.ledger.verify() > 0


async def test_voice_journey_fuel_with_one_time_code(svc):
    n, sink = svc.nova, svc.sink
    await n.handle_text("I need gas")
    assert sink.last["shop"]["places"][0]["name"] == "Summit Fuel & Wash"
    await n.handle_text("the first one")
    await n.handle_text("add premium 91")
    await n.handle_text("check out")
    assert sink.last["shop"]["checkout"]["total"] == "$40.00"
    await n.handle_text("send me a code")
    code = n.phone_note["code"]
    wrong = "000000" if code != "000000" else "111111"
    await n.handle_text(" ".join(wrong))
    assert n.shop.view == "checkout" and "didn't match" in sink.last["shop"]["checkout"]["error"]
    await n.handle_text(" ".join(code))
    assert n.shop.view == "order"
    assert await _wait(lambda: n.shop.tx.order_status is not None and n.shop.tx.order_status.value == "ready", 5)
    assert sink.last["shop"]["order"]["spot"] == "Pump 4"


async def test_small_order_is_approved_by_voice(svc):
    n = svc.nova
    await n.handle_text("find me a coffee")
    await n.open_place(place_id="local:r3-plur-coffee", speak=False)
    await n.handle_text("add a caramel latte")
    await n.handle_text("that's all")
    assert "voice" in n.shop.checkout.allowed
    await n.handle_text("confirm")
    assert n.shop.view == "order"
    await n.handle_text("I'll be there in 15 minutes")
    assert "fifteen minutes" in n.last_spoken
    await n.handle_text("cancel my order")
    assert await _wait(lambda: n.shop.tx.state in ("voided", "refunded"), 5)


async def test_cart_from_another_place_asks_first(svc):
    n = svc.nova
    await n.handle_text("find me a coffee")
    await n.open_place(place_id="local:r3-plur-coffee", speak=False)
    await n.handle_text("add a chai latte")
    await n.handle_text("find me a sandwich")
    await n.handle_text("the first one")
    await n.handle_text("add a turkey sandwich")
    assert n.shop.awaiting == "replace_cart" and n.shop.cart.merchant_id == "r3-plur-coffee"
    await n.handle_text("yes")
    assert n.shop.cart.merchant_id.startswith("r7-crust") and n.shop.cart.item_count == 1


@pytest.mark.skipif(os.environ.get("DRIVEOS_SKIP_UI") == "1", reason="UI disabled")
def test_ui_renders_headless(tmp_path):
    import subprocess

    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "DRIVEOS_DATA_DIR": str(tmp_path / "d"),
           "DRIVEOS_CONFIG": str(tmp_path / "none.toml"), "DRIVEOS_ENV_FILE": os.devnull}
    code = (
        "import asyncio,sys,pathlib\n"
        "from driveos.config import load_settings\n"
        "from driveos import app, screens\n"
        "async def short(engine, services, out):\n"
        "    await services.nova.handle_text('find me a coffee')\n"
        "    await asyncio.sleep(1.2)\n"
        "    w = screens._window(engine); w.grabWindow().save(str(out/'shop.png'))\n"
        "    return []\n"
        "screens.capture_flow = short\n"
        f"sys.exit(app.run(load_settings(), screenshot_dir=pathlib.Path({str(tmp_path)!r}), voice=False))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    from PySide6.QtGui import QImage

    img = QImage(str(tmp_path / "shop.png"))
    assert (img.width(), img.height()) == (1280, 800)
    qml_problems = [ln for ln in r.stderr.splitlines() if ".qml:" in ln and "Proxy" not in ln and "Network" not in ln]
    assert not qml_problems, qml_problems[:5]
