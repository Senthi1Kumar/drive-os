"""Identifiers: sortable, URL-safe, collision-resistant."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def new_id(prefix: str = "") -> str:
    """ULID-style id: 48-bit ms timestamp + 80 random bits, Crockford base32."""
    ms = int(time.time() * 1000)
    n = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        n, r = divmod(n, 32)
        chars.append(_ALPHABET[r])
    ulid = "".join(reversed(chars))
    return f"{prefix}_{ulid}" if prefix else ulid


def idempotency_key(*parts: str) -> str:
    """Deterministic key so a retried step can never double-charge or double-order."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:40]


def utcnow() -> datetime:
    return datetime.now(UTC)


def pickup_code(seq: int) -> str:
    letters = "ACDEFHJKMNPRTWXY"
    return f"{letters[(seq // 100) % len(letters)]}{seq % 100:02d}"
