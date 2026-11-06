"""In-memory PSP with the same semantics as a real one (idempotency, declines, delayed capture).

Test tokens mirror Stripe's test PaymentMethods: `pm_card_visa_chargeDeclined`,
`pm_card_visa_chargeDeclinedInsufficientFunds` and `pm_card_chargeCustomerFail` decline.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.ids import new_id
from ..domain.models import Payer, PaymentAuthorization
from ..domain.money import Money

_DECLINES = {
    "pm_card_visa_chargeDeclined": "card_declined",
    "pm_card_visa_chargeDeclinedInsufficientFunds": "insufficient_funds",
    "pm_card_chargeCustomerFail": "card_declined",
}


class SimulatedPayments:
    name = "simulated"

    def __init__(self, latency_s: float = 0.35):
        self.latency_s = latency_s
        self._by_key: dict[str, PaymentAuthorization] = {}
        self._auths: dict[str, PaymentAuthorization] = {}

    async def _wait(self) -> None:
        if self.latency_s:
            await asyncio.sleep(self.latency_s)

    def _remember(self, key: str, a: PaymentAuthorization) -> PaymentAuthorization:
        self._by_key[key] = a
        self._auths[a.auth_id] = a
        return a

    async def authorize(self, amount: Money, payer: Payer, *, idempotency_key: str, description: str,
                        metadata: dict[str, Any]) -> PaymentAuthorization:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        await self._wait()
        reason = _DECLINES.get(payer.token, "")
        a = PaymentAuthorization(
            auth_id=new_id("pi"), provider=self.name, amount=amount,
            status="declined" if reason else "authorized", decline_reason=reason,
            provider_ref={"payer": payer.id, "description": description, **metadata},
        )
        return self._remember(idempotency_key, a)

    async def capture(self, auth: PaymentAuthorization, *, idempotency_key: str,
                      amount: Money | None = None) -> PaymentAuthorization:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        await self._wait()
        cur = self._auths.get(auth.auth_id, auth)
        if cur.status != "authorized":
            raise ValueError(f"cannot capture payment in state {cur.status}")
        amt = amount or cur.amount
        if amt.amount > cur.amount.amount:
            raise ValueError("cannot capture more than authorised")
        return self._remember(idempotency_key, cur.model_copy(update={"status": "captured", "amount": amt}))

    async def void(self, auth: PaymentAuthorization, *, idempotency_key: str) -> PaymentAuthorization:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        await self._wait()
        cur = self._auths.get(auth.auth_id, auth)
        if cur.status == "voided":
            return cur
        if cur.status != "authorized":
            raise ValueError(f"cannot void payment in state {cur.status}")
        return self._remember(idempotency_key, cur.model_copy(update={"status": "voided"}))

    async def refund(self, auth: PaymentAuthorization, *, idempotency_key: str,
                     amount: Money | None = None) -> PaymentAuthorization:
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        await self._wait()
        cur = self._auths.get(auth.auth_id, auth)
        if cur.status != "captured":
            raise ValueError(f"cannot refund payment in state {cur.status}")
        return self._remember(idempotency_key, cur.model_copy(update={"status": "refunded"}))

    async def aclose(self) -> None:
        pass
