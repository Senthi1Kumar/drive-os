"""Exa web search for the agent: one POST, a scrubbed query, a compact result.

Only the (scrubbed) query and objective leave the device, plus an opaque salted-hash user identifier. The API
key travels in a request header, which the shared HTTP wrapper never logs. Warnings from Exa are logged and
never shown to the model; notices are passed through. Query text is logged only when transcript logging is on.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ...config import ExaConfig
from ...core.http import Http
from ...core.logging_setup import describe_text

log = logging.getLogger("driveos.web_search")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_STREET = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){0,3}(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
    r"court|ct|way|place|pl|highway|hwy|parkway|pkwy|circle|cir|terrace|ter)\b\.?", re.I)
_NUMBERISH = re.compile(r"(?<!\w)\+?\(?\d[\d\s().-]{5,}\d")
_COORD = re.compile(r"-?\d{1,3}\.\d{3,}")
_NEAR_ME = re.compile(r"\b(near me|nearby|around here|close by|nearest|closest|near here)\b", re.I)
_SALT = "driveos-exa-v1"


class WebSearchError(Exception):
    """Search could not be completed; the caller speaks a short notice."""


def scrub_query(text: str, redact_terms: tuple[str, ...] = ()) -> str:
    """Strip what must not leave the device: emails, phone and card numbers, street addresses, coordinates, names."""
    out = _EMAIL.sub(" ", text)
    out = _STREET.sub(" ", out)
    out = _NUMBERISH.sub(lambda m: " " if sum(c.isdigit() for c in m.group(0)) >= 7 else m.group(0), out)
    out = _COORD.sub(" ", out)
    for term in redact_terms:
        if term and term.strip():
            out = re.sub(rf"\b{re.escape(term.strip())}\b", " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" ,;")


def coarse_city(label: str | None) -> str | None:
    """City part of a location label ("Palo Alto, California" -> "Palo Alto"); nothing when it is numeric."""
    city = (label or "").split(",")[0].strip()
    return city if city and not any(c.isdigit() for c in city) else None


def _notice_text(n: Any) -> str:
    if isinstance(n, str):
        return n
    if isinstance(n, dict):
        return str(n.get("message") or n.get("text") or n.get("notice") or "")
    return str(n)


def _total_cost(c: Any) -> Any:
    return c.get("total") if isinstance(c, dict) else c


class WebSearch:
    def __init__(self, cfg: ExaConfig, http: Http, *, identity: str):
        assert cfg.api_key is not None
        self.cfg = cfg
        self.http = http
        self.user_identifier = hashlib.sha256(f"{_SALT}|{identity}".encode()).hexdigest()[:32]

    async def search(self, query: str, objective: str, *, city: str | None = None,
                     redact_terms: tuple[str, ...] = ()) -> dict[str, Any]:
        q = scrub_query(query, redact_terms)
        if not q:
            raise WebSearchError("empty query")
        if city and _NEAR_ME.search(q) and city.lower() not in q.lower():
            q = f"{q} in {city}"
        body = {"query": q, "objective": scrub_query(objective, redact_terms) or q, "type": self.cfg.type,
                "numResults": self.cfg.num_results, "contents": {"highlights": True},
                "userIdentifier": self.user_identifier}
        t = self.cfg.timeout_s
        try:
            resp = await asyncio.wait_for(self.http.request(
                "POST", f"{self.cfg.base_url.rstrip('/')}/search", service="exa", retries=0,
                json=body, timeout=httpx.Timeout(t, connect=min(1.5, t)),
                headers={"x-api-key": self.cfg.api_key.get_secret_value()}), t + 0.3)
        except TimeoutError as e:
            log.warning("exa timeout after %.1fs (query %s)", t, describe_text(q))
            raise WebSearchError("timeout") from e
        except Exception as e:
            log.warning("exa request failed: %s", type(e).__name__)
            raise WebSearchError(type(e).__name__) from e
        if resp.status_code != 200:
            log.warning("exa HTTP %s", resp.status_code)
            raise WebSearchError(f"http_{resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise WebSearchError("bad_json") from e
        if not isinstance(data, dict):
            raise WebSearchError("bad_json")
        for w in data.get("warnings") or []:
            log.warning("exa warning: %s", _notice_text(w)[:300])
        results = [r for r in data.get("results") or [] if isinstance(r, dict)]
        log.info("exa ok request_id=%s search_ms=%s cost=%s results=%d query=%s", data.get("requestId"),
                 data.get("searchTime"), _total_cost(data.get("costDollars")), len(results), describe_text(q))
        return {"ok": True, "count": len(results), "results": self._compact(results),
                "notices": [t_ for t_ in (_notice_text(n).strip() for n in data.get("notices") or []) if t_][:3]}

    def _compact(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        per = self.cfg.max_chars // max(1, len(results))
        out = []
        for r in results:
            left, lights = per, []
            for h in r.get("highlights") or []:
                h = re.sub(r"\s+", " ", str(h)).strip()
                if not h or left <= 0:
                    continue
                lights.append(h[:left])
                left -= len(lights[-1])
            url = str(r.get("url") or "")
            item = {"title": str(r.get("title") or "")[:120], "source": urlparse(url).netloc.removeprefix("www."),
                    "url": url, "highlights": lights}
            if r.get("publishedDate"):
                item["published"] = str(r["publishedDate"])[:10]
            out.append(item)
        return out
