"""Stripe PaymentIntents: off-session manual-capture holds on a saved card."""

from __future__ import annotations

from typing import Any

from ..config import StripeConfig
from ..core.http import Http
from ..domain.errors import ConfigError, PaymentDeclined
from ..domain.models import Payer, PaymentAuthorization
from ..domain.money import Money

API = "https://api.stripe.com/v1"
_STATUS = {"requires_capture": "authorized", "succeeded": "captured", "canceled": "voided"}


class StripePayments:
    name = "stripe"

    def __init__(self, cfg: StripeConfig, http: Http):
        if not cfg.secret_key:
            raise ConfigError("Stripe secret key not configured")
        self.cfg = cfg
        self.http = http
        self._headers = {"Authorization": f"Bearer {cfg.secret_key.get_secret_value()}",
                         "Stripe-Version": cfg.api_version}

    async def _post(self, path: str, data: dict[str, Any], key: str) -> tuple[int, dict]:
        r = await self.http.request("POST", API + path, data=data,
                                    headers={**self._headers, "Idempotency-Key": key[:255]},
                                    service="stripe", idempotent=True)
        if r.status_code in (401, 403):
            raise ConfigError("stripe authentication failed")
        return r.status_code, r.json()

    def _auth(self, pi: dict, fallback_amount: Money) -> PaymentAuthorization:
        cur = (pi.get("currency") or fallback_amount.currency).upper()
        amt = pi.get("amount_received") or pi.get("amount") or fallback_amount.amount
        return PaymentAuthorization(auth_id=pi.get("id", ""), provider=self.name,
                                    amount=Money(amount=int(amt), currency=cur),
                                    status=_STATUS.get(pi.get("status", ""), "declined"),
                                    provider_ref={"status": pi.get("status"), "latest_charge": pi.get("latest_charge")})

    async def authorize(self, amount: Money, payer: Payer, *, idempotency_key: str, description: str,
                        metadata: dict[str, Any]) -> PaymentAuthorization:
        customer = payer.customer_ref or self.cfg.customer_id
        pm = payer.token or self.cfg.payment_method_id
        data: dict[str, Any] = {
            "amount": str(amount.amount), "currency": amount.currency.lower(),
            "payment_method": pm, "capture_method": "manual", "confirm": "true", "off_session": "true",
            "description": description[:350],
            "payment_method_options[card][stored_credential_usage]": "unscheduled",
        }
        if customer:
            data["customer"] = customer
        for k, v in metadata.items():
            if v is not None:
                data[f"metadata[{k}]"] = str(v)[:500]
        status, body = await self._post("/payment_intents", data, idempotency_key)
        if status >= 400:
            err = body.get("error", {})
            pi = err.get("payment_intent") or {}
            return PaymentAuthorization(auth_id=pi.get("id", ""), provider=self.name, amount=amount, status="declined",
                                        decline_reason=err.get("decline_code") or err.get("code") or "declined")
        return self._auth(body, amount)

    async def capture(self, auth: PaymentAuthorization, *, idempotency_key: str,
                      amount: Money | None = None) -> PaymentAuthorization:
        data = {"amount_to_capture": str(amount.amount)} if amount else {}
        status, body = await self._post(f"/payment_intents/{auth.auth_id}/capture", data, idempotency_key)
        if status >= 400:
            raise PaymentDeclined(body.get("error", {}).get("message", "capture failed"))
        return self._auth(body, auth.amount)

    async def void(self, auth: PaymentAuthorization, *, idempotency_key: str) -> PaymentAuthorization:
        status, body = await self._post(f"/payment_intents/{auth.auth_id}/cancel",
                                        {"cancellation_reason": "abandoned"}, idempotency_key)
        if status >= 400:
            raise PaymentDeclined(body.get("error", {}).get("message", "cancel failed"))
        return self._auth(body, auth.amount)

    async def refund(self, auth: PaymentAuthorization, *, idempotency_key: str,
                     amount: Money | None = None) -> PaymentAuthorization:
        data = {"payment_intent": auth.auth_id, "reason": "requested_by_customer"}
        if amount:
            data["amount"] = str(amount.amount)
        status, body = await self._post("/refunds", data, idempotency_key)
        if status >= 400:
            raise PaymentDeclined(body.get("error", {}).get("message", "refund failed"))
        return auth.model_copy(update={"status": "refunded", "provider_ref": {**auth.provider_ref,
                                                                              "refund_id": body.get("id")}})

    async def aclose(self) -> None:
        pass
