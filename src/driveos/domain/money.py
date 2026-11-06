"""Money in integer minor units. Floats never touch an amount."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "JPY": "¥", "INR": "₹"}
_ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP"}


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: int = Field(description="Amount in minor units (cents for USD)")
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        return cls(amount=0, currency=currency)

    @classmethod
    def from_decimal(cls, value: str | Decimal | int, currency: str = "USD") -> Money:
        exp = 0 if currency in _ZERO_DECIMAL else 2
        q = Decimal(str(value)).quantize(Decimal(1).scaleb(-exp), rounding=ROUND_HALF_UP)
        return cls(amount=int(q.scaleb(exp)), currency=currency)

    def _check(self, other: Money) -> None:
        if other.currency != self.currency:
            raise ValueError(f"currency mismatch {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, qty: int) -> Money:
        if not isinstance(qty, int):
            raise TypeError("Money can only be multiplied by an int quantity")
        return Money(amount=self.amount * qty, currency=self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def apply_rate(self, rate: Decimal | str) -> Money:
        """Multiply by a rate (e.g. tax 0.1025), rounding half-up to the minor unit."""
        v = (Decimal(self.amount) * Decimal(str(rate))).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return Money(amount=int(v), currency=self.currency)

    @property
    def decimal(self) -> Decimal:
        exp = 0 if self.currency in _ZERO_DECIMAL else 2
        return Decimal(self.amount).scaleb(-exp)

    def format(self) -> str:
        sym = _SYMBOLS.get(self.currency, self.currency + " ")
        if self.currency in _ZERO_DECIMAL:
            return f"{sym}{self.amount:,}"
        sign = "-" if self.amount < 0 else ""
        return f"{sign}{sym}{abs(self.decimal):,.2f}"

    def spoken(self) -> str:
        """'$6.89' -> '6 dollars 89' style is awkward for TTS; most engines read '$6.89' well."""
        return self.format()

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.format()
