"""Typed errors. Each one maps to a specific, speakable outcome for the driver."""


class DriveOSError(Exception):
    """Base error. `spoken` is what Nova says; `code` is stable for logs/metrics."""

    code = "error"
    spoken = "Something went wrong. Nothing was charged."

    def __init__(self, message: str = "", *, spoken: str | None = None):
        super().__init__(message or self.code)
        if spoken:
            self.spoken = spoken


class ConfigError(DriveOSError):
    code = "config"


class ProviderUnavailable(DriveOSError):
    code = "provider_unavailable"
    spoken = "I can't reach that service right now. Nothing was charged."


class QuoteMismatch(DriveOSError):
    code = "quote_mismatch"
    spoken = "The price changed, so I need to check with you again."


class QuoteExpired(DriveOSError):
    code = "quote_expired"
    spoken = "That price expired. Let me get a fresh one."


class PolicyDenied(DriveOSError):
    code = "policy_denied"
    spoken = "That purchase isn't allowed on this profile."


class AuthenticationFailed(DriveOSError):
    code = "auth_failed"
    spoken = "I couldn't verify you, so I didn't place the order."


class PaymentDeclined(DriveOSError):
    code = "payment_declined"
    spoken = "Your card was declined. Nothing was charged."


class MerchantRejected(DriveOSError):
    code = "merchant_rejected"
    spoken = "The shop couldn't take the order, so I released the charge."


class ItemUnavailable(DriveOSError):
    code = "item_unavailable"
    spoken = "That item isn't available right now."


class LedgerTampered(DriveOSError):
    code = "ledger_tampered"
    spoken = "Payments are paused while I check the vehicle's records."


class OrderingUnavailable(DriveOSError):
    code = "ordering_unavailable"
    spoken = "I can't order from there from the car yet, but I can take you there."


class CartConflict(DriveOSError):
    code = "cart_restaurant_mismatch"
    spoken = "Your cart has items from another place. Should I start a new cart?"


class OtpInvalid(DriveOSError):
    code = "otp_invalid"
    spoken = "That code didn't match."
