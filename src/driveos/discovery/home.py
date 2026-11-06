"""The Home page's "Near you" strip: a few of the closest real places in each everyday category."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ..domain.errors import DriveOSError
from ..domain.models import GeoPoint, Place
from .service import DiscoveryService

log = logging.getLogger("driveos.home")

HOME_CATEGORIES = ("coffee", "food", "bar", "hotel", "pharmacy", "ev", "gas", "store")


def interleave(groups: list[list[Place]], cap: int) -> list[Place]:
    """Round-robin across categories so the strip is varied, nearest first within each."""
    out: list[Place] = []
    seen: set[str] = set()
    for rank in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if rank < len(g) and g[rank].id not in seen:
                seen.add(g[rank].id)
                out.append(g[rank])
    return out[:cap]


class HomeFeed:
    def __init__(self, discovery: DiscoveryService, *, categories: tuple[str, ...] = HOME_CATEGORIES,
                 per_category: int = 2, cap: int = 8, on_change: Callable[[], None] | None = None):
        self.discovery = discovery
        self.categories = categories
        self.per_category = per_category
        self.cap = cap
        self.on_change = on_change
        self.places: list[Place] = []
        self.counts: dict[str, int] = {}

    async def refresh(self, position: GeoPoint) -> None:
        async def one(cat: str) -> list[Place]:
            try:
                res = await self.discovery.nearby(category=cat, query=None, position=position,
                                                  limit=self.per_category + 1)
                return res.places[: self.per_category]
            except DriveOSError as e:
                log.info("home %s: %s", cat, e)
                return []

        groups = await asyncio.gather(*(one(c) for c in self.categories))
        for p, cat in ((p, c) for c, g in zip(self.categories, groups, strict=True) for p in g):
            p.extra.setdefault("home_category", cat)
        self.counts = {c: len(g) for c, g in zip(self.categories, groups, strict=True)}
        self.places = interleave([g for g in groups], self.cap)
        if self.on_change:
            self.on_change()
