"""Domain model shared by navigation, commerce, wallet, voice and UI layers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .money import Money

# ---------------------------------------------------------------- geography


class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    lat: float
    lon: float

    def as_lonlat(self) -> list[float]:
        return [self.lon, self.lat]


class Maneuver(BaseModel):
    type: str = "continue"  # turn, depart, arrive, merge, fork, roundabout ...
    modifier: str | None = None  # left, right, slight left, straight, uturn ...
    instruction: str = ""
    location: GeoPoint
    bearing_after: float | None = None


class VoiceCue(BaseModel):
    """A spoken prompt for a step; `at_m` is the distance before the step's end at which to say it."""

    at_m: float
    text: str


class Lane(BaseModel):
    """One lane of a lane-guidance banner. `valid`: the lane can make the maneuver; `active_indication`: which of its
    arrows the route takes (only on valid lanes)."""

    indications: list[str] = Field(default_factory=list)
    valid: bool = False
    active_indication: str | None = None


class BannerLanes(BaseModel):
    """The lanes of one banner instruction; `at_m` is the distance before the step's end where it comes into force."""

    at_m: float
    lanes: list[Lane] = Field(default_factory=list)


class RouteStep(BaseModel):
    name: str = ""
    distance_m: float
    duration_s: float
    maneuver: Maneuver
    banner: str | None = None
    # index into Route.geometry where this step's maneuver happens
    geometry_index: int = 0
    icon: str = ""  # maneuver icon key (nav.guidance.maneuver_icon) for this step's own maneuver
    voice: list[VoiceCue] = Field(default_factory=list)
    banner_lanes: list[BannerLanes] = Field(default_factory=list)  # lane guidance for this step's upcoming maneuver


class Route(BaseModel):
    geometry: list[GeoPoint]
    distance_m: float
    duration_s: float
    steps: list[RouteStep] = Field(default_factory=list)
    summary: str = ""
    destination_name: str = ""
    waypoint_names: list[str] = Field(default_factory=list)
    # cumulative distance (m) along geometry for each vertex; filled by nav.geo
    cumulative_m: list[float] = Field(default_factory=list)
    # whole-route polyline6 (lat, lon) and one congestion letter per segment (u l m h s); filled lazily
    polyline6: str = ""
    congestion: str = ""


class SavedPlace(BaseModel):
    id: str
    name: str
    location: GeoPoint
    aliases: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- commerce


class PickupMode(StrEnum):
    CURBSIDE = "curbside"
    DRIVE_THRU = "drive_thru"
    WALK_IN = "walk_in"
    WINDOW = "window"
    PUMP = "pump"          # fuel: the pump is unlocked for the prepaid amount
    CHARGER = "charger"    # EV: a charging session is started on the assigned stall
    IN_STORE = "in_store"  # retail: ready at the pickup counter


class Place(BaseModel):
    """A discovered, orderable merchant positioned relative to the active route."""

    id: str
    merchant_id: str
    provider: str
    name: str
    location: GeoPoint
    address: str = ""
    category: str = "coffee"
    cuisine: str = ""
    rating: float | None = None
    rating_count: int | None = None
    price_level: int | None = None  # 1..4
    photo_url: str | None = None
    logo_url: str | None = None
    open_now: bool | None = True
    pickup_modes: list[PickupMode] = Field(default_factory=lambda: [PickupMode.CURBSIDE])
    prep_minutes: int = 7
    detour_s: float | None = None
    distance_along_route_m: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def detour_min(self) -> int:
        return max(1, round((self.detour_s or 60) / 60))


class ModifierOption(BaseModel):
    id: str
    name: str
    price_delta: Money = Field(default_factory=Money.zero)
    aliases: list[str] = Field(default_factory=list)
    default: bool = False
    image: str = ""  # generated-image key (hmi assets/food); the item photo follows the selected option


class ModifierGroup(BaseModel):
    id: str
    name: str
    min_select: int = 0
    max_select: int = 1
    options: list[ModifierOption]
    aliases: list[str] = Field(default_factory=list)

    def default_ids(self) -> list[str]:
        return [o.id for o in self.options if o.default]

    def option(self, option_id: str) -> ModifierOption | None:
        return next((o for o in self.options if o.id == option_id), None)


class MenuItem(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = ""
    base_price: Money
    image_url: str | None = None
    modifier_groups: list[ModifierGroup] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    available: bool = True
    popular_rank: int | None = None
    # provider-specific identifiers (e.g. Square variation id)
    refs: dict[str, str] = Field(default_factory=dict)

    def group(self, group_id: str) -> ModifierGroup | None:
        return next((g for g in self.modifier_groups if g.id == group_id), None)


class Menu(BaseModel):
    merchant_id: str
    currency: str = "USD"
    categories: list[str] = Field(default_factory=list)
    items: list[MenuItem]

    def item(self, item_id: str) -> MenuItem | None:
        return next((i for i in self.items if i.id == item_id), None)


class CartLine(BaseModel):
    line_id: str
    item_id: str
    name: str
    quantity: int = 1
    selections: dict[str, list[str]] = Field(default_factory=dict)  # group_id -> option ids
    unit_price: Money
    note: str = ""

    @property
    def total(self) -> Money:
        return self.unit_price * self.quantity


class Cart(BaseModel):
    merchant_id: str
    lines: list[CartLine] = Field(default_factory=list)

    @property
    def subtotal(self) -> Money:
        cur = self.lines[0].unit_price.currency if self.lines else "USD"
        total = Money.zero(cur)
        for line in self.lines:
            total = total + line.total
        return total

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)


class QuoteLine(BaseModel):
    item_id: str
    name: str
    quantity: int
    description: str = ""  # human summary of modifiers
    selections: dict[str, list[str]] = Field(default_factory=dict)
    total: Money


class Quote(BaseModel):
    """A binding price from the merchant. The only source of amounts shown for consent."""

    quote_id: str
    merchant_id: str
    merchant_name: str
    provider: str
    lines: list[QuoteLine]
    subtotal: Money
    tax: Money
    fees: Money
    total: Money
    prep_minutes: int
    expires_at: datetime
    provider_ref: dict[str, Any] = Field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable hash over everything a consent binds to (merchant, lines, amounts)."""
        payload = {
            "merchant": self.merchant_id,
            "provider": self.provider,
            "lines": [
                [ln.item_id, ln.quantity, sorted((k, sorted(v)) for k, v in ln.selections.items()), ln.total.amount]
                for ln in self.lines
            ],
            "subtotal": self.subtotal.amount,
            "tax": self.tax.amount,
            "fees": self.fees.amount,
            "total": self.total.amount,
            "currency": self.total.currency,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OrderStatus(StrEnum):
    PLACED = "placed"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    PICKED_UP = "picked_up"
    REJECTED = "rejected"
    CANCELED = "canceled"


class PlacedOrder(BaseModel):
    order_id: str
    merchant_id: str
    provider: str
    pickup_code: str
    status: OrderStatus = OrderStatus.PLACED
    ready_at: datetime | None = None
    pickup_instructions: str = ""
    pickup_spot: str = ""
    provider_ref: dict[str, Any] = Field(default_factory=dict)


class OrderUpdate(BaseModel):
    order_id: str
    status: OrderStatus
    ready_at: datetime | None = None
    message: str = ""
    pickup_spot: str = ""
    at: datetime


# ---------------------------------------------------------------- identity & payment


class AuthTier(StrEnum):
    T0_MANDATE = "T0"
    T1_VOICE = "T1"
    T2_STRONG = "T2"
    T3_OUT_OF_BAND = "T3"


class Payer(BaseModel):
    id: str
    display: str  # "Visa ending 4417"
    brand: str = "visa"
    last4: str = ""
    owner_name: str = ""
    label: str = "Personal"
    provider: str = "simulated"
    token: str = ""  # PSP payment method / card-on-file id — never a PAN
    customer_ref: str = ""


class Occupant(BaseModel):
    id: str
    name: str
    role: str = "owner"  # owner | driver | passenger | valet | rental
    payer_ids: list[str] = Field(default_factory=list)
    default_payer_id: str | None = None
    phone: str = ""  # for one-time codes
    usual: dict[str, Any] | None = None  # {"merchant_id","item_id","selections"}


class TransactionProposal(BaseModel):
    """What the assistant is allowed to produce. Never moves money by itself."""

    proposal_id: str
    occupant_id: str
    payer_id: str
    merchant_id: str
    provider: str
    cart: Cart
    quote_id: str
    quote_hash: str
    stated_total: Money
    pickup_eta: datetime | None = None
    customer_name: str = ""


class PaymentAuthorization(BaseModel):
    auth_id: str
    provider: str
    amount: Money
    status: str  # authorized | captured | voided | refunded | declined
    provider_ref: dict[str, Any] = Field(default_factory=dict)
    decline_reason: str = ""
