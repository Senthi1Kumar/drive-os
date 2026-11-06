"""Guidance helpers shared by the routers, tracker and snapshot: maneuver icons, congestion, polyline cache,
off-route detection and the reroute / ETA-refresh gates. Pure and clock-injectable so they are unit-testable."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import Route, RouteStep, VoiceCue
from .geo import encode_polyline, with_cumulative

CONGESTION_LETTER = {"unknown": "u", "low": "l", "moderate": "m", "heavy": "h", "severe": "s"}


def maneuver_icon(mtype: str | None, modifier: str | None) -> str:
    """Icon key for a Mapbox maneuver type + modifier. Keys: depart arrive straight turn_left turn_right
    slight_left slight_right sharp_left sharp_right uturn merge fork_left fork_right ramp_left ramp_right
    roundabout."""
    t = (mtype or "").lower()
    m = (modifier or "").lower()
    side = "left" if "left" in m else "right" if "right" in m else ""
    if t in ("depart", "arrive"):
        return t
    if t in ("roundabout", "rotary", "roundabout turn", "exit roundabout", "exit rotary"):
        return "roundabout"
    if t == "merge":
        return "merge"
    if t == "fork" and side:
        return f"fork_{side}"
    if t in ("on ramp", "off ramp", "ramp") and side:
        return f"ramp_{side}"
    if m == "uturn":
        return "uturn"
    if m.startswith("slight") and side:
        return f"slight_{side}"
    if m.startswith("sharp") and side:
        return f"sharp_{side}"
    if t in ("turn", "end of road", "continue", "new name", "notification") and side and m != "straight":
        return f"turn_{side}"
    return "straight"


def lanes_at(step: RouteStep | None, remaining_m: float) -> list[dict]:
    """Lane guidance in force `remaining_m` before the end of `step`, as nav.step.lanes (see NAV_CONTRACT.md).
    The banner in force is the one with the smallest `at_m` that is still >= the distance left."""
    if step is None:
        return []
    due = [b for b in step.banner_lanes if b.at_m >= remaining_m]
    if not due:
        return []
    banner = min(due, key=lambda b: b.at_m)
    out = []
    for ln in banner.lanes:
        d: dict = {"indications": list(ln.indications), "valid": ln.valid, "active": ln.active_indication is not None}
        if ln.active_indication:
            d["active_indication"] = ln.active_indication
        out.append(d)
    return out


def fit_congestion(letters: str, n_points: int) -> str:
    """Exactly points - 1 letters: pad with unknown, trim extras."""
    need = max(0, n_points - 1)
    return (letters + "u" * need)[:need]


def route_polyline6(route: Route) -> str:
    """Whole-route polyline6, encoded once per route object."""
    if not route.polyline6:
        route.polyline6 = encode_polyline(route.geometry, 6)
    return route.polyline6


def route_congestion(route: Route) -> str:
    return fit_congestion(route.congestion, len(route.geometry))


class OffRouteDetector:
    """Off-route once `fixes` consecutive fixes are farther than `threshold_m` from the route."""

    def __init__(self, threshold_m: float = 40.0, fixes: int = 3):
        self.threshold_m, self.fixes, self.count = threshold_m, max(1, fixes), 0

    def observe(self, offset_m: float) -> bool:
        self.count = self.count + 1 if offset_m > self.threshold_m else 0
        return self.count >= self.fixes

    def reset(self) -> None:
        self.count = 0


@dataclass
class RerouteGate:
    """One reroute at a time, with a cooldown after each so a bad fix stream cannot loop."""

    cooldown_s: float = 20.0
    inflight: bool = False
    last_done: float | None = None
    count: int = 0

    def allow(self, now: float) -> bool:
        if self.inflight:
            return False
        return self.last_done is None or now - self.last_done >= self.cooldown_s

    def start(self) -> None:
        self.inflight = True
        self.count += 1

    def done(self, now: float) -> None:
        self.inflight = False
        self.last_done = now


class EtaRefresh:
    """Fires every `interval_s` while navigating; the first call arms the timer. interval 0 disables."""

    def __init__(self, interval_s: float = 90.0):
        self.interval_s = interval_s
        self._last: float | None = None

    def due(self, now: float) -> bool:
        if self.interval_s <= 0:
            return False
        if self._last is None:
            self._last = now
            return False
        if now - self._last >= self.interval_s:
            self._last = now
            return True
        return False

    def reset(self, now: float | None = None) -> None:
        self._last = now


class CueTracker:
    """Which spoken turn instruction is due next. A step's voice cue fires once the distance left in that step
    drops to `at_m`. At most one cue waits while the speaker is busy; it is dropped when it goes stale (too old,
    or the vehicle has moved on to another step). Cues already due when the tracker is built are treated as spoken
    (the trip start line covers them), and the arrive step is never announced here."""

    def __init__(self, route: Route, along_m: float = 0.0, stale_s: float = 10.0):
        r = with_cumulative(route)
        self.stale_s = stale_s
        self._spans: list[tuple[float, float, list[VoiceCue]]] = []
        n = len(r.cumulative_m)
        for i, st in enumerate(r.steps):
            start = r.cumulative_m[min(st.geometry_index, n - 1)]
            end = r.cumulative_m[min(r.steps[i + 1].geometry_index, n - 1)] if i + 1 < len(r.steps) else r.cumulative_m[-1]
            cues = [] if st.maneuver.type == "arrive" else sorted(st.voice, key=lambda c: -c.at_m)
            self._spans.append((start, end, cues))
        self._done: set[tuple[int, int]] = set()
        self._pending: tuple[int, int, str, float] | None = None
        for key in self._due(along_m):
            self._done.add(key)

    def _step_at(self, along_m: float) -> int:
        idx = 0
        for i, (start, _end, _c) in enumerate(self._spans):
            if start <= along_m + 1.0:
                idx = i
        return idx

    def _due(self, along_m: float) -> list[tuple[int, int]]:
        if not self._spans:
            return []
        i = self._step_at(along_m)
        _start, end, cues = self._spans[i]
        left = end - along_m
        return [(i, j) for j, c in enumerate(cues) if left <= c.at_m and (i, j) not in self._done]

    def poll(self, along_m: float, now: float, blocked: bool = False) -> str | None:
        """The cue to speak now, or None. Call on every position update."""
        due = self._due(along_m)
        if due:
            for key in due:
                self._done.add(key)
            i, j = due[-1]                                  # the most recent threshold wins; older ones are skipped
            self._pending = (i, j, self._spans[i][2][j].text, now)
        p = self._pending
        if p is None:
            return None
        if p[0] != self._step_at(along_m) or now - p[3] > self.stale_s:
            self._pending = None
            return None
        if blocked:
            return None
        self._pending = None
        return p[2]
