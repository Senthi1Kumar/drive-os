"""Follows the vehicle along the active route: progress, next maneuver, remaining time, off-route."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..core.ids import utcnow
from ..domain.models import GeoPoint, Route, RouteStep
from .geo import project, with_cumulative
from .guidance import OffRouteDetector

OFF_ROUTE_M = 40.0


@dataclass
class Progress:
    along_m: float
    remaining_m: float
    remaining_s: float
    eta: datetime
    step_index: int
    next_step: RouteStep | None
    distance_to_next_m: float
    off_route: bool
    fraction: float
    snapped: GeoPoint
    offset_m: float = 0.0


class RouteTracker:
    def __init__(self, route: Route, *, off_route_m: float = OFF_ROUTE_M, off_route_fixes: int = 3):
        self.route = with_cumulative(route)
        self._hint = 0
        self._off = OffRouteDetector(off_route_m, off_route_fixes)

    def update(self, position: GeoPoint, speed_mps: float = 0.0, now: datetime | None = None) -> Progress:
        r = self.route
        pr = project(r, position, hint=self._hint, window=80)
        if pr.offset_m > self._off.threshold_m * 0.5:  # re-acquire globally before declaring off-route
            pr = project(r, position)
        self._hint = pr.index
        off = self._off.observe(pr.offset_m)
        total = r.cumulative_m[-1] or 1.0
        along = pr.along_m
        remaining = max(0.0, total - along)
        avg_speed = r.distance_m / r.duration_s if r.duration_s else 13.0
        # blend live speed (when moving) with the route's traffic-aware average
        eff_speed = avg_speed if speed_mps < 2 else 0.6 * avg_speed + 0.4 * speed_mps
        remaining_s = remaining / max(eff_speed, 1.0)

        step_idx = 0
        for i, s in enumerate(r.steps):
            if r.cumulative_m[min(s.geometry_index, len(r.cumulative_m) - 1)] <= along + 1.0:
                step_idx = i
        nxt = r.steps[step_idx + 1] if step_idx + 1 < len(r.steps) else None
        dist_next = (r.cumulative_m[min(nxt.geometry_index, len(r.cumulative_m) - 1)] - along) if nxt else remaining
        now = now or utcnow()
        return Progress(
            along_m=along, remaining_m=remaining, remaining_s=remaining_s,
            eta=now + timedelta(seconds=remaining_s), step_index=step_idx, next_step=nxt,
            distance_to_next_m=max(0.0, dist_next), off_route=off,
            fraction=min(1.0, along / total), snapped=pr.point, offset_m=pr.offset_m,
        )

    def distance_to(self, point: GeoPoint, from_along_m: float) -> float:
        """Distance along the route from `from_along_m` to the projection of `point`."""
        pr = project(self.route, point)
        return pr.along_m - from_along_m
