"""Nova — the DriveOS in-car assistant.

One object owns the conversation, the shop session, checkout and the drive:

    wake → ask ("find me a sandwich") → nearby options on screen → choose → menu → add to cart
         → check out → approve (voice / Face ID / one-time code) → order placed → live status
         → "take me there" → route, ETA, arrival → pickup code → handed over

Every step is reachable by voice and by touch; both paths call the same methods (``discover``,
``open_place``, ``add_item``, ``checkout``, ``choose_auth``…), which are also the tools the OpenRouter agent
calls. Money only moves through ``TransactionAuthority`` + ``OrderOrchestrator``: the assistant builds a
proposal; the Authority re-prices, applies policy and picks the approval tier.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol

from .. import fmt
from ..biometrics.preview import HUB, PreviewHub
from ..biometrics.rung import rung_available
from ..biometrics.verifier import FaceFirstVerifier
from ..config import Settings
from ..core.ids import new_id, utcnow
from ..core.logging_setup import describe_text
from ..discovery.service import DiscoveryService, noun
from ..domain.categories import CATEGORIES
from ..domain.errors import (
    AuthenticationFailed,
    DriveOSError,
    OrderingUnavailable,
    PolicyDenied,
    QuoteMismatch,
)
from ..domain.models import (
    AuthTier,
    Cart,
    GeoPoint,
    OrderStatus,
    Payer,
    PickupMode,
    Place,
    Route,
    TransactionProposal,
)
from ..merchants.base import make_line, normalise_selections
from ..merchants.registry import MerchantRegistry
from ..nav.geo import haversine, project, splice_stop
from ..nav.guidance import CueTracker, EtaRefresh, RerouteGate
from ..nav.router import Router
from ..nav.tracker import Progress, RouteTracker
from ..nav.vehicle import VehicleSource, VehicleState
from ..wallet.auth import (
    AuthResult,
    FaceVerifier,
    NoFaceVerifier,
    OtpService,
    SimulatedFaceVerifier,
    voice_consent,
)
from ..wallet.authority import TransactionAuthority
from ..wallet.ledger import Ledger
from ..wallet.saga import OrderOrchestrator, Transaction
from .intents import Context, Intent, IntentParser, is_prepay
from .state import Checkout, Message, Nav, Shop, Voice

log = logging.getLogger("driveos.nova")

ARRIVE_RADIUS_M = 60.0
SPOKEN_PICKUP = {PickupMode.CURBSIDE: "curbside pickup", PickupMode.DRIVE_THRU: "a drive-thru",
                 PickupMode.WALK_IN: "counter pickup", PickupMode.WINDOW: "a pickup window",
                 PickupMode.PUMP: "pump prepay", PickupMode.CHARGER: "stall reservation",
                 PickupMode.IN_STORE: "in-store pickup"}


class Speaker(Protocol):
    async def say(self, text: str) -> None: ...
    def stop(self) -> None: ...


class UiSink(Protocol):
    def ui(self, state: dict[str, Any]) -> None: ...
    def route(self, coords: list[list[float]], bounds: list[float] | None) -> None: ...
    def markers(self, markers: list[dict[str, Any]]) -> None: ...
    def vehicle(self, v: dict[str, Any]) -> None: ...
    def camera(self, mode: str) -> None: ...


OTP_VOCABULARY = "The code is digits: zero one two three four five six seven eight nine."
OTP_REPROMPT = "I'm waiting for the code. Say the digits from your phone, or type it. Say cancel to stop."
_FREE_TEXT_SLOTS = ("query", "name", "text", "note", "phone", "destination")


def _loggable_slots(slots: dict[str, Any]) -> dict[str, Any]:
    """Slots for a log line: catalogue terms stay, free text is reduced to its length, codes never appear."""
    out: dict[str, Any] = {}
    for k, v in slots.items():
        if k == "items":
            continue
        if k == "code":
            out[k] = f"<{len(str(v))} digits>"
        elif k in _FREE_TEXT_SLOTS and isinstance(v, str):
            out[k] = describe_text(v)
        else:
            out[k] = v
    return out


class NullSpeaker:
    async def say(self, text: str) -> None:
        log.info("NOVA: %s", describe_text(text))

    def stop(self) -> None:
        pass


class Nova:
    def __init__(self, settings: Settings, *, router: Router, vehicle: VehicleSource, discovery: DiscoveryService,
                 registry: MerchantRegistry, ledger: Ledger, authority: TransactionAuthority,
                 orchestrator: OrderOrchestrator, face: FaceVerifier, otp: OtpService,
                 speaker: Speaker | None = None, sink: UiSink | None = None, brain: Any | None = None):
        self.s = settings
        self.router = router
        self.vehicle = vehicle
        self.discovery = discovery
        self.registry = registry
        self.ledger = ledger
        self.authority = authority
        self.orch = orchestrator
        self.face = face
        self.otp = otp
        self._verifier = None                  # the payment verifier currently scanning, so cancel reaches it
        self.preview: PreviewHub = HUB         # live face camera state for the checkout sheet
        self.verified_hold_s = 1.2             # how long a verified face stays on screen before the order goes through
        self.bio_capture = False               # voice pipeline stays out of the way while a challenge is captured
        if hasattr(face, "bind"):
            face.bind(self)
        self.speaker: Speaker = speaker or NullSpeaker()
        self.sink = sink
        self.brain = brain                     # optional OpenRouter tool agent (agent/harness)
        self.parser = IntentParser()
        self.units = settings.display.units
        self.occupant = settings.occupant()
        self.payer_id = self.occupant.default_payer_id or (self.occupant.payer_ids or [""])[0]
        self.tab = "home"
        self.shop = Shop()
        self.voice = Voice()
        self.nav = Nav()
        self.transcripts: list[Message] = []
        self.doc = ""                          # Agent tab markdown
        self.toast = ""
        self.toast_seq = 0
        self.phone_note: dict[str, Any] | None = None
        self.location_label = settings.vehicle.start_label
        self.last_spoken = ""
        self.tracker: RouteTracker | None = None
        self.progress: Progress | None = None
        self.vstate: VehicleState | None = vehicle.state
        self.request_listen: Callable[[], None] | None = None
        self.request_follow_up: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._last_ui = 0.0
        self._arrival_since: float | None = None
        self._tasks: set[asyncio.Task] = set()
        self.home_nearby: list[Place] = []
        self.active: dict[str, dict[str, Any]] = {}   # tx_id -> {tx, place, arrived, since}: orders in flight
        self._speak_lock = asyncio.Lock()
        self._pending_speech = 0
        self.geocoder: Callable[[GeoPoint], Any] | None = None   # async point -> "Town, State" (container)
        self._label_anchor: GeoPoint | None = None               # where the location label was last resolved
        self._relabel_pending = False
        self._clock: Callable[[], float] = time.monotonic    # guidance timers (reroute gate, ETA refresh, cues)
        self._gate = RerouteGate(settings.nav.reroute_cooldown_s)
        self._eta = EtaRefresh(settings.nav.eta_refresh_s)
        self._eta_busy = False
        self._cues: CueTracker | None = None
        self.capture_busy: Callable[[], bool] | None = None  # an enrollment capture is under way (set by the server)
        self.weather_poke: asyncio.Event | None = None
        vehicle.subscribe(self._on_vehicle)
        gps = getattr(vehicle, "gps", None)
        if gps is not None:
            gps.status_listeners.append(self._on_gps_status)
        orchestrator.subscribe(self._on_tx)

    # ================================================================== lifecycle
    async def start(self) -> None:
        recovered = await self.orch.recover()
        if recovered:
            log.info("recovered %d open transaction(s)", recovered)
        self.voice.label = "Ready"
        self.publish()
        self._bg(self._prefetch_nearby())

    def _bg(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _prefetch_nearby(self) -> None:
        """Home's "Closest to you" list."""
        try:
            res = await self.discovery.nearby(category="food", query=None, position=self._pos(), limit=4)
            self.home_nearby = res.places
            self.publish()
        except DriveOSError as e:
            log.warning("nearby prefetch failed: %s", e)

    # ================================================================== vehicle
    def _pos(self) -> GeoPoint:
        return self.vstate.position if self.vstate else self.s.vehicle.start

    def _moving(self) -> bool:
        return bool(self.vstate and self.vstate.moving)

    def _on_vehicle(self, v: VehicleState) -> None:
        self.vstate = v
        if v.source == "gps":
            self._maybe_relocate(v.position)
        if self.tracker:
            self.progress = self.tracker.update(v.position, v.speed_mps)
            if self.nav.mode == "navigating":
                now = self._clock()
                if self.progress.off_route and self._gate.allow(now):
                    self._gate.start()
                    self._bg(self._reroute())
                elif not self._eta_busy and self._gate.allow(now) and self._eta.due(now):
                    self._eta_busy = True
                    self._bg(self._refresh_eta())
                self._speak_guidance(self.progress, now)
        if self.sink:
            self.sink.vehicle({"lon": v.position.lon, "lat": v.position.lat, "heading": v.heading,
                               "speed": v.speed_mps, "frac": self.progress.fraction if self.progress else 0.0})
        self._check_arrival(v)
        now = time.monotonic()
        if now - self._last_ui > 0.5:
            self._last_ui = now
            self.publish()

    def _check_arrival(self, v: VehicleState) -> None:
        """Arrival at a destination (guidance) or at any active order's pickup — with or without guidance."""
        stopped = v.speed_mps < 1.0
        now = time.monotonic()
        if self.nav.mode == "navigating" and self.nav.dest:
            if stopped and haversine(v.position, self.nav.dest) < ARRIVE_RADIUS_M:
                if self._arrival_since is None:
                    self._arrival_since = now
                elif now - self._arrival_since > 1.0:
                    self._arrival_since = None
                    self._bg(self._arrived())
            else:
                self._arrival_since = None
        for tx_id, o in list(self.active.items()):
            if o["arrived"] or not stopped:
                o["since"] = None
                continue
            if haversine(v.position, o["place"].location) < ARRIVE_RADIUS_M:
                if o.get("since") is None:
                    o["since"] = now
                elif now - o["since"] > 1.5:
                    o["since"] = None
                    self._bg(self.order_arrived(tx_id))
            else:
                o["since"] = None

    async def _arrived(self) -> None:
        if self.nav.mode != "navigating":
            return
        self.nav.mode = "arrived"
        self.vehicle.set_gear("P")
        hold = getattr(self.vehicle, "hold", None)
        if hold:
            hold()
        place = self.nav.dest_place
        match = next((tid for tid, o in self.active.items()
                      if place and o["place"].merchant_id == place.merchant_id and not o["arrived"]), None)
        if match:
            await self.order_arrived(match)
        else:
            self.publish()
            await self.say(f"You've arrived at {self.nav.dest_name}.")

    async def order_arrived(self, tx_id: str | None = None) -> None:
        """At the pickup (detected, or the driver said / tapped "I'm here")."""
        tx_id = tx_id or (self.shop.tx.tx_id if self.shop.tx else None)
        o = self.active.get(tx_id or "")
        if not o or o["arrived"]:
            if not o:
                await self.say("You don't have an order waiting.")
            return
        o["arrived"] = True
        tx = self.orch.transactions.get(tx_id) or o["tx"]
        await self.orch.notify_arrival(tx_id)
        self.shop.tx, self.shop.order_place, self.shop.arrived = tx, o["place"], True
        self.tab, self.shop.view = "shop", "order"
        self.publish()
        await self.say(self._pickup_line(tx, o["place"]))

    def _pickup_line(self, tx: Transaction, place: Place) -> str:
        code = _spell(tx.order.pickup_code if tx.order else "")
        spot = (tx.order.pickup_spot if tx.order else "") or ""
        mode = place.pickup_modes[0] if place.pickup_modes else PickupMode.WALK_IN
        if mode == PickupMode.PUMP:
            return f"You're here. {spot or 'Your pump'} is unlocked — start fueling whenever you're ready."
        if mode == PickupMode.CHARGER:
            return f"You're here. Park at {spot.lower() or 'the reserved stall'} and plug in."
        if mode == PickupMode.CURBSIDE:
            return f"You're here. {tx.quote.merchant_name} is bringing it out — your code is {code}."
        return f"You're here. Your pickup code is {code}" + (f", at the {spot.lower()}." if spot else ".")

    # ================================================================== voice surface
    def set_voice_available(self, ok: bool, *, wake: bool = False, phrase: str = "") -> None:
        self.voice.available = ok
        self.voice.wake = wake
        if phrase:
            self.voice.phrase = phrase
        self.publish()

    async def wake(self, rest: str = "") -> None:
        """The wake phrase was heard: raise the voice float and listen."""
        self.voice.mode = "listening"
        self.voice.label = "Listening"
        self.voice.text = ""
        self.voice.heard = rest
        self.publish()

    def set_voice(self, mode: str, heard: str | None = None, *, keep: bool = False) -> None:
        self.voice.mode = mode
        self.voice.label = {"listening": "Listening", "processing": "Thinking", "speaking": "Speaking"}.get(
            mode, "Ready")
        if mode == "listening" and not keep:
            self.voice.text = ""
            self.voice.heard = ""
        if heard is not None:
            self.voice.heard = heard
        self.publish()

    async def say(self, text: str, *, listen: bool = False, transcript: bool = True) -> None:
        """One utterance at a time: overlapping lines queue, and the mic stays closed until the last ends."""
        self._pending_speech += 1
        try:
            async with self._speak_lock:
                self.last_spoken = text
                self.voice.mode = "speaking"
                self.voice.label = "Speaking"
                self.voice.text = text
                if transcript:
                    self.transcripts.append(Message("agent", text))
                    self.transcripts = self.transcripts[-60:]
                self.publish()
                await self.speaker.say(text)
        finally:
            self._pending_speech -= 1
            if self._pending_speech == 0:
                self.voice.mode = "idle"
                self.voice.label = "Nova"
                self.publish()
        if listen and self._pending_speech == 0 and self.request_follow_up:
            self.request_follow_up()

    def notify(self, msg: str) -> None:
        self.toast = msg
        self.toast_seq += 1
        self.publish()

    def set_face(self, face) -> None:
        """Swap the payment verifier at runtime (a fresh Voice ID enrollment switches ``auth.face=auto`` over)."""
        self.face = face
        if hasattr(face, "bind"):
            face.bind(self)

    # ---- Host interface for the voice-first verifier (biometrics/verifier.py)
    async def announce(self, text: str) -> None:
        await self.say(text, listen=False)

    def set_capturing(self, on: bool) -> None:
        self.bio_capture = on

    def set_status(self, stage: str, challenge: str | None, detail: str) -> None:
        co = self.shop.checkout
        if co is None:
            return
        co.bio = {"stage": stage, "challenge": challenge, "detail": detail}
        co.stage = ("voice" if co.method == "voice" else "face") if stage != "otp" else co.stage
        self.publish()

    async def hold_face_result(self, states: tuple[str, ...] = ("verified", "failed")) -> None:
        """Keep a face result (green or red brackets, the driver's name) on screen for a moment before the sheet moves
        on to the one-time code or the order."""
        co = self.shop.checkout
        if co is not None and co.method == "face" and self.verified_hold_s > 0 \
                and self.preview.snapshot()["face_state"] in states:
            await asyncio.sleep(self.verified_hold_s)

    def payment_context(self) -> dict[str, Any]:
        co = self.shop.checkout
        assert co is not None
        q = co.req.quote
        ctx: dict[str, Any] = {"amount": q.total.amount / 100.0, "merchant": q.merchant_name,
                               "known": self.ledger.has_completed_with(self.occupant.id,
                                                                       co.req.proposal.merchant_id)}
        v = self.vstate
        if v is not None:
            ctx["vehicle"] = {"lat": v.position.lat, "lon": v.position.lon, "speed_kmh": v.speed_mps * 3.6,
                              "parked": v.parked}
        place = self._place_for(place_id=co.req.proposal.merchant_id) or self.shop.cart_place
        if place is not None and place.location is not None:
            ctx["merchant_location"] = {"lat": place.location.lat, "lon": place.location.lon}
        return ctx

    def set_risk(self, risk: dict[str, Any]) -> None:
        co = self.shop.checkout
        if co is not None:
            co.risk = risk
            self.publish()

    async def otp_roundtrip(self, timeout_s: float) -> bool:
        """Biometric step-up: send a code to the phone, wait for the driver to enter it, report if it was right.
        The payment itself is not authorised here; the verifier goes back to DriveAuth for the decision."""
        co = self.shop.checkout
        if co is None:
            return False
        phone = self.occupant.phone or "+10000000000"
        co.otp = await self.otp.issue(phone, f"{_money_words(co.req.quote.total.amount)} at "
                                             f"{co.req.quote.merchant_name}")
        waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        co.otp_waiter, co.bio, co.stage, co.otp_entry = waiter, None, "otp", ""
        self.publish()
        await self.say(f"For this one I need a code. I sent it to your phone ending {phone[-4:]}. "
                       f"Read me the {co.otp.length} digits.", listen=True)
        try:
            return bool(await asyncio.wait_for(waiter, timeout_s))
        except TimeoutError:
            return False
        finally:
            co.otp_waiter = None
            if self.shop.checkout is co and co.stage == "otp":
                co.stage = "voice" if co.method == "voice" else "face"

    def show_otp_on_phone(self, code: str, purpose: str) -> None:
        """Demo delivery channel: the paired phone's notification, mirrored on the head unit."""
        self.phone_note = {"code": code, "purpose": purpose, "at": time.time()}
        self.publish()

    def otp_pending(self) -> bool:
        """A one-time code has been sent and is waiting to be entered (spoken or typed)."""
        co = self.shop.checkout
        return co is not None and co.stage == "otp" and co.otp is not None

    def vocabulary(self) -> str:
        if self.otp_pending():
            return OTP_VOCABULARY
        words = ["Nova", "DriveOS", "coffee", "sandwich", "gas station", "Face ID", "one-time code", "confirm",
                 "check out", "take me there"]
        words += [p.name for p in self.shop.places[:8]]
        if self.shop.menu:
            words += [it.name for it in self.shop.menu.items[:40]]
        seen, out = set(), []
        for w in words:
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        return ", ".join(out)

    # ================================================================== turns
    def barge_in(self) -> None:
        """The driver spoke over Nova or pressed the wheel: stop speech and abort any in-flight model call."""
        self.speaker.stop()
        if self.brain is not None and getattr(self.brain, "busy", False):
            self.brain.cancel()

    def _consent_open(self) -> bool:
        sh = self.shop
        return sh.checkout is not None or sh.view == "checkout" or sh.awaiting in ("replace_cart", "clear_cart", "confirm_qty")

    async def handle_text(self, text: str) -> None:
        if self.brain is not None and getattr(self.brain, "busy", False):
            self.brain.cancel()                # a new utterance supersedes the one still being thought about
        async with self._lock:
            intent = self.parser.parse(text, self._ctx())
            shown = self._shown_text(text, intent)
            self.voice.heard = shown
            self.voice.mode = "processing"
            self.voice.label = "Thinking"
            self.voice.text = ""
            self.transcripts.append(Message("you", shown))
            self.publish()
            log.info("heard %s -> %s %s", describe_text(text), intent.name, _loggable_slots(intent.slots))
            try:
                if self.brain and intent.name in ("unknown", "read_menu", "help") and \
                        intent.name not in self._rule_only():
                    if self._consent_open():
                        # consent turns never reach a model: rules parsed it above, and anything else is a re-prompt
                        if self.shop.checkout is not None:
                            await self.say(OTP_REPROMPT if self.otp_pending()
                                           else "Say approve, cancel, or use the screen.", listen=True)
                            return
                    else:
                        handled = await self.brain.turn(text, intent)
                        if handled:
                            return
                await self._dispatch(intent)
            except DriveOSError as e:
                log.warning("turn failed: %s (%s)", e, e.code)
                await self.say(e.spoken, listen=False)
            except Exception:
                log.exception("unexpected failure handling %s", describe_text(text))
                await self.say("Sorry, something went wrong there. Nothing was charged.")

    @staticmethod
    def _shown_text(text: str, intent: Intent) -> str:
        """What the screen and the transcript show for an utterance: a spoken code is masked, never echoed."""
        if intent.name == "otp_code":
            return "•" * len(intent.slots["code"])
        if intent.name == "otp_partial":
            return "•" * intent.slots["heard"]
        return text.strip()

    def _rule_only(self) -> set[str]:
        # approvals and codes never go through the model
        return {"approve", "otp_code", "otp_partial", "otp_prompt", "auth_method", "cancel_checkout"} \
            if self.shop.view == "checkout" else set()

    def _ctx(self) -> Context:
        sh = self.shop
        return Context(
            view=sh.view, places=[p.name for p in sh.places], menu=sh.menu, menu_category=sh.menu_category,
            item=sh.item if sh.view == "item" else None,
            cart_names=[ln.name for ln in sh.cart.lines] if sh.cart else [],
            checkout_stage=sh.checkout.stage if sh.checkout else "",
            otp_length=sh.checkout.otp.length if sh.checkout and sh.checkout.otp else 6,
            voice_allowed=bool(sh.checkout and "voice" in sh.checkout.allowed),
            has_order=bool(sh.tx and not sh.tx.terminal),
            saved_places={a: p.id for p in self.s.places for a in [p.name.lower(), *p.aliases]},
            awaiting=sh.awaiting,
            place_tags=list(sh.place.extra.get("tags") or []) if sh.place and sh.view in ("menu", "item") else [],
        )

    async def _dispatch(self, i: Intent) -> None:
        h = {
            "find": lambda: self.discover(i.slots["category"], i.slots.get("query")),
            "open_place": lambda: self.open_place(index=i.slots["index"]),
            "drive_to_place": lambda: self.drive_to(self.shop.places[i.slots["index"]], start=True),
            "more": self.more,
            "menu_category": lambda: self.select_menu_category(i.slots["category"], speak=True,
                                                               ask=i.slots.get("ask", False)),
            "open_item": lambda: self.open_item(i.slots["items"][0]["item_id"], qty=i.slots["items"][0]["qty"],
                                                selections=i.slots["items"][0]["selections"]),
            "add_items": lambda: self.add_items(i.slots["items"]),
            "add_item": lambda: self.add_item(i.slots["item_id"], i.slots.get("qty", 1),
                                              i.slots.get("selections") or {}),
            "set_option": lambda: self.set_item_option(i.slots["selections"]),
            "set_item_qty": lambda: self.set_item_qty(i.slots["qty"]),
            "read_menu": self.read_menu,
            "remove_line": lambda: self.remove_line(name=i.slots["name"]),
            "set_line_qty": lambda: self.set_line_qty_by_name(i.slots.get("name"), i.slots["qty"]),
            "view_cart": self.view_cart,
            "clear_cart": self.clear_cart,
            "checkout": self.checkout,
            "replace_cart": self._replace_cart,
            "keep_cart": self._keep_cart,
            "confirm_qty": self._confirm_quantity,
            "cancel_qty": self._cancel_quantity,
            "approve": lambda: self.approve_voice(under_lock=True),
            "auth_method": lambda: self.choose_auth(i.slots["method"], resend=i.slots.get("resend", False),
                                                    under_lock=True),
            "otp_code": lambda: self.submit_otp(i.slots["code"]),
            "otp_partial": lambda: self.otp_reprompt(i.slots["heard"], i.slots["need"]),
            "otp_prompt": lambda: self.say(OTP_REPROMPT, listen=True),
            "cancel_checkout": self.cancel_checkout,
            "status": self.order_status,
            "cancel_order": self.cancel_order,
            "update_eta": lambda: self.update_eta(i.slots["minutes"]),
            "im_here": self.order_arrived,
            "drive": self.drive_here,
            "stop_nav": self.stop_nav,
            "vehicle_mode": lambda: self.vehicle_mode_spoken(i.slots["mode"]),
            "navigate_saved": lambda: self.navigate_saved(i.slots["place_id"]),
            "navigate_to": lambda: self.navigate_to_query(i.slots["query"]),
            "find_along_route": lambda: self.find_along_route(i.slots["category"], i.slots.get("query"),
                                                              destination=i.slots.get("destination")),
            "add_stop": lambda: self.add_stop(index=i.slots.get("index")),
            "show_tab": lambda: self.show_tab_spoken(i.slots["tab"]),
            "back": self.back,
            "usual": self.usual,
            "help": self.help,
            "repeat": lambda: self.say(self.last_spoken or "I haven't said anything yet.", listen=True),
            "yes": self._yes,
            "no": self._no,
        }.get(i.name)
        if h is None:
            await self._unknown(i)
            return
        await h()

    async def _unknown(self, i: Intent) -> None:
        if self.shop.view == "menu" and self.shop.menu:
            await self.say("I couldn't find that on this menu. Try an item name, or say what they have.", listen=True)
        else:
            await self.say("I can find coffee, food, gas, chargers or a store nearby, and order for you. "
                           "What do you need?", listen=True)

    async def _yes(self) -> None:
        sh = self.shop
        if sh.awaiting == "drive_offer" and sh.order_place:
            sh.awaiting = ""
            await self.drive_to(sh.order_place, start=True)
        elif sh.awaiting == "clear_cart":
            sh.awaiting = ""
            await self.clear_cart()
        elif sh.view == "places" and sh.places:
            await self.open_place(index=0)
        elif sh.view == "item" and sh.item:
            await self.add_item(sh.item.id, sh.item_qty, sh.item_sel)
        elif sh.view in ("menu", "cart") and sh.cart and sh.cart.lines:
            await self.checkout()
        else:
            await self._unknown(Intent("unknown", "yes"))

    async def _no(self) -> None:
        sh = self.shop
        if sh.awaiting == "drive_offer":
            sh.awaiting = ""
            await self.say("Okay. Say take me there whenever you're ready.")
        elif sh.awaiting == "clear_cart":
            sh.awaiting = ""
            await self.say("Okay, I kept your cart.")
        elif sh.view == "item":
            await self.back()
        elif sh.view == "places":
            await self.say("Okay. Want something else nearby?", listen=True)
        else:
            await self.say("Okay.")

    # ================================================================== tabs
    def show_tab(self, tab: str) -> None:
        self.tab = tab if tab in ("home", "agent", "shop", "nav") else "home"
        self.publish()

    async def show_tab_spoken(self, tab: str) -> None:
        self.show_tab(tab)
        await self.say({"home": "Home.", "agent": "Here's the assistant.", "shop": "Shop.",
                        "nav": "Here's the map."}[self.tab])

    # ================================================================== discovery
    async def discover(self, category: str, query: str | None = None, *, speak: bool = True,
                       along_route: bool = False) -> list[Place]:
        sh = self.shop
        if sh.view == "checkout":
            await self.cancel_checkout(quiet=True)
        sh.category, sh.query, sh.searching = category, query, True
        sh.awaiting = ""
        sh.view, sh.place, sh.menu, sh.item = "places", None, None, None
        cat = CATEGORIES.get(category)
        sh.status = f"Searching {cat.many if cat else 'places'} near {self._area()}…"
        self.tab = "shop"
        self.publish()
        planned = along_route and self.nav.mode == "overview"
        route = self.nav.route if (self.nav.mode == "navigating" or planned) else None
        along = self.progress.along_m if (route and self.progress) else 0.0
        res = await self.discovery.nearby(category=category, query=query, position=self._pos(), route=route,
                                          along_m=along)
        sh.searching = False
        sh.places = res.places
        sh.seen = {p.merchant_id for p in res.places}
        n = len(res.places)
        what = (query or (cat.many if cat else "places")) if n != 1 else (query or (cat.one if cat else "place"))
        sh.status = (f"{n} {noun(category, n)} {'along your route' if res.along_route else 'near ' + self._area()}"
                     if n else f"No {what} found nearby")
        self._push_markers()
        self.publish()
        if not speak:
            return res.places
        if not n:
            await self.say(f"I couldn't find any {cat.many if cat else 'places'} open nearby.", listen=True)
            return res.places
        top = res.places[0]
        dist = self._dist_words(top)
        if top.extra.get("orderable"):
            extra = (f" They have {query}." if query and top.extra.get("matched_item") else "")
            where = "along your route" if res.along_route else "nearby"
            pick = "the quickest stop" if res.along_route else "closest"
            line = (f"{n} {noun(category, n)} {where}. {top.name} is {pick}, {dist}.{extra} "
                    f"Want to order from there?" if n > 1 else
                    f"{top.name} is {dist}.{extra} Want to order from there?")
        else:
            where = "along your route" if res.along_route else "nearby"
            pick = "the quickest stop" if res.along_route else "closest"
            line = (f"{n} {noun(category, n)} {where}. {top.name} is {pick}, {dist}. "
                    f"I can't order from there, but I can take you. Want directions?")
            sh.awaiting = ""
        await self.say(line, listen=True)
        return res.places

    async def more(self) -> None:
        sh = self.shop
        res = await self.discovery.nearby(category=sh.category or "food", query=sh.query, position=self._pos(),
                                          limit=self.s.merchants.max_results * 2)
        fresh = [p for p in res.places if p.merchant_id not in sh.seen]
        if not fresh:
            await self.say("That's everything open nearby.", listen=True)
            return
        sh.places = fresh[: self.s.merchants.max_results]
        sh.seen |= {p.merchant_id for p in sh.places}
        self._push_markers()
        self.publish()
        await self.say(f"Here are {len(sh.places)} more. {sh.places[0].name} is {self._dist_words(sh.places[0])}.",
                       listen=True)

    def _area(self) -> str:
        return (self.location_label or "you").split(",")[0]

    def _dist_words(self, p: Place) -> str:
        d = p.extra.get("distance_m") or haversine(self._pos(), p.location)
        mi = d / 1609.344
        if p.distance_along_route_m is not None and p.detour_s is not None:
            return f"adds {fmt.spoken_minutes(fmt.minutes(p.detour_s))}"
        if mi < 0.15:
            return "right here"
        return f"{mi:.1f} miles away"

    # ================================================================== menus
    def _place_for(self, *, index: int | None = None, place_id: str | None = None) -> Place | None:
        sh = self.shop
        pool = [*sh.places, *self.home_nearby, *([sh.cart_place] if sh.cart_place else []),
                *([sh.order_place] if sh.order_place else [])]
        if index is not None:
            return sh.places[index] if 0 <= index < len(sh.places) else None
        return next((p for p in pool if p and (p.id == place_id or p.merchant_id == place_id)), None)

    async def open_place(self, place: Place | None = None, *, index: int | None = None,
                         place_id: str | None = None, speak: bool = True) -> None:
        sh = self.shop
        place = place or self._place_for(index=index, place_id=place_id)
        if place is None:
            await self.say("Which one? Say the first one, or a name.", listen=True)
            return
        sh.awaiting = ""
        if not place.extra.get("has_menu", True) or not place.extra.get("orderable", True):
            sh.place = place
            self.publish()
            await self.say(f"I can't order from {place.name} from the car, but I can take you there. "
                           "Want directions?", listen=True)
            sh.awaiting = "drive_offer"
            sh.order_place = place
            return
        provider = self.registry.get(place.provider)
        menu = await provider.get_menu(place.merchant_id)
        sh.place, sh.menu, sh.item = place, menu, None
        sh.menu_category = menu.categories[0] if menu.categories else None
        sh.view = "menu"
        self.tab = "shop"
        self.publish()
        if not speak:
            return
        top = [it.name for it in menu.items if it.available][:3]
        hint = f" Popular: {' and '.join(speak_name(t) for t in top[:2])}." if top else ""
        await self.say(f"{place.name}.{hint} What would you like?", listen=True)

    def select_menu_category(self, name: str, *, speak: bool = False, ask: bool = False):
        sh = self.shop
        if sh.menu and name in sh.menu.categories:
            sh.menu_category = name
            if sh.view == "item":
                sh.view = "menu"
            self.publish()
            if speak:
                items = [it for it in sh.menu.items if it.category == name and it.available][:3]
                names = " or ".join(speak_name(it.name) for it in items[:3]) if ask else ", ".join(
                    speak_name(it.name) for it in items[:3])
                return self.say((f"{name}: {names}. Which one?" if ask else f"{name}: {names}."), listen=True)
        return asyncio.sleep(0)

    async def read_menu(self) -> None:
        sh = self.shop
        if not sh.menu:
            await self._unknown(Intent("unknown", ""))
            return
        cat = sh.menu_category
        items = [it for it in sh.menu.items if it.available and (not cat or it.category == cat)][:4]
        if not items:
            items = [it for it in sh.menu.items if it.available][:4]
        said = ", ".join(f"{speak_name(it.name)} {_money_words(it.base_price.amount)}" for it in items[:3])
        await self.say(f"{cat + ': ' if cat else ''}{said}. Say a name to add it.", listen=True)

    async def open_item(self, item_id: str, *, qty: int = 1, selections: dict | None = None,
                        speak: bool = True) -> None:
        sh = self.shop
        if not sh.menu or not sh.menu.item(item_id):
            return
        item = sh.menu.item(item_id)
        assert item
        sh.item, sh.item_qty = item, max(1, qty)
        sh.item_sel = normalise_selections(item, selections or {})
        sh.view = "item"
        self.publish()
        if speak:
            price = make_line("x", item, sh.item_sel, 1).unit_price
            opts = item.modifier_groups[0].options if item.modifier_groups else []
            choice = (" Options: " + ", ".join(o.name for o in opts[:3]) + ".") if len(opts) > 1 else ""
            await self.say(f"{speak_name(item.name)}, {_money_words(price.amount)}.{choice} Add it?", listen=True)

    def set_item_qty(self, qty: int):
        self.shop.item_qty = max(1, min(20, qty))
        self.publish()
        return asyncio.sleep(0)

    def set_item_option(self, selections: dict[str, list[str]]):
        sh = self.shop
        if sh.item:
            sh.item_sel = normalise_selections(sh.item, {**sh.item_sel, **selections})
            self.publish()
        return asyncio.sleep(0)

    # ================================================================== cart
    def _cart_locked(self) -> bool:
        """Cart edits void an open approval; once payment is under way they're refused."""
        co = self.shop.checkout
        if co is None:
            return False
        if co.stage == "processing":
            return True
        if co.stage in ("face", "voice"):
            self._cancel_verifier()
        self.shop.checkout = None
        self.phone_note = None
        return False

    async def _ask_quantity(self, items: list[dict[str, Any]]) -> bool:
        """An implausible quantity (a number that was really a price or an option) is confirmed, never added."""
        sh = self.shop
        for it in items:
            item = sh.menu.item(it["item_id"]) if sh.menu else None
            qty = int(it.get("qty", 1))
            if item is None or not (qty > MAX_SANE_QTY or (qty > 1 and is_prepay(item))):
                continue
            sh.awaiting, sh.pending = "confirm_qty", items
            self.publish()
            total = make_line("q", item, normalise_selections(item, it.get("selections") or {}), qty).total
            await self.say(f"That's {qty} of {speak_name(item.name)}, {_money_words(total.amount)} in all. "
                           "Is that right?", listen=True)
            return True
        return False

    async def _confirm_quantity(self) -> None:
        sh = self.shop
        pending, sh.awaiting, sh.pending = sh.pending, "", []
        await self.add_items(pending, confirmed=True)

    async def _cancel_quantity(self) -> None:
        sh = self.shop
        sh.awaiting, sh.pending = "", []
        await self.say("Okay, I didn't add it. How many would you like?", listen=True)

    async def add_items(self, items: list[dict[str, Any]], *, speak: bool = True, replace: bool = False,
                        confirmed: bool = False) -> None:
        sh = self.shop
        assert sh.menu and sh.place
        if self._cart_locked():
            return
        if not confirmed and await self._ask_quantity(items):
            return
        if sh.cart and sh.cart.lines and sh.cart.merchant_id != sh.place.merchant_id and not replace:
            sh.awaiting, sh.pending = "replace_cart", items
            self.publish()
            other = sh.cart_place.name if sh.cart_place else "another place"
            await self.say(f"Your cart has items from {other}. Start a new cart for {sh.place.name}?", listen=True)
            return
        if replace or not sh.cart or sh.cart.merchant_id != sh.place.merchant_id:
            sh.cart = Cart(merchant_id=sh.place.merchant_id)
            sh.cart_place = sh.place
        added = []
        for it in items:
            item = sh.menu.item(it["item_id"])
            if not item:
                continue
            if not item.available:
                await self.say(f"{speak_name(item.name)} needs options I can't choose from the car yet.", listen=True)
                return
            sel = normalise_selections(item, it.get("selections") or {})
            existing = next((ln for ln in sh.cart.lines if ln.item_id == item.id and ln.selections == sel), None)
            if existing:
                existing.quantity = min(20, existing.quantity + int(it.get("qty", 1)))
            else:
                sh.cart.lines.append(make_line(new_id("ln"), item, sel, int(it.get("qty", 1))))
            added.append((speak_name(item.name), int(it.get("qty", 1))))
        if not added:
            return
        sh.view = "menu"
        sh.item = None
        self.publish()
        names = " and ".join(f"{_QTY.get(q, q)} {n}" if q > 1 else n for n, q in added)
        self.notify(f"Added {names}")
        if speak:
            await self.say(f"Added {names}. That's {_money_words(sh.cart.subtotal.amount)} so far. "
                           "Anything else, or check out?", listen=True)

    async def add_item(self, item_id: str, qty: int = 1, selections: dict | None = None, *,
                       speak: bool = True, replace: bool = False) -> None:
        await self.add_items([{"item_id": item_id, "qty": qty, "selections": selections or {}}], speak=speak,
                             replace=replace)

    async def _replace_cart(self) -> None:
        sh = self.shop
        pending, sh.awaiting, sh.pending = sh.pending, "", []
        await self.add_items(pending, replace=True, confirmed=True)

    async def _keep_cart(self) -> None:
        sh = self.shop
        sh.awaiting, sh.pending = "", []
        await self.say("Okay, I kept your cart as it was.")

    def set_line_qty(self, line_id: str, qty: int):
        sh = self.shop
        if not sh.cart or self._cart_locked():
            return asyncio.sleep(0)
        if sh.view == "checkout":
            sh.view = "cart"
        if qty <= 0:
            sh.cart.lines = [ln for ln in sh.cart.lines if ln.line_id != line_id]
        else:
            for ln in sh.cart.lines:
                if ln.line_id == line_id:
                    ln.quantity = min(20, qty)
        if not sh.cart.lines:
            sh.cart, sh.cart_place = None, None
        self.publish()
        return asyncio.sleep(0)

    async def set_line_qty_by_name(self, name: str | None, qty: int) -> None:
        sh = self.shop
        if not sh.cart or not sh.cart.lines:
            return
        reopen = sh.checkout is not None
        line = next((ln for ln in sh.cart.lines if name and ln.name == name), sh.cart.lines[-1])
        await self.set_line_qty(line.line_id, qty)
        await self.say(f"{speak_name(line.name)}, {qty}." + (" Say check out to see the new total." if reopen else ""),
                       listen=True)

    async def remove_line(self, *, name: str | None = None, line_id: str | None = None) -> None:
        sh = self.shop
        if not sh.cart:
            return
        line = next((ln for ln in sh.cart.lines if ln.line_id == line_id or (name and ln.name == name)), None)
        if not line:
            return
        await self.set_line_qty(line.line_id, 0)
        await self.say(f"Removed {speak_name(line.name)}." + ("" if sh.cart else " Your cart is empty."), listen=True)

    async def view_cart(self, *, speak: bool = True) -> None:
        sh = self.shop
        sh.view = "cart"
        self.tab = "shop"
        self.publish()
        if not speak:
            return
        if not sh.cart or not sh.cart.lines:
            await self.say("Your cart is empty.", listen=True)
            return
        n = sh.cart.item_count
        await self.say(f"{n} item{'s' if n != 1 else ''}, {_money_words(sh.cart.subtotal.amount)} before tax. "
                       "Say check out when you're ready.", listen=True)

    async def clear_cart(self, *, speak: bool = True) -> None:
        sh = self.shop
        if self._cart_locked():
            return
        sh.cart, sh.cart_place = None, None
        if sh.view in ("cart", "checkout"):
            sh.view = "menu" if sh.menu else "places" if sh.places else "idle"
        self.publish()
        if speak:
            await self.say("Cart cleared.")

    async def back(self) -> None:
        sh = self.shop
        if self.tab != "shop":
            self.tab = "home"
        elif sh.view == "item":
            sh.view, sh.item = "menu", None
        elif sh.view == "checkout":
            await self.cancel_checkout(quiet=True)
        elif sh.view == "cart":
            sh.view = "menu" if sh.menu else "places" if sh.places else "idle"
        elif sh.view == "menu":
            sh.view, sh.menu, sh.place = ("places" if sh.places else "idle"), None, None
        elif sh.view == "order":
            sh.view = "places" if sh.places else "idle"
        self.publish()

    async def usual(self) -> None:
        u = self.occupant.usual
        if not u:
            await self.say("You haven't set a usual yet.", listen=True)
            return
        try:
            provider = self.registry.get("local")
            place = provider.place(u["merchant_id"])  # type: ignore[attr-defined]
        except Exception:
            await self.say("Your usual place isn't available right now.")
            return
        place.extra["distance_m"] = haversine(self._pos(), place.location)
        await self.open_place(place, speak=False)
        await self.add_item(u["item_id"], 1, u.get("selections") or {}, speak=False)
        await self.say(f"Your usual from {place.name} is in the cart. Check out?", listen=True)

    # ================================================================== checkout
    def _payer(self) -> Payer:
        return self.s.payer(self.payer_id)

    def _cart_signature(self) -> str:
        sh = self.shop
        lines = [(ln.item_id, repr(sorted((k, tuple(v)) for k, v in ln.selections.items())), ln.quantity)
                 for ln in (sh.cart.lines if sh.cart else [])]
        return repr((sh.cart_place.id if sh.cart_place else "", lines))

    async def checkout(self, *, source: str = "voice") -> None:  # noqa: C901 - one linear flow
        """Review the cart and open the approval sheet. A policy denial is explained once; while it stands the
        screen's repeat taps are ignored (voice gets the same sentence again, without a second review)."""
        from ..biometrics.f455 import FaceUserMap

        users = FaceUserMap(self.s.biometrics.home)
        selected = users.selected_id(self.s.biometrics.driver_id)
        if selected and users.role(selected) == "passenger":
            await self.say("The selected profile is a passenger. Select a driver before checkout.", listen=True)
            return
        if self.capture_busy is not None and self.capture_busy():
            await self.say("Finish or cancel enrollment before checkout.", listen=True)
            return
        sh = self.shop
        sh.awaiting = ""
        if sh.checkout is not None and sh.checkout.stage == "processing":
            return
        if not sh.cart or not sh.cart.lines or not sh.cart_place:
            await self.say("Your cart is empty. What would you like?", listen=True)
            return
        if sh.denied is not None and sh.denied[0] == self._cart_signature():
            if source != "screen":
                await self.say(sh.denied[1], listen=True)
            return
        sh.denied = None
        place = sh.cart_place
        provider = self.registry.get(place.provider)
        try:
            quote = await provider.quote(sh.cart, merchant_name=place.name)
        except OrderingUnavailable as e:
            await self.say(e.spoken, listen=True)
            sh.awaiting, sh.order_place = "drive_offer", place
            return
        eta = await self._eta_to(place)
        proposal = TransactionProposal(
            proposal_id=new_id("pp"), occupant_id=self.occupant.id, payer_id=self._payer().id,
            merchant_id=place.merchant_id, provider=place.provider, cart=sh.cart, quote_id=quote.quote_id,
            quote_hash=quote.content_hash(), stated_total=quote.total, pickup_eta=eta,
            customer_name=self.occupant.name)
        category = (place.extra.get("tags") or [place.category])[0]
        try:
            try:
                req = await self.authority.review(proposal, category=category, moving=self._moving())
            except QuoteMismatch:
                quote = await provider.quote(sh.cart, merchant_name=place.name)
                proposal = proposal.model_copy(update={"quote_id": quote.quote_id,
                                                       "quote_hash": quote.content_hash(),
                                                       "stated_total": quote.total})
                req = await self.authority.review(proposal, category=category, moving=self._moving())
        except PolicyDenied as e:
            log.info("checkout denied: %s", e)
            sh.denied = (self._cart_signature(), e.spoken)
            self.publish()
            self.notify(e.spoken)
            await self.say(e.spoken, listen=True)
            return
        methods = [m for m in self.s.auth.methods]
        allowed = [m for m in methods if m != "voice" or req.tier == AuthTier.T1_VOICE or self.voice_biometric]
        sh.checkout = Checkout(req=req, allowed=allowed)
        sh.view = "checkout"
        self.tab = "shop"
        self.publish()
        total = _money_words(req.quote.total.amount)
        if "voice" in allowed and self.voice_biometric:
            prompt = (f"That's {total} on your {self._payer().display}. Say use voice to verify it's you, "
                      "or use Face ID.")
        elif "voice" in allowed:
            prompt = f"That's {total} on your {self._payer().display}. Say confirm to place it, or use Face ID."
        else:
            prompt = (f"That's {total} on your {self._payer().display}. For this amount I need Face ID or a code. "
                      "Which would you like?")
        await self.say(prompt, listen=True)

    @property
    def voice_biometric(self) -> bool:
        """True when the payment verifier is the enrolled Voice ID (auth.face=auto resolved to it)."""
        f = self.face
        return getattr(f, "name", "") == "voice_id" and getattr(f, "enrolled", True)

    def _face_verifier(self):
        """What the Face ID tile runs: the F455 rung through the same DriveAuth decision as Voice ID, else the
        configured verifier."""
        if self.voice_biometric:
            f = FaceFirstVerifier.sharing(self.face)
            f.bind(self)
            return f
        return self.face

    def method_info(self) -> dict[str, dict[str, Any]]:
        """Per-method descriptor the HMI renders its tile subtitles from."""
        if self.voice_biometric:
            digits = self.s.biometrics.challenge_digits
            voice = {"mode": "biometric", "subtitle": f"Say {digits} digits"}
        else:
            voice = {"mode": "consent", "subtitle": 'Say "confirm"'}
        if self.voice_biometric or isinstance(self.face, FaceFirstVerifier):
            ok = rung_available(getattr(self.face, "rung", None))
        else:
            ok = not isinstance(self.face, NoFaceVerifier)
        face = {"available": ok, "subtitle": "Look at the camera" if ok else "Unavailable"}
        return {"voice": voice, "face": face, "code": {"available": True, "subtitle": "Code to your phone"}}

    async def _scan(self, co, verifier, *, look: bool) -> None:
        sh = self.shop
        self._verifier = verifier
        verify = asyncio.ensure_future(verifier.verify(self.s.auth.timeout_s))
        if look:
            await self.say("Look at the camera.")
        try:
            auth = await verify
        finally:
            if self._verifier is verifier:
                self._verifier = None
        if sh.checkout is not co:      # cancelled while scanning
            return
        if auth.success:
            await self.hold_face_result(("verified",))      # the green brackets and the driver's name
            if sh.checkout is not co:
                return
        co.bio = None
        human = getattr(verifier, "human_errors", False)
        if not auth.success:
            if human:
                co.error = auth.detail or "Couldn't verify you"
            else:
                co.error = "I couldn't see you clearly" if auth.detail == "timeout" else "Face didn't match"
            co.stage = "failed"
            co.attempts += 1
            self.publish()
            await self.say(f"{co.error}. Try again, or use a code.", listen=True)
            return
        await self._authorised(auth)

    async def _run_scan(self, co, verifier, *, look: bool, under_lock: bool) -> None:
        if under_lock and self._lock.locked():
            self._lock.release()              # a spoken "cancel" must not queue behind the scan
            try:
                await self._scan(co, verifier, look=look)
            finally:
                await self._lock.acquire()
        else:
            await self._scan(co, verifier, look=look)

    async def choose_auth(self, method: str, *, resend: bool = False, under_lock: bool = False) -> None:
        if self.capture_busy is not None and self.capture_busy():
            await self.say("Finish or cancel enrollment before choosing an approval method.", listen=True)
            return
        sh = self.shop
        co = sh.checkout
        if not co or co.stage == "processing":
            return
        if method in ("voice", "face") and co.otp_waiter is not None and not co.otp_waiter.done():
            await self.say(OTP_REPROMPT, listen=True)      # a scan is waiting for its code: no second scan
            return
        if method == "voice":
            if "voice" not in co.allowed:
                await self.say("Voice approval isn't allowed for this amount. Use Face ID or a code.", listen=True)
                return
            if not self.voice_biometric:
                await self.approve_voice()
                return
            if self._verifier is not None:
                return                        # a scan is already running
            co.method, co.error = "voice", ""
            co.stage, co.bio = "voice", None
            self.preview.reset()
            self.publish()
            await self._run_scan(co, self.face, look=False, under_lock=under_lock)
            return
        if method not in co.allowed:
            await self.say("That method isn't available in this car.", listen=True)
            return
        if method == "face":
            if self._verifier is not None:
                return
            co.method, co.error = "face", ""
            co.stage, co.bio = "face", None
            self.preview.begin_scan()
            self.publish()
            await self._run_scan(co, self._face_verifier(), look=True, under_lock=under_lock)
            return
        co.method, co.error = method, ""
        self.preview.reset()
        if method == "otp":
            phone = self.occupant.phone or "+10000000000"
            co.otp = await self.otp.issue(phone, f"{_money_words(co.req.quote.total.amount)} at "
                                                 f"{co.req.quote.merchant_name}")
            co.stage, co.otp_entry = "otp", ""
            self.publish()
            await self.say(("I sent a new code" if resend else "I sent a code") +
                           f" to your phone ending {phone[-4:]}. Read me the {co.otp.length} digits.", listen=True)

    async def otp_reprompt(self, heard: int, need: int) -> None:
        """A spoken code with the wrong number of digits: ask again; no attempt is used."""
        plural = "digit" if heard == 1 else "digits"
        await self.say(f"I heard {heard} {plural} and the code has {need}. Say all {need} again, or type it.",
                       listen=True)

    def otp_key(self, key: str):
        """On-screen keypad."""
        co = self.shop.checkout
        if not co or co.stage != "otp" or not co.otp:
            return asyncio.sleep(0)
        if key == "del":
            co.otp_entry = co.otp_entry[:-1]
        elif key.isdigit() and len(co.otp_entry) < co.otp.length:
            co.otp_entry += key
        self.publish()
        if co.otp and len(co.otp_entry) == co.otp.length:
            return self.submit_otp(co.otp_entry)
        return asyncio.sleep(0)

    async def submit_otp(self, code: str) -> None:
        co = self.shop.checkout
        if not co or not co.otp or co.stage == "processing":
            return
        co.otp_entry = code
        self.publish()
        res = self.otp.verify(co.otp.challenge_id, code)
        waiter = co.otp_waiter
        if res.success:
            self.phone_note = None
            if waiter is not None and not waiter.done():
                waiter.set_result(True)      # biometric step-up: DriveAuth makes the final decision
                return
            await self._authorised(res)
            return
        co.otp_entry = ""
        if waiter is not None and not waiter.done() and res.detail in ("locked", "expired"):
            waiter.set_result(False)
            self.phone_note = None
            return
        if res.detail in ("locked", "expired"):
            co.stage, co.error = "failed", "Code expired" if res.detail == "expired" else "Too many tries"
            self.publish()
            await self.say(f"{co.error}. Say send a new code, or use Face ID.", listen=True)
        else:
            left = res.detail.split(":")[-1]
            tries = f"{left} {'try' if left == '1' else 'tries'} left"
            co.error = f"That code didn't match — {tries}"
            self.publish()
            await self.say(f"That code didn't match. {tries.capitalize()}. Read it again, or type it.", listen=True)

    async def approve_voice(self, *, under_lock: bool = False) -> None:
        co = self.shop.checkout
        if not co or co.stage == "processing":
            return
        if self.otp_pending():                # a code is waiting: "approve" must neither rescan nor consent
            await self.say(OTP_REPROMPT, listen=True)
            return
        if "voice" not in co.allowed:
            await self.say("For this amount I need Face ID or a code. Which would you like?", listen=True)
            return
        if self.voice_biometric:      # a bare "confirm" is not an approval when Voice ID is on: verify the speaker
            await self.choose_auth("voice", under_lock=under_lock)
            return
        co.method = "voice"
        await self._authorised(voice_consent())

    async def _authorised(self, auth: AuthResult) -> None:
        sh = self.shop
        co = sh.checkout
        if not co or co.stage == "processing":   # one approval -> one payment, however many taps/words
            return
        co.stage = "processing"
        self.publish()
        try:
            tx = await self.orch.execute(co.req, voice_confirmed=True, auth=auth)
        except AuthenticationFailed as e:
            co.stage, co.error = "failed", "Not verified"
            self.publish()
            await self.say(e.spoken, listen=True)
            return
        except DriveOSError as e:
            co.stage, co.error = "failed", e.spoken
            self.publish()
            await self.say(e.spoken, listen=True)
            return
        place = sh.cart_place
        assert place is not None
        self.active[tx.tx_id] = {"tx": tx, "place": place, "arrived": False, "since": None}
        sh.tx, sh.order_place, sh.arrived = tx, place, False
        sh.checkout, sh.cart, sh.cart_place = None, None, None
        self.preview.reset()
        sh.view = "order"
        sh.awaiting = "drive_offer"
        self.phone_note = None
        self.publish()
        ready = fmt.minutes(((tx.ready_at or utcnow()) - utcnow()).total_seconds())
        kind = place.pickup_modes[0] if place and place.pickup_modes else PickupMode.WALK_IN
        if kind == PickupMode.PUMP:
            msg = f"Done. {tx.order.pickup_spot if tx.order else 'Your pump'} is reserved."
        elif kind == PickupMode.CHARGER:
            msg = f"Done. {tx.order.pickup_spot if tx.order else 'A stall'} is held for you."
        else:
            msg = f"Order placed. Ready in about {fmt.spoken_minutes(ready)}."
        await self.say(msg + " Want me to take you there?", listen=True)

    async def cancel_checkout(self, *, quiet: bool = False) -> None:
        sh = self.shop
        if sh.checkout and sh.checkout.stage == "processing":
            if not quiet:
                await self.say("It's already being placed. You can cancel the order in a moment.")
            return
        if sh.checkout:
            if sh.checkout.stage in ("face", "voice", "otp"):
                self._cancel_verifier()
            if sh.checkout.otp_waiter is not None and not sh.checkout.otp_waiter.done():
                sh.checkout.otp_waiter.set_result(False)
            sh.checkout = None
        self.bio_capture = False
        self.phone_note = None
        self.preview.reset()
        sh.view = "cart" if sh.cart else "menu" if sh.menu else "idle"
        self.publish()
        if not quiet:
            await self.say("Okay, I didn't place it.")

    def _cancel_verifier(self) -> None:
        (self._verifier or self.face).cancel()

    def face_report(self, match: bool):
        """Simulated Face ID: the UI's scan finished (or the user tapped 'not me')."""
        if isinstance(self.face, SimulatedFaceVerifier):
            self.face.report(match)
        return asyncio.sleep(0)

    # ================================================================== orders
    async def _on_tx(self, tx: Transaction) -> None:
        sh = self.shop
        o = self.active.get(tx.tx_id)
        if o is None:
            return
        prev = o.get("status")
        if self.brain is not None and hasattr(self.brain, "note_tx"):
            self.brain.note_tx(tx)
        o["tx"], o["status"] = tx, tx.order_status
        place = o["place"]
        if sh.tx is not None and sh.tx.tx_id == tx.tx_id:
            sh.tx = tx
        if tx.terminal:
            self.active.pop(tx.tx_id, None)
        if tx.state in ("voided", "refunded", "declined", "failed"):
            msg = {"voided": "The order didn't go through, so I released the hold. Nothing was charged.",
                   "refunded": "Your order was canceled and refunded.",
                   "declined": "Your card was declined. Nothing was charged.",
                   "failed": "Something went wrong with the order. I'll make sure nothing is charged."}[tx.state]
            if tx.message == "customer_cancel":
                msg = "Canceled. " + ("Refund on its way." if tx.state == "refunded" else "Nothing was charged.")
            self.publish()
            await self.say(msg)
        elif tx.state == "completed":
            self.publish()
            name = tx.quote.merchant_name
            sh.awaiting = ""
            if self.nav.mode == "arrived" and self.nav.dest_kind == "order":
                self.nav.mode = "idle"
            await self.say(f"All done at {name}. Enjoy!")
        elif tx.order_status == OrderStatus.READY and prev != OrderStatus.READY and not o["arrived"]:
            self.publish()
            kind = place.pickup_modes[0] if place.pickup_modes else None
            if kind not in (PickupMode.PUMP, PickupMode.CHARGER):
                await self.say(f"Your order at {tx.quote.merchant_name} is ready.")
        else:
            self.publish()

    async def order_status(self) -> None:
        tx = self.shop.tx
        if not tx or tx.terminal:
            await self.say("You don't have an order in progress.")
            return
        self.tab, self.shop.view = "shop", "order"
        self.publish()
        eta = None
        if self.nav.mode == "navigating" and self.progress:
            eta = self.progress.remaining_s
        arrive = f" You're {fmt.spoken_minutes(fmt.minutes(eta))} away." if eta else ""
        st = tx.order_status
        if st == OrderStatus.READY:
            await self.say("It's ready for you." + arrive)
        elif tx.ready_at:
            m = fmt.minutes((tx.ready_at - utcnow()).total_seconds())
            await self.say(f"{tx.quote.merchant_name} is on it — ready in about {fmt.spoken_minutes(m)}." + arrive)
        else:
            await self.say(f"{tx.quote.merchant_name} has your order." + arrive)

    async def cancel_order(self) -> None:
        tx = self.shop.tx
        if not tx or tx.terminal:
            await self.say("There's no order to cancel.")
            return
        if not self.orch.can_cancel(tx):
            await self.say(f"{tx.quote.merchant_name} has already started on it, so it can't be canceled now.")
            return
        if not await self.orch.cancel(tx.tx_id):
            await self.say("I couldn't cancel it with the merchant. It's still on.")

    async def update_eta(self, minutes: int) -> None:
        tx = self.shop.tx
        if not tx or tx.terminal or not tx.order:
            await self.say("You don't have an order in progress.")
            return
        if tx.order_status == OrderStatus.READY:
            await self.say(f"It's already ready at {tx.quote.merchant_name} — they'll hold it for you.")
            return
        provider = self.registry.get(tx.quote.provider)
        upd = getattr(provider, "update_eta", None)
        eta = utcnow() + timedelta(minutes=max(1, minutes))
        if upd:
            await upd(tx.order, eta)
            tx.ready_at = eta
        self.publish()
        await self.say(f"I told {tx.quote.merchant_name} you'll be there in {fmt.spoken_minutes(minutes)}.")

    # ================================================================== navigation
    async def _route(self, dest: GeoPoint, name: str, *, via: list[GeoPoint] | None = None,
                     via_names: list[str] | None = None, fresh: bool = False) -> Route | None:
        try:
            return await self.router.route(self._pos(), dest, via=via or None, destination_name=name,
                                           via_names=via_names or None, fresh=fresh)
        except DriveOSError as e:
            log.error("routing failed: %s", e)
            return None

    async def _eta_to(self, place: Place) -> Any:
        r = await self._route(place.location, place.name)
        return utcnow() + timedelta(seconds=r.duration_s) if r else None

    async def drive_to(self, place: Place, *, start: bool = True, kind: str | None = None,
                       quiet: bool = False) -> None:
        same_destination = self.nav.dest is not None and self.nav.dest == place.location
        if start and same_destination and self.nav.route and self.nav.mode in ("overview", "navigating"):
            if self.nav.mode == "overview":
                await self.start_nav(speak=True)
            else:
                self.tab = "nav"
                self.publish()
            return
        r = await self._route(place.location, place.name)
        if not r:
            await self.say("I couldn't find a route there.")
            return
        is_order = bool(self.shop.tx and not self.shop.tx.terminal and
                        self.shop.tx.quote.merchant_id == place.merchant_id)
        self.nav = Nav(mode="overview", dest_name=place.name, dest=place.location,
                       dest_kind=kind or ("order" if is_order else "place"), dest_place=place, route=r,
                       estimated=r.summary == "estimated")
        self._set_route(r)
        self.shop.awaiting = ""
        if start:
            await self.start_nav(speak=True)
        elif quiet:
            self.tab = "nav"
            self.publish()
        else:
            self.tab = "nav"
            self.publish()
            await self.say(f"{place.name} is {fmt.spoken_minutes(fmt.minutes(r.duration_s))} away. "
                           "Say start to go.", listen=True)

    async def drive_here(self) -> None:
        sh = self.shop
        place = (sh.order_place if sh.tx and not sh.tx.terminal else None) or sh.place or sh.cart_place or (
            sh.places[0] if sh.places else None)
        if self.nav.mode == "overview" and self.nav.route:
            await self.start_nav(speak=True)
            return
        if not place:
            await self.say("Where to? Say a place, or find something first.", listen=True)
            return
        await self.drive_to(place, start=True)

    async def navigate_saved(self, place_id: str) -> None:
        sp = self.s.place(place_id)
        if not sp:
            return
        r = await self._route(sp.location, sp.name)
        if not r:
            await self.say("I couldn't find a route there.")
            return
        self.nav = Nav(mode="overview", dest_name=sp.name, dest=sp.location, dest_kind="saved", route=r,
                       estimated=r.summary == "estimated")
        self._set_route(r)
        await self.start_nav(speak=True)

    async def navigate_to_point(self, point: GeoPoint, name: str, *, start: bool = True) -> None:
        place = Place(id=f"pt:{name}", merchant_id=f"pt:{name}", provider="none", name=name, location=point,
                      extra={"orderable": False, "has_menu": False})
        await self.drive_to(place, start=start, kind="place")

    def _set_route(self, r: Route) -> None:
        nv = self.s.nav
        self.tracker = RouteTracker(r, off_route_m=nv.off_route_m, off_route_fixes=nv.off_route_fixes)
        self.progress = self.tracker.update(self._pos(), self.vstate.speed_mps if self.vstate else 0.0)
        self._cues = CueTracker(r, self.progress.along_m)
        if self.sink:
            coords = [[p.lon, p.lat] for p in r.geometry]
            lo = [min(c[0] for c in coords), min(c[1] for c in coords)]
            hi = [max(c[0] for c in coords), max(c[1] for c in coords)]
            self.sink.route(coords, [*lo, *hi])
        self._push_markers()

    async def start_nav(self, *, speak: bool = False) -> None:
        if not self.nav.route:
            await self.say("Pick a destination first.", listen=True)
            return
        self.nav.mode = "navigating"
        self.tab = "nav"
        self._arrival_since = None
        self._gate = RerouteGate(self.s.nav.reroute_cooldown_s, count=self.nav.reroutes)
        self._eta.reset()
        if self.progress:
            self._cues = CueTracker(self.nav.route, self.progress.along_m)
        self.vehicle.follow(self.nav.route)
        resume = getattr(self.vehicle, "resume", None)
        if resume:
            resume()
        if self.sink:
            self.sink.camera("follow")
        self.publish()
        if speak:
            secs = self.progress.remaining_s if self.progress else self.nav.route.duration_s
            mins = fmt.minutes(secs)
            arrive = fmt.clock(utcnow() + timedelta(seconds=secs))
            await self.say(f"Heading to {self.nav.dest_name}. {fmt.spoken_minutes(mins).capitalize()}, "
                           f"arriving {arrive}.")

    async def stop_nav(self) -> None:
        if self.nav.mode in ("navigating", "overview"):
            hold = getattr(self.vehicle, "hold", None)
            if hold:
                hold()
            self.vehicle.set_gear("P")
            self._eta.reset()
            self.nav.mode = "overview" if self.nav.route else "idle"
            self.publish()
            await self.say("Guidance stopped.")
        else:
            await self.say("You're not navigating anywhere.")

    def clear_nav(self) -> None:
        self.nav = Nav()
        self.tracker, self.progress, self._cues = None, None, None
        if self.sink:
            self.sink.route([], None)
        self._push_markers()
        self.publish()

    # ================================================================== live GPS
    @property
    def vehicle_mode(self) -> str:
        return getattr(self.vehicle, "mode", "sim")

    def gps_info(self) -> dict[str, Any]:
        gps = getattr(self.vehicle, "gps", None)
        mode = self.vehicle_mode
        if gps is None:
            return {"mode": "sim", "status": "off", "label": "Simulated", "source": "", "available": False,
                    "accuracy": "", "error": ""}
        acc = gps.accuracy_m
        acc_s = (f"±{acc / 1000:.0f} km" if acc and acc >= 1000 else f"±{acc:.0f} m" if acc else "")
        if mode != "gps":
            label = "Simulated"
        else:
            label = {"fix": f"GPS {acc_s}".strip(), "searching": "GPS · searching", "lost": "GPS · signal lost",
                     "unavailable": "GPS unavailable", "off": "GPS off"}.get(gps.status, "GPS")
        return {"mode": mode, "status": gps.status if mode == "gps" else "off", "label": label,
                "source": gps.label or gps.spec, "available": True, "accuracy": acc_s, "error": gps.error}

    async def set_vehicle_mode(self, mode: str) -> None:
        """Nav screen switch: drive the simulated car, or follow the real position from the GPS."""
        setter = getattr(self.vehicle, "set_mode", None)
        if setter is None:
            self.notify("Live GPS isn't available in this build")
            return
        if mode == self.vehicle_mode:
            return
        if self.nav.mode == "navigating":
            hold = getattr(self.vehicle, "hold", None)
            if hold:
                hold()
        await setter(mode)
        if mode == "gps":
            self._label_anchor = None
            self.notify("Live GPS · finding your position…")
        else:
            self.notify("Simulating the drive from here")
            if self.nav.mode == "navigating" and self.nav.route:
                self.vehicle.follow(self.nav.route)
                resume = getattr(self.vehicle, "resume", None)
                if resume:
                    resume()
        self.publish()

    async def vehicle_mode_spoken(self, mode: str) -> None:
        if getattr(self.vehicle, "set_mode", None) is None:
            await self.say("Live GPS isn't available here.")
            return
        await self.set_vehicle_mode(mode)
        await self.say("Using live GPS. I'll follow your real position." if mode == "gps"
                       else "Simulation on. I'll drive the route for you.")

    def _on_gps_status(self, status: str) -> None:
        if self.vehicle_mode != "gps":
            return
        if status == "lost":
            self.notify("GPS signal lost — holding the last position")
        elif status == "unavailable":
            gps = getattr(self.vehicle, "gps", None)
            self.notify(f"GPS unavailable: {gps.error if gps else ''}"[:90])
        self.publish()

    def _via_left(self) -> tuple[list[GeoPoint], list[str]]:
        """Stops added to this trip that the car has not passed yet."""
        n = self.nav
        along = self.progress.along_m if self.progress else 0.0
        keep = [(p, nm) for p, nm in zip(n.via, n.via_names, strict=False)
                if self.tracker is None or self.tracker.distance_to(p, along) > -30.0]
        n.via, n.via_names = [p for p, _ in keep], [nm for _, nm in keep]
        return list(n.via), list(n.via_names)

    def _refollow(self, r: Route) -> None:
        """Point the simulated car at a re-planned route; a no-op for live GPS."""
        self.vehicle.follow(r)

    async def _reroute(self) -> None:
        """The car left the route: plan again from where it is, fresh (bypassing the origin cache)."""
        n = self.nav
        done = False
        try:
            if n.mode != "navigating" or not n.dest:
                return
            via, via_names = self._via_left()
            r = await self._route(n.dest, n.dest_name, via=via, via_names=via_names, fresh=True)
            if r and self.nav is n and n.mode == "navigating":
                n.route, n.route_reason = r, "reroute"
                n.estimated = r.summary == "estimated"
                self._set_route(r)
                self._refollow(r)
                n.reroutes = self._gate.count
                n.rerouted_at = time.monotonic()
                self._eta.reset(self._clock())
                self.notify("Rerouting…")
                log.info("rerouted (%d this trip)", n.reroutes)
                done = True
        finally:
            if not done:
                self._gate.count -= 1
            self._gate.done(self._clock())
        self.publish()
        await self.say("Rerouting.", transcript=False)

    async def _refresh_eta(self) -> None:
        """Re-request the route from where the car is so the ETA follows traffic (silent; not a reroute)."""
        n = self.nav
        old = n.route
        try:
            if n.mode != "navigating" or not n.dest:
                return
            via, via_names = self._via_left()
            r = await self._route(n.dest, n.dest_name, via=via, via_names=via_names, fresh=True)
            if r and self.nav is n and n.mode == "navigating" and n.route is old:
                n.route, n.route_reason = r, "refresh"
                n.estimated = r.summary == "estimated"
                self._set_route(r)
                self._refollow(r)
                self.publish()
        finally:
            self._eta_busy = False

    # ------------------------------------------------------------------ spoken turn guidance
    def _cue_blocked(self) -> bool:
        """Turn guidance never talks over a payment or biometric prompt, nor while Nova is speaking or the driver
        is mid-turn. A wake-once conversation keeps the mic open between turns, so there only an utterance blocks."""
        sh = self.shop
        v = self.voice
        listening = v.user_speaking if v.continuous else v.mode == "listening"
        return bool(
            sh.checkout is not None or sh.view == "checkout" or sh.awaiting in ("replace_cart", "clear_cart", "confirm_qty")
            or self.bio_capture or (self.capture_busy is not None and self.capture_busy())
            or listening or v.mode in ("processing", "speaking") or self._pending_speech > 0
            or self._lock.locked())

    def _speak_guidance(self, p: Progress, now: float) -> None:
        if self._cues is None:
            return
        text = self._cues.poll(p.along_m, now, blocked=self._cue_blocked())
        if text:
            self._bg(self.say(text, transcript=False))

    # ------------------------------------------------------------------ navigate to X / along the route / stops
    async def _resolve_destination(self, query: str) -> tuple[GeoPoint, str, Place | None] | None:
        """Free text -> a point: saved places, Mapbox Search Box, then the local catalogue when offline."""
        q = query.strip()
        low = q.lower()
        for sp in self.s.places:
            if low in (sp.name.lower(), *[a.lower() for a in sp.aliases]):
                return sp.location, sp.name, None
        client = getattr(self.router, "client", None)
        if client is not None:
            try:
                hits = await client.forward(q, proximity=self._pos(), limit=3)
                if hits:
                    h = hits[0]
                    return h["location"], h.get("name") or h.get("address") or "your destination", None
            except DriveOSError as e:
                log.warning("destination search failed: %s", e.code)
            except Exception:
                log.exception("destination search failed")
        return await self._catalogue_destination(low)

    async def _catalogue_destination(self, low: str) -> tuple[GeoPoint, str, Place | None] | None:
        from rapidfuzz import fuzz

        words = [w for w in low.replace(",", " ").split() if len(w) > 2 and w not in ("the", "and")]
        if not words:
            return None
        found = await asyncio.gather(*(self.registry.search(near=self._pos(), route=None, category=cid, query=None,
                                                           limit=30) for cid in CATEGORIES), return_exceptions=True)
        best: tuple[float, Place] | None = None
        for batch in found:
            for pl in batch if isinstance(batch, list) else []:
                name = pl.name.lower()
                score = max(fuzz.token_sort_ratio(low, name), 100.0 if all(w in name for w in words) else 0.0)
                if score >= 80 and (best is None or score > best[0]):
                    best = (score, pl)
        return (best[1].location, best[1].name, best[1]) if best else None

    async def navigate_to_query(self, query: str, *, start: bool = True) -> None:
        hit = await self._resolve_destination(query)
        if hit is None:
            await self.say("I couldn't find that place. Try its name with the town, or a street address.",
                           listen=True)
            return
        point, name, place = hit
        if place is not None:
            await self.drive_to(place, start=start)
        else:
            await self.navigate_to_point(point, name, start=start)

    async def find_along_route(self, category: str, query: str | None = None, *, destination: str | None = None,
                               speak: bool = True) -> list[Place]:
        """Search the route (the active one, or the one to `destination`); with no route, a normal nearby search."""
        if destination:
            hit = await self._resolve_destination(destination)
            if hit is None:
                if speak:
                    await self.say("I couldn't find that destination.", listen=True)
                return []
            point, name, place = hit
            same = self.nav.route is not None and self.nav.dest is not None and haversine(self.nav.dest, point) < 150
            if not same:
                if place is None:
                    place = Place(id=f"pt:{name}", merchant_id=f"pt:{name}", provider="none", name=name,
                                  location=point, extra={"orderable": False, "has_menu": False})
                await self.drive_to(place, start=False, kind="place", quiet=True)
        usable = self.nav.mode in ("navigating", "overview") and self.nav.route is not None
        return await self.discover(category, query, speak=speak, along_route=usable)

    async def add_stop(self, place: Place | None = None, *, index: int | None = None, speak: bool = True) -> bool:
        """Add a found place as a stop on the current route (re-planned through it, in route order)."""
        if place is None:
            place = self._place_for(index=index) if index is not None else None
            if place is None:
                if speak:
                    await self.say("Which one? Say the first one, or a name.", listen=True)
                return False
        n = self.nav
        if n.mode not in ("navigating", "overview") or not n.route or not n.dest:
            if speak:
                await self.say("Start a route first, then I can add a stop.")
            return False
        if any(haversine(p, place.location) < 40 for p in n.via):
            if speak:
                await self.say(f"{place.name} is already a stop.")
            return False
        route = n.route
        stops = sorted([*zip(n.via, n.via_names, strict=False), (place.location, place.name)],
                       key=lambda t: project(route, t[0]).along_m)
        pts, names = [p for p, _ in stops], [nm for _, nm in stops]
        r = await self._route(n.dest, n.dest_name, via=pts, via_names=names, fresh=True)
        if r is None:
            along = self.progress.along_m if self.progress else 0.0
            r, _added = splice_stop(route, place.location, from_along_m=along)
        n.route, n.via, n.via_names, n.route_reason = r, pts, names, "reroute"
        n.estimated = r.summary == "estimated"
        self._set_route(r)
        if n.mode == "navigating":
            self._refollow(r)
            self._eta.reset(self._clock())
        self.publish()
        if speak:
            secs = self.progress.remaining_s if self.progress else r.duration_s
            await self.say(f"Added {place.name} as a stop. {fmt.spoken_minutes(fmt.minutes(secs)).capitalize()} "
                           f"to {n.dest_name} now.")
        return True

    def _maybe_relocate(self, p: GeoPoint) -> None:
        """First fix, or a big move: re-label the location and refresh what's nearby."""
        if self._relabel_pending:
            return
        if self._label_anchor is not None and haversine(self._label_anchor, p) < 3000:
            return
        self._relabel_pending = True
        self._bg(self._relocate(p))

    async def _relocate(self, p: GeoPoint) -> None:
        try:
            first = self._label_anchor is None
            self._label_anchor = p
            label = None
            if self.geocoder is not None:
                try:
                    label = await self.geocoder(p)
                except Exception as e:
                    log.info("reverse geocode failed: %s", e)
            self.location_label = label or f"{p.lat:.4f}, {p.lon:.4f}"
            if self.weather_poke is not None:
                self.weather_poke.set()
            await self._prefetch_nearby()
            if first:
                self.notify(f"Live GPS · {self.location_label.split(',')[0]}")
        finally:
            self._relabel_pending = False

    async def set_location(self, point: GeoPoint, label: str) -> None:
        """Location picker: jump the (simulated) car somewhere else."""
        if self.vehicle_mode == "gps":
            await self.set_vehicle_mode("sim")
        self._label_anchor = point
        teleport = getattr(self.vehicle, "place_at", None)
        if teleport:
            teleport(point)
        self.location_label = label
        self.shop = Shop(tx=self.shop.tx, order_place=self.shop.order_place)
        self.clear_nav()
        await self._prefetch_nearby()

    # ================================================================== map markers
    def _push_markers(self) -> None:
        if not self.sink:
            return
        ms: list[dict[str, Any]] = []
        for i, p in enumerate(self.shop.places[:8]):
            ms.append({"id": p.id, "lon": p.location.lon, "lat": p.location.lat, "label": chr(65 + i),
                       "name": p.name, "kind": "option"})
        if self.nav.dest and self.nav.mode != "idle":
            ms.append({"id": "dest", "lon": self.nav.dest.lon, "lat": self.nav.dest.lat, "label": "",
                       "name": self.nav.dest_name, "kind": "dest"})
        self.sink.markers(ms)

    # ================================================================== help
    async def help(self) -> None:
        self.doc = (
            "## What you can say\n"
            "- **Find** — *\"find me a coffee\"*, *\"I need gas\"*, *\"any sandwich places nearby?\"*, "
            "*\"where can I charge?\"*, *\"get me some ibuprofen\"*\n"
            "- **Choose** — *\"the first one\"*, *\"Plur Coffee\"*, *\"show me more\"*\n"
            "- **Order** — *\"add a caramel latte\"*, *\"two cold brews\"*, *\"what do they have?\"*\n"
            "- **Cart** — *\"what's in my cart\"*, *\"remove the cookie\"*, *\"check out\"*\n"
            "- **Approve** — *\"confirm\"* (small amounts), *\"use Face ID\"*, *\"send me a code\"*\n"
            "- **After ordering** — *\"take me there\"*, *\"where's my order?\"*, "
            "*\"I'll be there in 10 minutes\"*, *\"cancel my order\"*\n"
            "- **Drive** — *\"take me home\"*, *\"stop navigation\"*, *\"show the map\"*\n")
        self.tab = "agent"
        self.publish()
        await self.say("Here's what you can ask. Try: find me a coffee.", listen=True)

    # ================================================================== publish
    def publish(self) -> None:
        if not self.sink:
            return
        from ..viewmodel import build_view

        try:
            self.sink.ui(build_view(self))
        except Exception:
            log.exception("view build failed")


def speak_name(name: str) -> str:
    """Menu names as a person would say them: '6" The Original Sandwich (Veg)' -> 'six-inch Original Sandwich'."""
    import re

    n = re.sub(r"\(.*?\)", "", name)
    n = re.sub(r"(\d+)\s*(?:\"|''|inch(?:es)?)", lambda m: f"{_NUM.get(m.group(1), m.group(1))}-inch", n)
    n = re.sub(r"^\s*(the)\s+", "", n, flags=re.I)
    n = re.sub(r"(-inch)\s+the\s+", r"\1 ", n, flags=re.I)
    n = re.sub(r"[®™*/]", " ", n)
    return re.sub(r"\s{2,}", " ", n).strip()


MAX_SANE_QTY = 20
_QTY = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
_NUM = {"6": "six", "8": "eight", "10": "ten", "12": "twelve", "16": "sixteen", "4": "four"}


def _spell(code: str) -> str:
    return " ".join(code) if code else ""


def _money_words(cents: int) -> str:
    d, c = divmod(int(cents), 100)
    if c == 0:
        return f"{d} dollars" if d != 1 else "1 dollar"
    return f"${d}.{c:02d}"
