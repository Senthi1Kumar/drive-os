"""Persistent session store (SQLite, WAL). Writes go through a background thread so a turn never waits on disk.

Holds the turn log, tool-call audit, rolling summary, driver preferences and the open-order pointer. The ledger
stays the source of truth for orders; this store keeps only a pointer and the last known state. Nothing
biometric, no codes, no card data, no raw audio.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ...core.private_fs import ensure_private_dir, private_file
from .context import Turn, redact_digits

log = logging.getLogger("driveos.memory")

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
  session_id TEXT PRIMARY KEY, created_at REAL, last_active_at REAL, device_id TEXT, driver_id TEXT,
  status TEXT CHECK(status IN ('active','idle','ended')), summary TEXT DEFAULT '',
  summary_upto_turn INTEGER DEFAULT 0, screen_ref TEXT);
CREATE TABLE IF NOT EXISTS turn (
  session_id TEXT, turn_no INTEGER, turn_id TEXT UNIQUE, t_start REAL, route TEXT, user_text TEXT, stt_conf REAL,
  reply_text TEXT, trace TEXT DEFAULT '', status TEXT, model TEXT, provider TEXT, steps INTEGER,
  tok_in INTEGER, tok_out INTEGER, tok_cached INTEGER, cost_usd REAL, lat_first_audio_ms INTEGER,
  lat_total_ms INTEGER, PRIMARY KEY(session_id, turn_no));
CREATE TABLE IF NOT EXISTS tool_call (
  turn_id TEXT, idx INTEGER, name TEXT, effect TEXT, args_json TEXT, result_json TEXT, ok INTEGER, ms INTEGER,
  request_id TEXT UNIQUE, PRIMARY KEY(turn_id, idx));
CREATE TABLE IF NOT EXISTS driver_pref (
  driver_id TEXT, key TEXT, value TEXT, source TEXT, confidence REAL, updated_at REAL,
  PRIMARY KEY(driver_id, key));
CREATE TABLE IF NOT EXISTS order_link (
  session_id TEXT, tx_id TEXT PRIMARY KEY, merchant_id TEXT, cart_digest TEXT, saga_state TEXT,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS cart_snapshot (
  session_id TEXT PRIMARY KEY, merchant_id TEXT, lines_json TEXT, updated_at REAL);
"""
TERMINAL_SAGA = {"completed", "voided", "refunded", "declined", "failed", "expired", "canceled"}


def _redact(text: str) -> str:
    return redact_digits(text)


class SessionStore:
    def __init__(self, path: Path | str, *, device_id: str = "device", driver_id: str = "driver1",
                 clock=time.time):
        self.path = Path(path)
        self.device_id, self.driver_id, self.clock = device_id, driver_id, clock
        self.in_memory_only = False
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        try:
            ensure_private_dir(self.path.parent)
            private_file(self.path, create=True)
            self._db = self._connect(str(self.path))
            private_file(self.path)
        except (OSError, sqlite3.Error) as e:
            log.error("session store unavailable (%s); running in memory only", type(e).__name__)
            self._db = self._connect(":memory:")
            self.in_memory_only = True
        self._writer = threading.Thread(target=self._drain, name="session-writer", daemon=True)
        self._writer.start()

    def _connect(self, target: str) -> sqlite3.Connection:
        db = sqlite3.connect(target, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript(SCHEMA)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
        return db

    # ---------------------------------------------------------------- writer
    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._q.task_done()
                return
            sql, params = item
            try:
                with self._lock:
                    self._db.execute(sql, params)
                    self._db.commit()
            except sqlite3.Error as e:
                log.error("session write failed: %s", type(e).__name__)
            self._q.task_done()

    def _w(self, sql: str, params: tuple = ()) -> None:
        self._q.put((sql, params))

    def flush(self) -> None:
        self._q.join()

    def close(self) -> None:
        self.flush()
        self._q.put(None)
        self._writer.join(timeout=2)
        with self._lock:
            self._db.close()

    def _r(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        self.flush()
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- session
    def open_session(self, resume_window_s: float = 1800.0) -> tuple[str, bool]:
        """Resume the latest active session inside the window, else start one carrying the last summary."""
        now = self.clock()
        with self._lock:
            self._db.execute("UPDATE turn SET status='error:crash' WHERE status='running'")
            self._db.commit()
        rows = self._r("SELECT * FROM session WHERE status IN ('active','idle') ORDER BY last_active_at DESC LIMIT 1")
        if rows and now - rows[0]["last_active_at"] <= resume_window_s:
            sid = rows[0]["session_id"]
            self._w("UPDATE session SET status='active', last_active_at=? WHERE session_id=?", (now, sid))
            return sid, True
        prev = self._r("SELECT summary FROM session ORDER BY last_active_at DESC LIMIT 1")
        carried = prev[0]["summary"] if prev else ""
        self._w("UPDATE session SET status='ended' WHERE status IN ('active','idle')")
        sid = "s_" + uuid.uuid4().hex[:12]
        self._w("INSERT INTO session(session_id, created_at, last_active_at, device_id, driver_id, status, summary)"
                " VALUES (?,?,?,?,?,'active',?)", (sid, now, now, self.device_id, self.driver_id, carried))
        return sid, False

    def touch(self, sid: str, screen_ref: str = "") -> None:
        self._w("UPDATE session SET last_active_at=?, screen_ref=? WHERE session_id=?", (self.clock(), screen_ref, sid))

    def idle_session(self, sid: str) -> None:
        self._w("UPDATE session SET status='idle', last_active_at=? WHERE session_id=?", (self.clock(), sid))

    def end_session(self, sid: str) -> None:
        self._w("UPDATE session SET status='ended' WHERE session_id=?", (sid,))

    def summary(self, sid: str) -> tuple[str, int]:
        r = self._r("SELECT summary, summary_upto_turn FROM session WHERE session_id=?", (sid,))
        return (r[0]["summary"] or "", r[0]["summary_upto_turn"] or 0) if r else ("", 0)

    def set_summary(self, sid: str, text: str, upto: int) -> None:
        self._w("UPDATE session SET summary=?, summary_upto_turn=? WHERE session_id=?", (text, upto, sid))

    def last_active(self, sid: str) -> float:
        r = self._r("SELECT last_active_at FROM session WHERE session_id=?", (sid,))
        return r[0]["last_active_at"] if r else 0.0

    # ---------------------------------------------------------------- turns
    def next_turn_no(self, sid: str) -> int:
        r = self._r("SELECT COALESCE(MAX(turn_no),0)+1 AS n FROM turn WHERE session_id=?", (sid,))
        return int(r[0]["n"])

    def begin_turn(self, sid: str, turn_no: int, turn_id: str, text: str, route: str, stt_conf: float | None) -> None:
        self._w("INSERT INTO turn(session_id, turn_no, turn_id, t_start, route, user_text, stt_conf, status)"
                " VALUES (?,?,?,?,?,?,?,'running')",
                (sid, turn_no, turn_id, self.clock(), route, _redact(text)[:400], stt_conf))

    def finish_turn(self, turn_id: str, *, status: str, reply: str, trace: str, route: str, model: str | None,
                    provider: str | None, steps: int, tok_in: int, tok_out: int, tok_cached: int, cost: float,
                    first_audio_ms: int | None, total_ms: int) -> None:
        self._w("UPDATE turn SET status=?, reply_text=?, trace=?, route=?, model=?, provider=?, steps=?, tok_in=?,"
                " tok_out=?, tok_cached=?, cost_usd=?, lat_first_audio_ms=?, lat_total_ms=? WHERE turn_id=?",
                (status, _redact(reply)[:400], trace[:200], route, model, provider, steps, tok_in, tok_out,
                 tok_cached, cost, first_audio_ms, total_ms, turn_id))

    def log_tool(self, turn_id: str, idx: int, name: str, effect: str, args: dict[str, Any], result: dict[str, Any],
                 ok: bool, ms: int, request_id: str) -> None:
        self._w("INSERT OR IGNORE INTO tool_call(turn_id, idx, name, effect, args_json, result_json, ok, ms, request_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (turn_id, idx, name, effect, json.dumps(args)[:600], json.dumps(result)[:800], int(ok), ms, request_id))

    def lookup_request(self, request_id: str) -> dict[str, Any] | None:
        r = self._r("SELECT result_json FROM tool_call WHERE request_id=?", (request_id,))
        if not r:
            return None
        try:
            return json.loads(r[0]["result_json"])
        except ValueError:
            return None

    def history(self, sid: str, after_turn: int = 0, limit: int = 40) -> list[tuple[int, Turn]]:
        rows = self._r("SELECT turn_no, user_text, reply_text, trace, status FROM turn WHERE session_id=? AND turn_no>?"
                       " AND status IN ('done','cancelled','escalated') ORDER BY turn_no DESC LIMIT ?",
                       (sid, after_turn, limit))
        return [(r["turn_no"], Turn(r["user_text"] or "", r["reply_text"] or "", r["trace"] or "", r["status"]))
                for r in reversed(rows)]

    def day_cost(self, since: float) -> float:
        r = self._r("SELECT COALESCE(SUM(cost_usd),0) AS c FROM turn WHERE t_start>=?", (since,))
        return float(r[0]["c"])

    # ---------------------------------------------------------------- preferences
    def set_pref(self, key: str, value: str, source: str = "explicit", confidence: float = 1.0) -> None:
        self._w("INSERT INTO driver_pref(driver_id, key, value, source, confidence, updated_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(driver_id, key) DO UPDATE SET value=excluded.value, source=excluded.source,"
                " confidence=excluded.confidence, updated_at=excluded.updated_at",
                (self.driver_id, key, value[:80], source, confidence, self.clock()))

    def prefs(self, limit: int = 5) -> dict[str, str]:
        rows = self._r("SELECT key, value FROM driver_pref WHERE driver_id=? ORDER BY confidence DESC, updated_at DESC"
                       " LIMIT ?", (self.driver_id, limit))
        return {r["key"]: r["value"] for r in rows}

    def forget_prefs(self) -> None:
        self._w("DELETE FROM driver_pref WHERE driver_id=?", (self.driver_id,))
        self.flush()

    # ---------------------------------------------------------------- order pointer and cart
    def link_order(self, sid: str, tx_id: str, merchant_id: str, cart_digest: str, saga_state: str) -> None:
        now = self.clock()
        self._w("INSERT INTO order_link(session_id, tx_id, merchant_id, cart_digest, saga_state, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(tx_id) DO UPDATE SET saga_state=excluded.saga_state,"
                " updated_at=excluded.updated_at", (sid, tx_id, merchant_id, cart_digest, saga_state, now, now))

    def open_orders(self, sid: str | None = None) -> list[dict[str, Any]]:
        rows = self._r("SELECT * FROM order_link ORDER BY updated_at DESC")
        return [dict(r) for r in rows if r["saga_state"] not in TERMINAL_SAGA and (sid is None or r["session_id"] == sid)]

    def snapshot_cart(self, sid: str, merchant_id: str, lines: list[dict[str, Any]]) -> None:
        self._w("INSERT INTO cart_snapshot(session_id, merchant_id, lines_json, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(session_id) DO UPDATE SET merchant_id=excluded.merchant_id,"
                " lines_json=excluded.lines_json, updated_at=excluded.updated_at",
                (sid, merchant_id, json.dumps(lines)[:2000], self.clock()))

    # ---------------------------------------------------------------- retention
    def purge(self, turn_days: int = 30, summary_days: int = 90) -> None:
        now = self.clock()
        self._w("DELETE FROM tool_call WHERE turn_id IN (SELECT turn_id FROM turn WHERE t_start<?)",
                (now - turn_days * 86400,))
        self._w("DELETE FROM turn WHERE t_start<?", (now - turn_days * 86400,))
        self._w("DELETE FROM session WHERE status='ended' AND last_active_at<?", (now - summary_days * 86400,))
        self._w("DELETE FROM order_link WHERE updated_at<?", (now - 365 * 86400,))


_NAME = re.compile(r"\b(?:call me|my name is|i'm called|i am called)\s+([A-Za-z][A-Za-z'-]{1,19})\b", re.I)
_DIET = re.compile(r"\bi(?:'m| am)\s+(vegetarian|vegan|pescatarian|gluten[- ]free|lactose[- ]intolerant|dairy[- ]free)\b", re.I)
_USUAL = re.compile(r"\bmy usual (coffee|drink|order|lunch|breakfast) is (?:a |an |the )?([a-z][a-z \-']{2,40})", re.I)
_FORGET = re.compile(r"\bforget (?:all )?(?:my )?(?:preferences|what you know about me)\b", re.I)


def extract_prefs(text: str) -> dict[str, str]:
    """Explicit statements only; nothing is inferred."""
    out: dict[str, str] = {}
    if m := _NAME.search(text):
        out["name"] = m.group(1).capitalize()
    if m := _DIET.search(text):
        out["diet"] = m.group(1).lower()
    if m := _USUAL.search(text):
        out[f"usual {m.group(1).lower()}"] = m.group(2).strip().lower()
    return out


def is_forget(text: str) -> bool:
    return bool(_FORGET.search(text))
