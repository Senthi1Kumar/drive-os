"""Transaction Authority — the only component allowed to move money.

The assistant hands over a TransactionProposal. The Authority:
  1. re-fetches a binding quote from the merchant and diffs it against the proposal,
  2. evaluates spend policy and picks the authentication tier,
  3. renders the consent prompt from the *verified quote* (never from model text),
  4. after spoken consent + authentication, signs a consent record into the ledger,
  5. only then asks the payment provider for an authorisation.
A tampered proposal (wrong amount, merchant or items) cannot reach step 5.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from ..config import Settings
from ..core.ids import idempotency_key, new_id, utcnow
from ..domain.errors import AuthenticationFailed, PolicyDenied, QuoteExpired, QuoteMismatch
from ..domain.models import AuthTier, Payer, PaymentAuthorization, Quote, TransactionProposal
from ..merchants.base import MerchantProvider
from ..payments.base import PaymentProvider
from .auth import AuthResult
from .ledger import Ledger
from .policy import PolicyContext, PolicyEngine

log = logging.getLogger("driveos.authority")

_TIER_RANK = {AuthTier.T0_MANDATE: 0, AuthTier.T1_VOICE: 1, AuthTier.T2_STRONG: 2, AuthTier.T3_OUT_OF_BAND: 3}


class ProviderLookup(Protocol):
    def get(self, provider: str) -> MerchantProvider: ...


@dataclass
class ConsentRequest:
    request_id: str
    tx_id: str
    proposal: TransactionProposal
    quote: Quote
    payer: Payer
    tier: AuthTier
    challenge: str
    expires_at: datetime
    reasons: list[str] = field(default_factory=list)

    @property
    def spoken_prompt(self) -> str:
        return f"That's {self.quote.total.format()} on your {self.payer.display}. Shall I place it?"

    @property
    def expired(self) -> bool:
        return utcnow() >= self.expires_at


@dataclass
class ConsentRecord:
    consent_id: str
    tx_id: str
    occupant_id: str
    payer_id: str
    merchant_id: str
    quote_hash: str
    amount_minor: int
    currency: str
    tier: AuthTier
    methods: list[str]
    at: datetime


class TransactionAuthority:
    def __init__(self, settings: Settings, ledger: Ledger, merchants: ProviderLookup, payments: PaymentProvider):
        self.settings = settings
        self.ledger = ledger
        self.merchants = merchants
        self.payments = payments
        self.policy = PolicyEngine(settings.policy)

    async def review(self, proposal: TransactionProposal, *, category: str, moving: bool) -> ConsentRequest:
        provider = self.merchants.get(proposal.provider)
        fresh = await provider.quote(proposal.cart)
        if fresh.content_hash() != proposal.quote_hash or fresh.total != proposal.stated_total:
            log.warning("proposal %s diverges from verified quote", proposal.proposal_id)
            raise QuoteMismatch("proposal does not match verified quote")
        if fresh.expires_at <= utcnow():
            raise QuoteExpired()

        occupant = self.settings.occupant(proposal.occupant_id)
        payer = self.settings.payer(proposal.payer_id)
        decision = self.policy.evaluate(PolicyContext(
            occupant=occupant, payer=payer, amount=fresh.total, merchant_id=proposal.merchant_id,
            category=category, moving=moving,
            spent_today_minor=self.ledger.spent_today(occupant.id),
            voice_spent_today_minor=self.ledger.spent_today(occupant.id, tier=AuthTier.T1_VOICE.value),
            known_merchant=self.ledger.has_completed_with(occupant.id, proposal.merchant_id),
        ))
        if not decision.allowed:
            raise PolicyDenied(",".join(decision.reasons), spoken=decision.spoken or None)

        ttl = min(self.settings.policy.quote_ttl_s, max(30, int((fresh.expires_at - utcnow()).total_seconds())))
        req = ConsentRequest(
            request_id=new_id("cr"), tx_id=new_id("tx"), proposal=proposal, quote=fresh, payer=payer,
            tier=decision.tier, challenge=secrets.token_hex(16), expires_at=utcnow() + timedelta(seconds=ttl),
            reasons=decision.reasons,
        )
        self.ledger.append(req.tx_id, "consent_requested", {
            "request_id": req.request_id, "proposal_id": proposal.proposal_id, "quote_id": fresh.quote_id,
            "quote_hash": fresh.content_hash(), "amount": fresh.total.amount, "currency": fresh.total.currency,
            "merchant_id": proposal.merchant_id, "payer_id": payer.id, "occupant_id": occupant.id,
            "tier": decision.tier.value, "reasons": decision.reasons,
        })
        return req

    async def authorize(
        self, req: ConsentRequest, *, voice_confirmed: bool, auth: AuthResult | None,
        payments: PaymentProvider | None = None, metadata: dict | None = None,
    ) -> tuple[ConsentRecord, PaymentAuthorization]:
        if req.expired:
            raise QuoteExpired("consent window expired")
        if not voice_confirmed:
            raise AuthenticationFailed("no explicit spoken confirmation")
        methods = ["voice-confirm"]
        if _TIER_RANK[req.tier] >= _TIER_RANK[AuthTier.T2_STRONG]:
            if auth is None or not auth.success or _TIER_RANK[auth.tier] < _TIER_RANK[req.tier]:
                self.ledger.append(req.tx_id, "auth_failed", {"request_id": req.request_id,
                                                              "detail": auth.detail if auth else "missing"})
                raise AuthenticationFailed(auth.detail if auth else "strong auth missing")
            methods.append(auth.method)

        q = req.quote
        record = ConsentRecord(
            consent_id=new_id("cs"), tx_id=req.tx_id, occupant_id=req.proposal.occupant_id, payer_id=req.payer.id,
            merchant_id=q.merchant_id, quote_hash=q.content_hash(), amount_minor=q.total.amount,
            currency=q.total.currency, tier=req.tier, methods=methods, at=utcnow(),
        )
        self.ledger.append(req.tx_id, "consent_given", {
            "consent_id": record.consent_id, "request_id": req.request_id, "challenge": req.challenge,
            "quote_hash": record.quote_hash, "amount": record.amount_minor, "currency": record.currency,
            "payer_id": record.payer_id, "occupant_id": record.occupant_id, "tier": record.tier.value,
            "methods": methods,
        })
        rail = payments or self.payments
        payment = await rail.authorize(
            q.total, req.payer,
            idempotency_key=idempotency_key(req.tx_id, "authorize", record.quote_hash),
            description=f"{q.merchant_name} · DriveOS",
            metadata={"tx_id": req.tx_id, "merchant_id": q.merchant_id, "consent_id": record.consent_id,
                      **(metadata or {})},
        )
        self.ledger.append(req.tx_id, "payment_" + payment.status, {
            "auth_id": payment.auth_id, "provider": payment.provider, "amount": payment.amount.amount,
            "currency": payment.amount.currency, "decline_reason": payment.decline_reason,
        })
        return record, payment
