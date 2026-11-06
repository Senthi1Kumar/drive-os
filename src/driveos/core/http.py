"""Shared async HTTP with timeouts, bounded retries on transient failures, and redacted logging."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any

import httpx

from ..domain.errors import ProviderUnavailable

log = logging.getLogger("driveos.http")

_SECRET_QS = re.compile(r"(access_token|api_key|key|token)=[^&\s\"']+", re.I)


class _RedactUrls(logging.Filter):
    """httpx logs every request URL at INFO; Mapbox keeps its token in the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SECRET_QS.sub(r"\1=***", str(record.getMessage()))
        record.args = None
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_RedactUrls())

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class Http:
    def __init__(self, *, timeout: float = 8.0, user_agent: str = "DriveOS/1.0"):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=4.0),
            headers={"User-Agent": user_agent},
            http2=False,
            follow_redirects=False,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        service: str,
        retries: int = 2,
        idempotent: bool | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Retries only when safe: GET, or writes carrying an idempotency key (idempotent=True)."""
        method = method.upper()
        can_retry = method in SAFE_METHODS if idempotent is None else idempotent
        attempt = 0
        while True:
            try:
                resp = await self.client.request(method, url, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if can_retry and attempt < retries:
                    attempt += 1
                    await asyncio.sleep(_backoff(attempt))
                    continue
                log.warning("%s %s transport error: %s", service, method, type(e).__name__)
                raise ProviderUnavailable(f"{service}: {type(e).__name__}") from e
            if resp.status_code in RETRY_STATUS and can_retry and attempt < retries:
                attempt += 1
                delay = _retry_after(resp) or _backoff(attempt)
                log.info("%s %s -> %s, retry %d in %.1fs", service, method, resp.status_code, attempt, delay)
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 500:
                raise ProviderUnavailable(f"{service}: HTTP {resp.status_code}")
            return resp

    async def aclose(self) -> None:
        await self.client.aclose()


def _backoff(attempt: int) -> float:
    return min(4.0, 0.25 * 2 ** attempt) * (0.7 + random.random() * 0.6)


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    try:
        return min(8.0, float(v)) if v else None
    except ValueError:
        return None
