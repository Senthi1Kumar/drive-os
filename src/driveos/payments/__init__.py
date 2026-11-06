from __future__ import annotations

from ..config import Settings
from ..core.http import Http
from .base import PaymentProvider
from .simulated import SimulatedPayments


def build_payments(settings: Settings, http: Http) -> PaymentProvider:
    if settings.payments.provider == "stripe":
        from .stripe_provider import StripePayments
        return StripePayments(settings.stripe, http)
    if settings.payments.provider == "square":
        from ..merchants.square import SquareApi, SquarePayments
        return SquarePayments(SquareApi(settings.square, http), settings.square)
    return SimulatedPayments()
