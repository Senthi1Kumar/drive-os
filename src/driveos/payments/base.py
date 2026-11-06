"""Payment provider contract: tokenised credentials only, delayed capture, explicit void/refund."""

from __future__ import annotations

from typing import Any, Protocol

from ..domain.models import Payer, PaymentAuthorization
from ..domain.money import Money


class PaymentProvider(Protocol):
    name: str

    async def authorize(
        self, amount: Money, payer: Payer, *, idempotency_key: str, description: str, metadata: dict[str, Any]
    ) -> PaymentAuthorization: ...

    async def capture(
        self, auth: PaymentAuthorization, *, idempotency_key: str, amount: Money | None = None
    ) -> PaymentAuthorization: ...

    async def void(self, auth: PaymentAuthorization, *, idempotency_key: str) -> PaymentAuthorization: ...

    async def refund(
        self, auth: PaymentAuthorization, *, idempotency_key: str, amount: Money | None = None
    ) -> PaymentAuthorization: ...

    async def aclose(self) -> None: ...
