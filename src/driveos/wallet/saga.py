"""Order saga: authorise → place → accept → capture → fulfil, with a compensating action for every exit.

Every transition is persisted (transactions table + signed ledger entry) before the next step runs, and every
external call carries a deterministic idempotency key, so a crash or retry can resume without ever
double-ordering or double-charging. On start-up, `recover()` finishes or compensates open transactions.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ..config import Settings
from ..core.ids import idempotency_key, utcnow
from ..domain.errors import DriveOSError, MerchantRejected, PaymentDeclined, ProviderUnavailable
from ..domain.models import (
    AuthTier,
    OrderStatus,
    OrderUpdate,
    PaymentAuthorization,
    PlacedOrder,
    Quote,
    TransactionProposal,
)
from ..payments.base import PaymentProvider
from .auth import AuthResult
from .authority import ConsentRequest, ProviderLookup, TransactionAuthority
from .ledger import TERMINAL_STATES, Ledger

log = logging.getLogger("driveos.saga")

PLACE_TIMEOUT_S = 25.0
ACCEPT_TIMEOUT_S = 90.0


class Transaction(BaseModel):
    tx_id: str
    state: str = "proposed"
    tier: AuthTier = AuthTier.T2_STRONG
    proposal: TransactionProposal
    quote: Quote
    consent_id: str | None = None
    payment: PaymentAuthorization | None = None
    order: PlacedOrder | None = None
    order_status: OrderStatus | None = None
    ready_at: datetime | None = None
    placed_at: datetime | None = None
    message: str = ""
    history: list[tuple[str, str]] = Field(default_factory=list)  # (iso time, state)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


Listener = Callable[[Transaction], Awaitable[None] | None]


class OrderOrchestrator:
    def __init__(self, settings: Settings, ledger: Ledger, authority: TransactionAuthority,
                 merchants: ProviderLookup, payments: PaymentProvider):
        self.settings = settings
        self.ledger = ledger
        self.authority = authority
        self.merchants = merchants
        self.payments = payments
        self._listeners: list[Listener] = []
        self._watchers: dict[str, asyncio.Task] = {}
        self.transactions: dict[str, Transaction] = {}

    # ---------------------------------------------------------------- observers
    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    async def _emit(self, tx: Transaction) -> None:
        for fn in list(self._listeners):
            try:
                r = fn(tx)
                if asyncio.iscoroutine(r):
                    await r
            except Exception:  # a UI listener must never break the saga
                log.exception("listener failed")

    async def _transition(self, tx: Transaction, state: str, kind: str | None = None, **payload) -> None:
        tx.state = state
        tx.history.append((utcnow().isoformat(timespec="seconds"), state))
        self.ledger.append(tx.tx_id, kind or f"state_{state}", {"state": state, **payload})
        self._persist(tx)
        log.info("tx %s -> %s", tx.tx_id, state, extra={"tx_id": tx.tx_id, "saga_state": state})
        await self._emit(tx)

    def _persist(self, tx: Transaction) -> None:
        self.ledger.upsert_transaction(
            tx.tx_id, state=tx.state, occupant_id=tx.proposal.occupant_id, payer_id=tx.proposal.payer_id,
            merchant_id=tx.quote.merchant_id, provider=tx.quote.provider, total_minor=tx.quote.total.amount,
            currency=tx.quote.total.currency, tier=tx.tier.value, snapshot=tx.model_dump(mode="json"),
        )

    # ---------------------------------------------------------------- happy path
    async def execute(self, req: ConsentRequest, *, voice_confirmed: bool, auth: AuthResult | None) -> Transaction:
        if req.tx_id in self.transactions:
            # a consent request is spent once: a repeated approval must never authorise or order twice
            raise DriveOSError("consent request already used", spoken="That order is already being placed.")
        tx = Transaction(tx_id=req.tx_id, proposal=req.proposal, quote=req.quote, tier=req.tier)
        self.transactions[tx.tx_id] = tx
        self._persist(tx)

        provider = self.merchants.get(tx.quote.provider)
        rail = self.rail_for(provider)
        pay_meta: dict = {}
        prepare = getattr(provider, "prepare_order", None)
        try:
            if prepare is not None and voice_confirmed:
                # merchant platforms that take payment against their own order (Square) need the order first
                pay_meta = await prepare(tx.quote, customer_name=req.proposal.customer_name,
                                         pickup_eta=req.proposal.pickup_eta,
                                         idempotency_key=idempotency_key(tx.tx_id, "prepare"))
                tx.quote = tx.quote.model_copy(update={"provider_ref": {**tx.quote.provider_ref, **pay_meta}})
            record, payment = await self.authority.authorize(req, voice_confirmed=voice_confirmed, auth=auth,
                                                             payments=rail, metadata=pay_meta)
        except DriveOSError as e:
            tx.message = e.code
            await self._transition(tx, "abandoned", reason=e.code)
            raise
        tx.consent_id = record.consent_id
        tx.payment = payment
        await self._transition(tx, "consented", consent_id=record.consent_id)
        if payment.status != "authorized":
            tx.message = payment.decline_reason
            await self._transition(tx, "declined", reason=payment.decline_reason)
            raise PaymentDeclined(payment.decline_reason)
        await self._transition(tx, "authorised", auth_id=payment.auth_id)

        try:
            order = await asyncio.wait_for(provider.place_order(
                tx.quote, payment=payment, customer_name=req.proposal.customer_name,
                pickup_eta=req.proposal.pickup_eta,
                idempotency_key=idempotency_key(tx.tx_id, "place"),
            ), PLACE_TIMEOUT_S)
        except (TimeoutError, DriveOSError, Exception) as e:
            log.warning("place_order failed for %s: %s", tx.tx_id, e)
            await self._void(tx, reason=f"place_failed:{type(e).__name__}")
            if isinstance(e, MerchantRejected):
                raise
            raise MerchantRejected(str(e)) from e

        tx.order = order
        tx.order_status = order.status
        tx.ready_at = order.ready_at
        tx.placed_at = utcnow()
        await self._transition(tx, "placed", order_id=order.order_id, pickup_code=order.pickup_code)

        if provider.capture_before_accept:
            await self._capture(tx)
        self._watch(tx)
        return tx

    def rail_for(self, provider) -> PaymentProvider:
        """A merchant platform with its own payment rail (Square) is paid through it; others use our PSP."""
        return getattr(provider, "payment_rail", None) or self.payments

    def _rail(self, tx: Transaction) -> PaymentProvider:
        return self.rail_for(self.merchants.get(tx.quote.provider))

    async def _capture(self, tx: Transaction) -> None:
        assert tx.payment
        if tx.payment.status == "captured":
            return
        try:
            tx.payment = await self._rail(tx).capture(tx.payment,
                                                      idempotency_key=idempotency_key(tx.tx_id, "capture"))
        except Exception as e:
            # capture failures are retried by recovery; the order stays valid while the authorisation lives
            log.error("capture failed for %s: %s", tx.tx_id, e)
            self.ledger.append(tx.tx_id, "capture_failed", {"error": type(e).__name__})
            return
        await self._transition(tx, "captured", kind="payment_captured", amount=tx.payment.amount.amount)

    async def _void(self, tx: Transaction, *, reason: str) -> None:
        if tx.payment and tx.payment.status == "authorized":
            try:
                tx.payment = await self._rail(tx).void(tx.payment,
                                                       idempotency_key=idempotency_key(tx.tx_id, "void"))
            except Exception as e:
                log.error("void failed for %s: %s — will retry on recovery", tx.tx_id, e)
                self.ledger.append(tx.tx_id, "void_failed", {"error": type(e).__name__})
                tx.message = reason
                await self._transition(tx, "failed", reason=reason)
                return
        tx.message = reason
        await self._transition(tx, "voided", reason=reason)

    async def _refund(self, tx: Transaction, *, reason: str) -> None:
        assert tx.payment
        tx.payment = await self._rail(tx).refund(tx.payment, idempotency_key=idempotency_key(tx.tx_id, "refund"))
        tx.message = reason
        await self._transition(tx, "refunded", reason=reason)

    # ---------------------------------------------------------------- fulfilment
    def _watch(self, tx: Transaction) -> None:
        if tx.tx_id in self._watchers:
            return
        self._watchers[tx.tx_id] = asyncio.create_task(self._watch_loop(tx), name=f"watch-{tx.tx_id}")

    async def _watch_loop(self, tx: Transaction) -> None:
        assert tx.order
        provider = self.merchants.get(tx.quote.provider)
        accepted = tx.state in ("accepted", "captured", "fulfilled")
        deadline = utcnow() + timedelta(seconds=ACCEPT_TIMEOUT_S)
        try:
            async for upd in provider.watch(tx.order):
                await self._on_update(tx, upd)
                if upd.status in (OrderStatus.ACCEPTED, OrderStatus.PREPARING, OrderStatus.READY):
                    accepted = True
                if not accepted and utcnow() > deadline:
                    await self._void(tx, reason="merchant_ack_timeout")
                    return
                if tx.terminal:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("order watch for %s failed", tx.tx_id)
        finally:
            self._watchers.pop(tx.tx_id, None)

    async def _on_update(self, tx: Transaction, upd: OrderUpdate) -> None:
        prev = tx.order_status
        tx.order_status = upd.status
        if upd.ready_at:
            tx.ready_at = upd.ready_at
        if upd.pickup_spot and tx.order:
            tx.order = tx.order.model_copy(update={"pickup_spot": upd.pickup_spot})
        if upd.status == prev and not upd.ready_at:
            return
        self.ledger.append(tx.tx_id, "order_" + upd.status.value, {"message": upd.message,
                                                                   "ready_at": upd.ready_at})
        if upd.status in (OrderStatus.REJECTED, OrderStatus.CANCELED):
            if tx.payment and tx.payment.status == "captured":
                await self._refund(tx, reason=f"merchant_{upd.status.value}")
            else:
                await self._void(tx, reason=f"merchant_{upd.status.value}")
            return
        if upd.status in (OrderStatus.ACCEPTED, OrderStatus.PREPARING, OrderStatus.READY) and tx.state == "placed":
            await self._transition(tx, "accepted")
            if self.settings.payments.capture_on == "accepted":
                await self._capture(tx)
        if upd.status == OrderStatus.PICKED_UP:
            if tx.payment and tx.payment.status == "authorized":
                await self._capture(tx)
            await self._transition(tx, "completed")
            return
        self._persist(tx)
        await self._emit(tx)

    async def notify_arrival(self, tx_id: str) -> None:
        tx = self.transactions.get(tx_id)
        if tx and tx.order and not tx.terminal:
            await self.merchants.get(tx.quote.provider).notify_arrival(tx.order)
            self.ledger.append(tx_id, "customer_arrived", {})

    # ---------------------------------------------------------------- cancellation
    def can_cancel(self, tx: Transaction) -> bool:
        if tx.terminal or not tx.placed_at:
            return tx.state in ("consented", "authorised")
        within = (utcnow() - tx.placed_at).total_seconds() <= self.settings.payments.cancel_window_s
        return within and tx.order_status not in (OrderStatus.READY, OrderStatus.PICKED_UP)

    async def cancel(self, tx_id: str) -> bool:
        tx = self.transactions.get(tx_id)
        if not tx or not self.can_cancel(tx):
            return False
        if tx.order:
            ok = await self.merchants.get(tx.quote.provider).cancel(tx.order)
            if not ok:
                return False
        w = self._watchers.pop(tx_id, None)
        if w:
            w.cancel()
        if tx.payment and tx.payment.status == "captured":
            await self._refund(tx, reason="customer_cancel")
        else:
            await self._void(tx, reason="customer_cancel")
        return True

    # ---------------------------------------------------------------- recovery
    async def recover(self) -> int:
        """Finish or compensate transactions left open by a crash or power loss."""
        n = 0
        for row in self.ledger.open_transactions():
            try:
                tx = Transaction.model_validate(row["snapshot"])
            except Exception:
                log.exception("unreadable snapshot for %s", row["tx_id"])
                continue
            self.transactions[tx.tx_id] = tx
            n += 1
            if tx.state in ("proposed", "consented"):
                await self._transition(tx, "abandoned", reason="recovered_before_authorisation")
            elif tx.state == "authorised" and not tx.order:
                await self._void(tx, reason="recovered_unplaced")
            elif tx.order:
                try:
                    if tx.state == "accepted" and self.settings.payments.capture_on == "accepted":
                        await self._capture(tx)
                    self._watch(tx)
                except ProviderUnavailable:
                    log.warning("provider unavailable while recovering %s; will retry next start", tx.tx_id)
        return n

    def active(self) -> Transaction | None:
        live = [t for t in self.transactions.values() if not t.terminal]
        return max(live, key=lambda t: t.placed_at or utcnow(), default=None)

    async def aclose(self) -> None:
        for t in list(self._watchers.values()):
            t.cancel()
        self._watchers.clear()
