"""Declarative spend policy. Evaluated before every consent prompt; the AI cannot bypass it."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import PolicyConfig
from ..domain.models import AuthTier, Occupant, Payer
from ..domain.money import Money

# roles that may never spend from the car
_NO_SPEND_ROLES = {"valet"}
# categories a non-owner driver may buy without owner approval
_DRIVER_CATEGORIES = {"ev", "gas", "parking", "coffee", "food", "sandwich", "bakery", "store", "pharmacy"}


def _dollars(cents: int) -> str:
    d, c = divmod(int(cents), 100)
    return f"${d:,}" if c == 0 else f"${d:,}.{c:02d}"


@dataclass
class PolicyContext:
    occupant: Occupant
    payer: Payer
    amount: Money
    merchant_id: str
    category: str
    moving: bool
    spent_today_minor: int
    voice_spent_today_minor: int
    known_merchant: bool


@dataclass
class PolicyDecision:
    allowed: bool
    tier: AuthTier = AuthTier.T2_STRONG
    reasons: list[str] = field(default_factory=list)
    spoken: str = ""


class PolicyEngine:
    def __init__(self, cfg: PolicyConfig):
        self.cfg = cfg

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        c = self.cfg
        if ctx.occupant.role in _NO_SPEND_ROLES:
            return PolicyDecision(False, reasons=["role_no_spend"], spoken="Payments are turned off in valet mode.")
        if ctx.payer.id not in ctx.occupant.payer_ids:
            return PolicyDecision(False, reasons=["payer_not_bound"],
                                  spoken="That card isn't linked to your profile.")
        if ctx.occupant.role == "driver" and ctx.category not in _DRIVER_CATEGORIES:
            return PolicyDecision(False, reasons=["category_not_allowed"],
                                  spoken="That kind of purchase needs the owner's approval.")
        if ctx.amount.amount > c.per_transaction_max_minor:
            return PolicyDecision(False, reasons=["over_transaction_max"],
                                  spoken=f"That's {_dollars(ctx.amount.amount)}, over your "
                                         f"{_dollars(c.per_transaction_max_minor)} per-order limit.")
        if ctx.spent_today_minor + ctx.amount.amount > c.daily_max_minor:
            return PolicyDecision(False, reasons=["over_daily_max"],
                                  spoken=f"That's {_dollars(ctx.amount.amount)}, which would go over your "
                                         f"{_dollars(c.daily_max_minor)} daily in-car limit.")

        reasons: list[str] = []
        voice_ok = (
            ctx.occupant.role == "owner"
            and (ctx.known_merchant or not c.voice_requires_known_merchant)
            and ctx.amount.amount <= c.voice_only_max_minor
            and ctx.voice_spent_today_minor + ctx.amount.amount <= c.voice_only_daily_minor
        )
        if voice_ok:
            tier = AuthTier.T1_VOICE
        else:
            tier = AuthTier.T2_STRONG
            if not ctx.known_merchant and c.voice_requires_known_merchant:
                reasons.append("first_purchase_at_merchant")
            if ctx.voice_spent_today_minor + ctx.amount.amount > c.voice_only_daily_minor:
                reasons.append("voice_daily_limit")
            if ctx.amount.amount > c.voice_only_max_minor:
                reasons.append("above_voice_only_cap")
        return PolicyDecision(True, tier=tier, reasons=reasons)
