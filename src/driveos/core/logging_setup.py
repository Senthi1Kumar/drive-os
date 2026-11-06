"""Logging: human-readable on a dev console, JSON lines for the vehicle's log collector.

Secrets never reach logs: settings use SecretStr and the redaction filter masks token-like strings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

_SECRET = re.compile(r"(sk_(?:live|test)_[A-Za-z0-9]+|pk\.[A-Za-z0-9._-]{20,}|EAAA[A-Za-z0-9_-]{20,}|"
                     r"AIza[0-9A-Za-z_-]{30,}|sk-ant-[A-Za-z0-9_-]+|sk-or-[A-Za-z0-9_-]+|access_token=[^&\s]+|key=[^&\s]+)")


_log_transcripts = os.environ.get("DRIVEOS_LOG_TRANSCRIPTS", "").strip().lower() in ("1", "true", "yes", "on")


def set_log_transcripts(on: bool) -> None:
    global _log_transcripts
    _log_transcripts = bool(on)


def describe_text(text: object) -> str:
    """What a log line may say about free text: its length, or the text itself when transcripts are enabled."""
    s = "" if text is None else str(text)
    return repr(s) if _log_transcripts else f"{len(s)} chars"


def _redact(s: str) -> str:
    return _SECRET.sub(lambda m: m.group(0)[:6] + "…redacted", s)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        if record.args:
            record.args = tuple(_redact(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        for k in ("tx_id", "saga_state", "merchant", "provider"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        return json.dumps(out, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_lines: bool = False, transcripts: bool | None = None) -> None:
    if transcripts is not None:
        set_log_transcripts(transcripts)
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stderr)
    h.addFilter(RedactFilter())
    h.setFormatter(JsonFormatter() if json_lines else logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(h)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "asyncio", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
