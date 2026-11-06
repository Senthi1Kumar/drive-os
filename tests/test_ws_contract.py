"""WS contract v1 (/v1/ui): hello, snapshot on connect, commands and acks."""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from driveos.server import build_backend


@pytest.fixture
async def backend(settings):
    settings.vehicle.simulator_time_scale = 25
    b = build_backend(settings, voice=False, host="127.0.0.1", port=0)
    local = b.services.registry.get("local")
    local.time_scale, local.accept_delay_s = 120, 0.05
    b.services.face.scan_s = 0.05
    await b.start()
    yield b
    await b.stop()


class Client:
    def __init__(self, ws):
        self.ws = ws
        self.state: dict = {}
        self.events: list[dict] = []
        self.acks: dict[int, dict] = {}
        self.seqs: list[int] = []
        self._id = 0
        self._task = asyncio.ensure_future(self._pump())

    async def _pump(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if m["type"] == "state":
                self.state, _ = m["state"], self.seqs.append(m["seq"])
            elif m["type"] == "event":
                self.events.append(m)
            elif m["type"] == "ack":
                self.acks[m["id"]] = m

    async def cmd(self, name, **args):
        self._id += 1
        await self.ws.send(json.dumps({"v": 1, "type": "cmd", "id": self._id, "name": name, "args": args}))
        return await self.wait(lambda: self._id in self.acks, what=f"ack {name}") and self.acks[self._id]

    async def wait(self, cond, timeout=10.0, what="condition"):
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if cond():
                return True
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
async def client(backend):
    async with connect(f"ws://127.0.0.1:{backend.server.port}/v1/ui") as ws:
        first = json.loads(await ws.recv())
        c = Client(ws)
        c.hello = first
        await c.wait(lambda: c.state, what="first state")
        yield c
        c._task.cancel()


async def test_hello_first(client):
    h = client.hello
    assert h["v"] == 1 and h["type"] == "hello" and h["app"] == "DriveOS"
    assert h["version"] and h["build"]


async def test_snapshot_on_connect_has_contract_shape(client):
    s = client.state
    assert s["tab"] == "home"
    assert set(s["voice"]) >= {"state", "level", "partial", "heard", "reply"}
    assert s["voice"]["state"] in ("idle", "wake", "listening", "thinking", "speaking")
    assert set(s["vehicle"]) >= {"speed_mps", "parked", "lat", "lon", "label"}
    assert set(s["shop"]) >= {"view", "places", "place", "menu", "item", "cart", "checkout", "order"}
    assert set(s["shop"]["cart"]) >= {"lines", "subtotal", "tax", "total"}
    assert set(s["shop"]["checkout"]) >= {"pending", "amount", "methods", "auth"}
    assert s["shop"]["checkout"]["auth"]["stage"] == "none"
    assert set(s["nav"]) >= {"active", "dest", "maneuver", "eta_min"}
    assert s["toast"] is None
    assert s["update"] == {"available": False, "version": None, "highlights": []}


async def test_show_tab_changes_state(client):
    ack = await client.cmd("show_tab", tab="nav")
    assert ack == {"v": 1, "type": "ack", "id": 1, "ok": True, "error": None}
    await client.wait(lambda: client.state["tab"] == "nav", what="tab nav")


async def test_find_coffee_populates_places(client):
    assert (await client.cmd("find", category="coffee"))["ok"]
    await client.wait(lambda: client.state["shop"]["places"], what="places")
    s = client.state["shop"]
    assert s["view"] == "places" and client.state["tab"] == "shop"
    p = s["places"][0]
    assert set(p) >= {"id", "name", "category", "distance_m", "eta_min", "rating"}


async def test_order_flow_menu_cart_checkout(client):
    await client.cmd("find", category="coffee")
    await client.wait(lambda: client.state["shop"]["places"], what="places")
    pid = next(p["id"] for p in client.state["shop"]["places"] if p["name"] == "Plur Coffee")
    await client.cmd("open_place", id=pid)
    await client.wait(lambda: client.state["shop"]["menu"], what="menu")
    assert client.state["shop"]["place"]["id"] == pid
    item = client.state["shop"]["menu"][0]["items"][0]
    assert set(item) >= {"id", "name", "price", "desc"}
    assert (await client.cmd("add_item", id=item["id"], qty=2))["ok"]
    await client.wait(lambda: client.state["shop"]["cart"]["lines"], what="cart line")
    line = client.state["shop"]["cart"]["lines"][0]
    assert line["qty"] == 2 and client.state["shop"]["cart"]["subtotal"] > 0
    assert (await client.cmd("checkout"))["ok"]
    await client.wait(lambda: client.state["shop"]["checkout"]["pending"], what="checkout")
    co = client.state["shop"]["checkout"]
    assert co["amount"] > 0 and "voice" in co["methods"]
    assert (await client.cmd("cancel_checkout"))["ok"]
    await client.wait(lambda: not client.state["shop"]["checkout"]["pending"], what="checkout cancelled")


async def test_say_text_goes_through_nova_and_emits_say(client):
    assert (await client.cmd("say_text", text="find me a coffee"))["ok"]
    await client.wait(lambda: client.state["shop"]["places"], what="places")
    await client.wait(lambda: any(e["name"] == "say" for e in client.events), what="say event")
    assert client.state["voice"]["heard"] == "find me a coffee"


async def test_unknown_and_bad_commands_ack_false(client):
    a = await client.cmd("no_such_cmd")
    assert a["ok"] is False and a["error"]
    b = await client.cmd("show_tab", tab="garage")
    assert b["ok"] is False
    c = await client.cmd("open_item")
    assert c["ok"] is False and "id" in c["error"]


async def test_state_seq_increases_and_rate_is_bounded(client):
    n0 = len(client.seqs)
    for t in ("nav", "home", "nav", "home", "agent", "shop"):
        await client.cmd("show_tab", tab=t)
    await client.wait(lambda: client.state["tab"] == "shop", what="last tab")
    assert client.seqs == sorted(client.seqs) and len(set(client.seqs)) == len(client.seqs)
    assert len(client.seqs) > n0


def test_fixture_matches_contract_keys():
    from pathlib import Path

    msg = json.loads((Path(__file__).parent / "fixtures" / "ui_state_shop_menu.json").read_text())
    assert msg["type"] == "state" and msg["v"] == 1
    assert set(msg["state"]) == {"tab", "voice", "vehicle", "shop", "nav", "toast", "update"}
    assert msg["state"]["shop"]["view"] == "menu" and msg["state"]["shop"]["menu"]


async def test_phone_note_in_state_and_event(client, backend):
    assert client.state["phone"] is None
    backend.services.nova.show_otp_on_phone("481516", "$4.50 at Plur")
    await client.wait(lambda: client.state["phone"], what="phone state")
    p = client.state["phone"]
    assert set(p) == {"title", "text", "code", "at"} and p["code"] == "481516" and "481516" in p["text"]
    await client.wait(lambda: any(e["name"] == "phone_note" for e in client.events), what="phone_note event")
    ev = next(e for e in client.events if e["name"] == "phone_note")
    assert ev["data"]["code"] == "481516"
    backend.services.nova.phone_note = None
    backend.services.nova.publish()
    await client.wait(lambda: client.state["phone"] is None, what="phone cleared")


class _FakePipe:
    available = True

    def __init__(self):
        self.ended = 0
        self.muted: list[bool] = []

    def end(self):
        self.ended += 1

    def set_muted(self, m):
        self.muted.append(m)


async def test_end_conversation_and_mute_commands(client, backend):
    pipe = _FakePipe()
    backend.server.commands.pipeline = pipe
    assert (await client.cmd("end_conversation"))["ok"]
    assert pipe.ended == 1
    assert (await client.cmd("set_voice_muted", muted=True))["ok"]
    assert (await client.cmd("set_voice_muted", muted=False))["ok"]
    assert pipe.muted == [True, False]
    bad = await client.cmd("set_voice_muted")
    assert bad["ok"] is False and "muted" in bad["error"]


async def test_end_conversation_without_voice_is_still_acked(client):
    assert (await client.cmd("end_conversation"))["ok"]
    assert (await client.cmd("set_voice_muted", muted=True))["ok"] is False


async def test_vehicle_fixes_stream_at_sim_rate_while_driving(backend, client):
    from driveos.domain.models import GeoPoint
    n = backend.services.nova
    base = n._pos()
    await n.navigate_to_point(GeoPoint(lat=base.lat + 0.02, lon=base.lon + 0.015), "Far Cafe")
    await asyncio.sleep(0.5)
    seen: set[float] = set()
    t_end = asyncio.get_running_loop().time() + 3.0
    while asyncio.get_running_loop().time() < t_end:
        seen.add(client.state["vehicle"]["t_ms"])
        await asyncio.sleep(0.02)
    assert len(seen) >= 20      # ~10 Hz of distinct fixes in 3 s, not the 2 Hz of the periodic view publish
