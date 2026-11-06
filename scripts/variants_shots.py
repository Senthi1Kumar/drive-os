#!/usr/bin/env python3
"""Headless review screenshots of shop screens driven through a real back-end on a spare port.

Usage: .venv/bin/python scripts/variants_shots.py OUT_DIR [--port 8793] [--hmi hmi_lvgl/build/driveos_hmi]

Starts the back-end (no voice, simulated payments) on 127.0.0.1:PORT, builds Mapbox-style sample places in
process, drives Nova through the scenarios and renders each with the LVGL HMI (``--screenshot``). Prints every HMI
log line that mentions an image that could not be loaded, so a "Couldn't get info about the image" shows up here.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
from pathlib import Path

from driveos.config import load_settings
from driveos.domain.models import GeoPoint
from driveos.merchants.sample_menus import merchant_record
from driveos.server.runner import build_backend

HERE = Path(__file__).resolve().parents[1]
BAD = re.compile(r"couldn't get info|image|decoder", re.I)
PLACES = [
    ("cafe", "Roast & Bean", "coffee", ["coffee"], 0.2),
    ("cafe2", "Corner Espresso", "coffee", ["cafe"], 0.4),
    ("market", "Fresh Market", "store", ["convenience_store"], 0.3),
    ("rx", "Valley Pharmacy", "pharmacy", ["pharmacy"], 0.5),
    ("gas", "Summit Gas", "gas", ["gas_station"], 0.6),
    ("ev", "Plug Point", "ev", ["charging_station"], 0.7),
    ("hotel", "Grand Hotel", "hotel", ["hotel"], 0.9),
    ("bar", "Taproom 9", "bar", ["brewery"], 1.1),
    ("pizza", "Slice House", "food", ["pizza_restaurant"], 1.2),
    ("thai", "Bangkok Kitchen", "food", ["thai_restaurant"], 1.4),
]


async def hmi(binary: Path, port: int, out: Path, screen: str = "shop", after_ms: int = 2500) -> list[str]:
    p = await asyncio.create_subprocess_exec(
        str(binary), "--url", f"ws://127.0.0.1:{port}/v1/ui", "--screen", screen, "--screenshot", str(out),
        "--shot-after", str(after_ms), "--backend", "headless",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(HERE / "hmi_lvgl"))
    data, _ = await asyncio.wait_for(p.communicate(), 60)
    return [ln for ln in data.decode(errors="replace").splitlines() if BAD.search(ln)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", type=int, default=8793)
    ap.add_argument("--hmi", default=str(HERE / "hmi_lvgl/build/driveos_hmi"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = load_settings(data_dir=Path(tempfile.mkdtemp(prefix="variants_shots_")))
    settings.merchants.sources = ["local"]
    settings.location.auto = False
    backend = build_backend(settings, voice=False, host="127.0.0.1", port=a.port)
    await backend.start()
    nova = backend.services.nova
    catalog = backend.services.registry.get("local").catalog
    places = []
    for i, (pid, name, cat, pois, miles) in enumerate(PLACES):
        rec = merchant_record({"id": pid, "name": name, "address": f"{i + 1} Main St",
                               "location": GeoPoint(lat=37.236 + i * 0.001, lon=-121.974), "categories": pois}, cat)
        if rec is None:                                          # hotels have nothing to order
            from driveos.domain.models import Place
            places.append(Place(id=f"local:mbx-{pid}", merchant_id=f"mbx-{pid}", provider="local", name=name, category=cat,
                                location=GeoPoint(lat=37.236, lon=-121.974),
                                extra={"distance_m": miles * 1609, "orderable": False, "sample_menu": False}))
            continue
        catalog.add_external(rec)
        pl = catalog.place(rec, provider="local")
        pl.extra.update(distance_m=miles * 1609.0, orderable=True, has_menu=True, sample_menu=True)
        places.append(pl)
    by = {p.id.split(":")[-1]: p for p in places}
    nova.home_feed.places = places[:8]
    problems: list[str] = []

    async def shot(name: str, screen: str = "shop") -> None:
        backend.server.mark_dirty()
        await asyncio.sleep(0.6)
        bad = await hmi(Path(a.hmi), a.port, out / f"variants_{name}.png", screen)
        problems.extend(f"{name}: {b}" for b in bad)
        print("shot", out / f"variants_{name}.png")

    async def open_menu(pid: str):
        nova.shop.places = [by[pid]]
        await nova.open_place(place_id=by[pid].id, speak=False)

    nova.show_tab("home")
    await shot("home", "home")
    nova.shop.places = places
    nova.shop.category = "food"
    nova.shop.view = "places"
    nova.show_tab("shop")
    await shot("places")
    await open_menu("mbx-cafe")
    await shot("coffee_menu")
    latte = next(i for i in nova.shop.menu.items if i.name == "Latte")
    await nova.open_item(latte.id, speak=False)
    await nova.set_item_option({"size": ["s20"], "temp": ["iced"], "milk": ["oat"], "syrup": ["vanilla"]})
    await shot("latte_item")
    await nova.add_item(latte.id, 1, {"size": ["s20"], "temp": ["iced"], "milk": ["oat"], "syrup": ["vanilla"]},
                        speak=False)
    await open_menu("mbx-market")
    soda = next(i for i in nova.shop.menu.items if i.name == "Soda")
    await nova.open_item(soda.id, speak=False)
    await shot("soda_item")
    await nova.set_item_option({"brand": ["zero"]})
    await shot("soda_item_zero")
    await open_menu("mbx-cafe")
    await nova.add_item(latte.id, 1, {"size": ["s20"], "temp": ["iced"], "milk": ["oat"], "syrup": ["vanilla"]},
                        speak=False)
    await nova.view_cart(speak=False)
    await shot("cart")
    await nova.clear_cart(speak=False)
    await open_menu("mbx-market")
    for name, sel, qty in (("Soda", {"brand": ["diet"]}, 2), ("Energy Drink", {"brand": ["redbull"]}, 1),
                           ("Chips", {"brand": ["doritos"]}, 1)):
        it = next(i for i in nova.shop.menu.items if i.name == name)
        await nova.add_item(it.id, qty, sel, speak=False)
    await nova.view_cart(speak=False)
    await shot("cart_brands")
    await open_menu("mbx-gas")
    await shot("gas_menu")
    await open_menu("mbx-rx")
    await shot("pharmacy_menu")
    await open_menu("mbx-thai")
    await shot("thai_menu")
    await backend.stop()
    if problems:
        print("IMAGE WARNINGS:", *problems, sep="\n  ")
        return 1
    print("no image warnings")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
