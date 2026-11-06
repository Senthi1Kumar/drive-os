"""Session state shared by the assistant, the view-model and the agent tools."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from ..domain.models import Cart, GeoPoint, Menu, MenuItem, Place, Route
from ..wallet.auth import OtpChallenge
from ..wallet.authority import ConsentRequest
from ..wallet.saga import Transaction

_ids = itertools.count(1)
_route_ids = itertools.count(1)


@dataclass
class Checkout:
    req: ConsentRequest
    allowed: list[str]                 # methods on offer for this purchase
    stage: str = "choose"              # choose | face | otp | voice | processing | failed
    method: str = ""
    otp: OtpChallenge | None = None
    otp_entry: str = ""
    error: str = ""
    attempts: int = 0
    bio: dict[str, Any] | None = None  # voice-first verification progress: {stage, challenge, detail}
    risk: dict[str, Any] | None = None  # DriveAuth {score, rule} of the last decision (no biometric data)
    otp_waiter: Any = None             # future resolved when a code entered during biometric step-up is checked


@dataclass
class Shop:
    view: str = "idle"                 # idle | places | menu | item | cart | checkout | order
    category: str = ""
    query: str | None = None
    places: list[Place] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    status: str = ""
    searching: bool = False
    place: Place | None = None         # the open merchant
    menu: Menu | None = None
    menu_category: str | None = None
    item: MenuItem | None = None
    item_qty: int = 1
    item_sel: dict[str, list[str]] = field(default_factory=dict)
    cart: Cart | None = None
    cart_place: Place | None = None
    checkout: Checkout | None = None
    tx: Transaction | None = None
    order_place: Place | None = None
    awaiting: str = ""                 # replace_cart | clear_cart | confirm_qty | drive_offer
    denied: tuple[str, str] | None = None   # (cart signature, spoken reason) while a policy denial is showing
    pending: list[dict[str, Any]] = field(default_factory=list)
    arrived: bool = False


@dataclass
class Voice:
    mode: str = "idle"                 # idle | listening | processing | speaking
    label: str = "Ready"
    text: str = ""                     # Nova's line
    heard: str = ""                    # the user's line
    available: bool = True
    wake: bool = False
    phrase: str = "hey nova"
    route: str = ""                    # rules | agent | router | offline: who is answering this turn
    step: int = 0                      # model call number within the turn (0 = none in flight)
    offline: bool = False              # the model path is down: rules only
    user_speaking: bool = False        # local only: the driver is mid-turn; turn guidance waits
    continuous: bool = False           # local only: a wake-once conversation keeps the mic open


@dataclass
class Nav:
    mode: str = "idle"                 # idle | overview | navigating | arrived
    dest_name: str = ""
    dest: GeoPoint | None = None
    dest_kind: str = ""                # place | saved | order
    dest_place: Place | None = None
    route: Route | None = None
    estimated: bool = False            # offline estimate, not a routed path
    reroutes: int = 0                  # reroutes done in this trip
    rerouted_at: float = 0.0           # time.monotonic() of the last one
    via: list[GeoPoint] = field(default_factory=list)   # stops added along the way, in route order
    via_names: list[str] = field(default_factory=list)
    route_id: int = field(default_factory=lambda: next(_route_ids))   # stable for a trip; a re-plan keeps it
    route_reason: str = "new"          # new | refresh | reroute: why the current route line was (re)issued


@dataclass
class Message:
    role: str
    text: str
    id: int = field(default_factory=lambda: next(_ids))
