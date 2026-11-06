"""Which image the HMI shows for a menu item or a place, as relative paths under the image root."""

from __future__ import annotations

from typing import Any

from .matcher import assign_covers, assign_menu, cover_pool, key_for, key_for_place
from .store import ImageStore, Thumb


def _fields(t: Thumb | None) -> dict[str, Any]:
    return {"img": t.small, "img_lg": t.large} if t else {}


def option_key(item, selections: dict[str, list[str]] | None = None) -> str:
    """Image key of the selected option that carries one (defaults when nothing is selected), else ''."""
    for g in item.modifier_groups:
        chosen = (selections or {}).get(g.id) or g.default_ids()
        for oid in chosen:
            o = g.option(oid)
            if o and o.image:
                return o.image
    return ""


def menu_picks(items, category: str = "") -> dict[str, str]:
    """Generated-image key per item id for one menu (alternates handed out to avoid neighbouring repeats)."""
    return assign_menu(items, category)


def item_image(store: ImageStore | None, item, *, category: str = "", key: str = "",
               selections: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """A real photo when the item has one (empty until it is downloaded), else the generated image.
    A selected option with its own image (a brand) wins over the item's."""
    if store is None:
        return {}
    url = getattr(item, "image_url", None)
    if url:
        return _fields(store.thumb(url))
    k = option_key(item, selections) or key or key_for(item.name, category, item.category or "", item.id)
    return _fields(store.generated(k))


def option_image(store: ImageStore | None, option) -> dict[str, Any]:
    if store is None or not option.image:
        return {}
    return _fields(store.generated(option.image))


def _kind_from_name(place) -> str:
    from ..merchants.sample_menus import kind_for
    return kind_for(place.category, place.name) or ""


def cover_picks(places) -> dict[str, str]:
    """Generated cover key per place id for one visible list, with no repeated cover among its first cards.
    Cached per list, so the pictures do not reshuffle from one snapshot to the next."""
    places = list(places)
    return assign_covers(places, {p.id: _kind_from_name(p) for p in places if not p.extra.get("sample_kind")})


def place_image(store: ImageStore | None, place, picks: dict[str, str] | None = None) -> dict[str, Any]:
    """Brand cover when the place has one (empty until downloaded), else a cover for its kind of business.
    ``picks`` (from ``cover_picks`` for the whole list) keeps covers distinct inside a list; without it the cover
    is chosen by place id alone."""
    if store is None:
        return {}
    if place.photo_url:
        t = store.thumb(place.photo_url)
        if t:
            return _fields(t)
    kind = place.extra.get("sample_kind") or _kind_from_name(place)
    key = (picks or {}).get(place.id) or key_for_place(place.category, kind, place.id, place.name,
                                                       place.extra.get("poi_categories") or ())
    for k in (key, *(c for c in cover_pool(place.category, kind) if c != key)):     # a missing file is skipped
        t = store.generated(k)
        if t:
            return _fields(t)
    return {}


def name_image(store: ImageStore | None, name: str, category: str = "") -> dict[str, Any]:
    """Generated image for a bare item name (cart lines whose menu is no longer loaded)."""
    if store is None:
        return {}
    return _fields(store.generated(key_for(name, category)))
