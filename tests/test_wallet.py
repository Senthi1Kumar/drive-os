"""Money-moving paths: policy, the Transaction Authority, the signed ledger and the order saga."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from driveos.core.ids import new_id
from driveos.domain.errors import (
    AuthenticationFailed,
    LedgerTampered,
    MerchantRejected,
    PaymentDeclined,
    PolicyDenied,
    QuoteMismatch,
)
from driveos.domain.models import AuthTier, Cart, TransactionProposal
from driveos.domain.money import Money
from driveos.merchants.base import make_line
from driveos.merchants.local import LocalMerchantProvider
from driveos.wallet.auth import AuthResult, voice_consent
from driveos.wallet.policy import PolicyContext, PolicyEngine

PLUR = "r3-plur-coffee"
LATTE = "CALI_CAESGFg1VlNCUkZMVlZHUEs3M0FUN0tDWlJEWg"  # Cafe Latte, $5.25 (DriveOS Los Gatos fixture)


async def _proposal(demo: LocalMerchantProvider, settings, *, merchant=PLUR, item=LATTE, sel=None,
                    qty=1, payer="visa-4417") -> TransactionProposal:
    menu = await demo.get_menu(merchant)
    cart = Cart(merchant_id=merchant, lines=[make_line("l1", menu.item(item), sel or {}, qty)])
    q = await demo.quote(cart)
    return TransactionProposal(proposal_id=new_id("pp"), occupant_id="singh", payer_id=payer, merchant_id=merchant,
                               provider="local", cart=cart, quote_id=q.quote_id, quote_hash=q.content_hash(),
                               stated_total=q.total, customer_name="Singh")


def _strong(ok=True):
    return AuthResult(AuthTier.T2_STRONG, "face-sim", ok, detail="match" if ok else "no-match")


# ---------------------------------------------------------------- quote / tax
async def test_quote_includes_tax(demo, settings):
    p = await _proposal(demo, settings, qty=2)
    q = await demo.quote(p.cart)
    assert q.subtotal.amount == 1050
    assert q.tax.amount == 97  # 9.25 % California, rounded half-up
    assert q.total.amount == 1147


async def test_fuel_prepay_is_not_taxed(demo, settings):
    p = await _proposal(demo, settings, merchant="v-fuel-1", item="fuel-regular", sel={"amount": ["a60"]})
    q = await demo.quote(p.cart)
    assert (q.subtotal.amount, q.tax.amount, q.total.amount) == (6000, 0, 6000)


# ---------------------------------------------------------------- policy
def _ctx(settings, amount, *, known=True, role="owner", payer="visa-4417", spent=0, voice_spent=0, category="coffee"):
    occ = settings.occupant().model_copy(update={"role": role})
    return PolicyContext(occupant=occ, payer=settings.payer(payer), amount=Money(amount=amount),
                         merchant_id=PLUR, category=category, moving=True, spent_today_minor=spent,
                         voice_spent_today_minor=voice_spent, known_merchant=known)


def test_policy_tiers(settings):
    pe = PolicyEngine(settings.policy)
    assert pe.evaluate(_ctx(settings, 689)).tier == AuthTier.T1_VOICE
    assert pe.evaluate(_ctx(settings, 689, known=False)).tier == AuthTier.T1_VOICE   # voice cap, any merchant
    d = pe.evaluate(_ctx(settings, 4000))
    assert d.tier == AuthTier.T2_STRONG and "above_voice_only_cap" in d.reasons
    assert pe.evaluate(_ctx(settings, 900, voice_spent=4500)).tier == AuthTier.T2_STRONG
    settings.policy.voice_requires_known_merchant = True
    d = PolicyEngine(settings.policy).evaluate(_ctx(settings, 689, known=False))
    assert d.tier == AuthTier.T2_STRONG and "first_purchase_at_merchant" in d.reasons


def test_policy_denials(settings):
    pe = PolicyEngine(settings.policy)
    assert not pe.evaluate(_ctx(settings, 500, role="valet")).allowed
    assert not pe.evaluate(_ctx(settings, 20000)).allowed
    assert not pe.evaluate(_ctx(settings, 5000, spent=28000)).allowed
    assert not pe.evaluate(_ctx(settings, 500, role="driver", category="retail")).allowed


# ---------------------------------------------------------------- authority
async def test_tampered_amount_is_rejected(stack, settings):
    p = await _proposal(stack["demo"], settings)
    bad = p.model_copy(update={"stated_total": Money(amount=1)})
    with pytest.raises(QuoteMismatch):
        await stack["authority"].review(bad, category="coffee", moving=True)


async def test_tampered_cart_is_rejected(stack, settings):
    p = await _proposal(stack["demo"], settings)
    other = await _proposal(stack["demo"], settings, qty=3)
    bad = p.model_copy(update={"cart": other.cart})  # cart swapped, hash and total kept
    with pytest.raises(QuoteMismatch):
        await stack["authority"].review(bad, category="coffee", moving=True)


async def test_large_order_needs_strong_auth(stack, settings):
    p = await _proposal(stack["demo"], settings, qty=6)   # $34.41 > $25 voice cap
    req = await stack["authority"].review(p, category="coffee", moving=True)
    assert req.tier == AuthTier.T2_STRONG
    assert "$34.41" in req.spoken_prompt
    with pytest.raises(AuthenticationFailed):
        await stack["authority"].authorize(req, voice_confirmed=True, auth=voice_consent())
    with pytest.raises(AuthenticationFailed):
        await stack["authority"].authorize(req, voice_confirmed=True, auth=_strong(False))
    with pytest.raises(AuthenticationFailed):
        await stack["authority"].authorize(req, voice_confirmed=False, auth=_strong())
    record, pay = await stack["authority"].authorize(req, voice_confirmed=True, auth=_strong())
    assert pay.status == "authorized" and pay.amount.amount == 3441
    assert record.methods == ["voice-confirm", "face-sim"]


async def test_policy_denied_surfaces_spoken_reason(stack, settings):
    p = await _proposal(stack["demo"], settings, qty=30)
    with pytest.raises(PolicyDenied) as e:
        await stack["authority"].review(p, category="coffee", moving=True)
    assert "limit" in e.value.spoken


# ---------------------------------------------------------------- ledger
def test_ledger_detects_tampering(ledger):
    for k in range(4):
        ledger.append("tx_1", "state_x", {"n": k})
    assert ledger.verify() == 4
    # append-only: the schema refuses updates outright
    with pytest.raises(sqlite3.DatabaseError):
        ledger._db.execute("UPDATE entries SET payload='{}' WHERE seq=2")  # noqa: SLF001
    # an attacker with raw file access drops the trigger and edits a row
    ledger._db.execute("DROP TRIGGER IF EXISTS entries_no_update")  # noqa: SLF001
    for (name,) in ledger._db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():  # noqa: SLF001
        ledger._db.execute(f"DROP TRIGGER {name}")  # noqa: SLF001
    ledger._db.execute("""UPDATE entries SET payload='{"n":99}' WHERE seq=2""")  # noqa: SLF001
    with pytest.raises(LedgerTampered):
        ledger.verify()


# ---------------------------------------------------------------- saga
async def _wait(cond, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_saga_happy_path(stack, settings, ledger):
    orch = stack["orch"]
    p = await _proposal(stack["demo"], settings)
    req = await stack["authority"].review(p, category="coffee", moving=True)
    tx = await orch.execute(req, voice_confirmed=True, auth=_strong())
    assert tx.state == "placed" and tx.order and tx.order.pickup_code
    assert await _wait(lambda: tx.state == "captured")
    await orch.notify_arrival(tx.tx_id)
    assert await _wait(lambda: tx.state == "completed", 15)
    states = [s for _, s in tx.history]
    assert states[:4] == ["consented", "authorised", "placed", "accepted"]
    assert ledger.verify() > 5
    assert ledger.has_completed_with("singh", PLUR)
    req2 = await stack["authority"].review(await _proposal(stack["demo"], settings), category="coffee", moving=True)
    assert req2.tier == AuthTier.T1_VOICE
    await orch.aclose()


async def test_saga_merchant_reject_voids(settings, ledger):
    from driveos.merchants.local_catalog import LocalCatalog
    from driveos.merchants.registry import MerchantRegistry
    from driveos.payments.simulated import SimulatedPayments
    from driveos.wallet.authority import TransactionAuthority
    from driveos.wallet.saga import OrderOrchestrator

    demo = LocalMerchantProvider(LocalCatalog(include_dataset=False), time_scale=200, accept_delay_s=0.05,
                                 reject_merchants={PLUR})
    reg, pay = MerchantRegistry([demo]), SimulatedPayments(latency_s=0)
    auth = TransactionAuthority(settings, ledger, reg, pay)
    orch = OrderOrchestrator(settings, ledger, auth, reg, pay)
    req = await auth.review(await _proposal(demo, settings), category="coffee", moving=True)
    with pytest.raises(MerchantRejected):
        await orch.execute(req, voice_confirmed=True, auth=_strong())
    tx = orch.transactions[req.tx_id]
    assert tx.state == "voided" and tx.payment.status == "voided"


async def test_saga_card_decline(stack, settings):
    p = await _proposal(stack["demo"], settings)
    settings.payers[0] = settings.payers[0].model_copy(update={"token": "pm_card_visa_chargeDeclined"})
    req = await stack["authority"].review(p, category="coffee", moving=True)
    with pytest.raises(PaymentDeclined):
        await stack["orch"].execute(req, voice_confirmed=True, auth=_strong())
    assert stack["orch"].transactions[req.tx_id].state == "declined"


async def test_customer_cancel_refunds_or_voids(stack, settings):
    orch = stack["orch"]
    req = await stack["authority"].review(await _proposal(stack["demo"], settings), category="coffee", moving=True)
    tx = await orch.execute(req, voice_confirmed=True, auth=_strong())
    assert await orch.cancel(tx.tx_id)
    assert tx.state in ("voided", "refunded")
    await orch.aclose()


async def test_recover_voids_unplaced_authorisation(stack, settings, ledger):
    orch = stack["orch"]
    req = await stack["authority"].review(await _proposal(stack["demo"], settings), category="coffee", moving=True)
    _, pay = await stack["authority"].authorize(req, voice_confirmed=True, auth=_strong())
    from driveos.wallet.saga import OrderOrchestrator, Transaction

    tx = Transaction(tx_id=req.tx_id, proposal=req.proposal, quote=req.quote, state="authorised", payment=pay)
    orch._persist(tx)  # noqa: SLF001 - simulate power loss right after authorisation
    fresh = OrderOrchestrator(settings, ledger, stack["authority"], stack["registry"], stack["payments"])
    assert await fresh.recover() == 1
    assert fresh.transactions[req.tx_id].state == "voided"
