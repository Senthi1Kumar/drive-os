"""Coverage audit of the generated-image matcher over every menu the app can show.

``python -m driveos.images.audit`` prints the numbers; ``tests/test_image_audit.py`` and
``tests/test_place_covers.py`` pin thresholds (menu repeats, cover pools of eight or more, no cover repeated in a
visible place list).
Items that carry a real photo (Square catalogue) are counted separately: the matcher is not used for them.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ..merchants import sample_menus
from ..merchants.local_catalog import LocalCatalog
from .matcher import ALL_KEYS, GENERIC_KEYS, assign_covers, assign_menu, base_key, cover_pool, key_for_place

ASSETS = Path(__file__).resolve().parents[3] / "hmi_lvgl" / "assets" / "food"


def _menu_stats(merchant_id: str, items: list, category: str) -> dict[str, Any]:
    picks = assign_menu(items, category)
    counts = Counter(picks[i.id] for i in items if not i.image_url)
    n = sum(counts.values())
    return {"merchant": merchant_id, "items": n, "real_photo": len(items) - n, "keys": dict(counts),
            "max_repeat": max(counts.values(), default=0), "unique": len(counts),
            "generic": sum(1 for i in items if not i.image_url and base_key(i.name, category, i.category or "") in GENERIC_KEYS),
            "missing_assets": sorted(k for k in counts if not (ASSETS / f"{k}.jpg").is_file()),
            "names": {i.id: (i.name, picks[i.id]) for i in items if not i.image_url}}


def sample_menu_stats() -> list[dict[str, Any]]:
    from ..domain.models import MenuItem
    out = []
    for kind in sample_menus.KINDS:
        cats = sample_menus.categories_for(kind)
        items = [MenuItem.model_validate(i) for c in cats for i in c["items"]]
        cat = {"coffee": "coffee", "tea": "coffee", "bakery": "bakery", "bar": "bar", "pharmacy": "pharmacy",
               "convenience": "store", "ev": "ev", "fuel": "gas", "sandwich": "sandwich"}.get(kind, "food")
        s = _menu_stats(f"sample:{kind}", items, cat)
        s["category"] = cat
        out.append(s)
    return out


def catalog_stats(sample: int = 0) -> list[dict[str, Any]]:
    cat = LocalCatalog(None)
    out = []
    mids = [m for m in cat.merchants if cat.merchants[m]["source"] == "catalog"]
    if sample:
        mids = mids[::max(1, len(mids) // sample)]
    for mid in mids:
        menu = cat.menu(mid)
        out.append(_menu_stats(mid, menu.items, (cat.merchants[mid]["tags"] or ["food"])[0]))
    return out


def venue_stats() -> list[dict[str, Any]]:
    from ..domain.models import GeoPoint, MenuItem
    from ..merchants import venues
    out = []
    for v in venues.build(GeoPoint(lat=37.236, lon=-121.97), {"coffee", "sandwich"}):
        items = [MenuItem.model_validate(i) for c in v["categories"] for i in c["items"]]
        out.append(_menu_stats(v["id"], items, v["tags"][0]))
    return out


MIN_POOL = 8
LIST_CATEGORIES = ("coffee", "tea", "food", "sandwich", "bakery", "bar", "gas", "ev", "store", "pharmacy", "hotel")


def place_cover_stats() -> dict[str, Any]:
    covers = {c: sorted(set(cover_pool(c)) | {key_for_place(c, "", f"id{i}") for i in range(40)})
              for c in LIST_CATEGORIES}
    return {"covers": covers, "none": [c for c, k in covers.items() if not k],
            "missing_assets": sorted({k for ks in covers.values() for k in ks
                                      if not (ASSETS / f"{k}.jpg").is_file()})}


def place_list_stats(lists_per_category: int = 30, size: int = 6) -> dict[str, Any]:
    """Cover repeats inside simulated visible place lists: ``lists_per_category`` random lists of ``size`` places per
    category plus mixed-category Home strips of eight. ``max_repeat`` is the most times one cover shows in a list."""
    import random
    from types import SimpleNamespace

    def place(pid: str, cat: str):
        return SimpleNamespace(id=pid, name=f"Place {pid}", category=cat, photo_url=None, extra={})

    rnd = random.Random(7)
    worst, lists = 1, 0
    for cat in LIST_CATEGORIES:
        for _ in range(lists_per_category):
            places = [place(f"mapbox:{rnd.getrandbits(48):012x}", cat) for _ in range(size)]
            counts = Counter(assign_covers(places).values())
            worst, lists = max(worst, max(counts.values())), lists + 1
    for _ in range(lists_per_category):
        places = [place(f"mapbox:{rnd.getrandbits(48):012x}", c) for c in rnd.sample(LIST_CATEGORIES, 8)]
        counts = Counter(assign_covers(places).values())
        worst, lists = max(worst, max(counts.values())), lists + 1
    pools = {c: len(set(cover_pool(c))) for c in LIST_CATEGORIES}
    return {"lists": lists, "max_repeat": worst, "pools": pools, "min_pool": min(pools.values()),
            "short_pools": [c for c, n in pools.items() if n < MIN_POOL],
            "missing_assets": sorted({k for c in LIST_CATEGORIES for k in cover_pool(c)
                                      if not (ASSETS / f"{k}.jpg").is_file()})}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = sum(r["items"] for r in rows)
    total = Counter()
    for r in rows:
        total.update(r["keys"])
    return {"menus": len(rows), "items": items, "unique_keys": len(total),
            "max_repeat_in_menu": max((r["max_repeat"] for r in rows), default=0),
            "generic_items": sum(r["generic"] for r in rows),
            "missing_assets": sorted({k for r in rows for k in r["missing_assets"]}),
            "most_repeated": total.most_common(12)}


def main() -> None:
    sets = {"sample_menus": sample_menu_stats(), "venues": venue_stats(), "catalog_all": catalog_stats()}
    rep = {k: summarise(v) for k, v in sets.items()}
    rep["places"] = place_cover_stats()
    rep["place_lists"] = place_list_stats()
    rep["all_keys"] = len(ALL_KEYS)
    rep["worst_menus"] = sorted(((r["max_repeat"], r["merchant"]) for v in sets.values() for r in v), reverse=True)[:8]
    json.dump(rep, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
