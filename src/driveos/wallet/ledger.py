"""Append-only, hash-chained, device-signed transaction ledger (SQLite).

Every money-relevant event (consent, authorisation, capture, void, refund, order state) is an entry.
Entries cannot be updated or deleted (enforced by triggers), each entry hashes its predecessor, and each
hash is signed with the vehicle key. `verify()` recomputes the chain; any edit breaks it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.ids import utcnow
from ..core.keys import KeyStore
from ..core.private_fs import ensure_private_dir, private_file
from ..domain.errors import LedgerTampered

GENESIS = "0" * 64

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS entries (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  tx_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE,
  sig TEXT NOT NULL,
  key_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_tx ON entries(tx_id);
CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries
  BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS transactions (
  tx_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  occupant_id TEXT NOT NULL,
  payer_id TEXT NOT NULL,
  merchant_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  total_minor INTEGER NOT NULL,
  currency TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT '',
  snapshot TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tx_state ON transactions(state);
"""

TERMINAL_STATES = {"completed", "abandoned", "declined", "voided", "refunded", "failed"}
SPENDING_STATES = {"authorised", "placed", "accepted", "captured", "fulfilled", "completed"}


@dataclass(frozen=True)
class Entry:
    seq: int
    ts: str
    tx_id: str
    kind: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _entry_hash(prev_hash: str, ts: str, tx_id: str, kind: str, payload_json: str) -> str:
    h = hashlib.sha256()
    for part in (prev_hash, ts, tx_id, kind, payload_json):
        h.update(part.encode())
        h.update(b"\x1f")
    return h.hexdigest()


class Ledger:
    def __init__(self, path: Path, keys: KeyStore):
        self.path = path
        self.keys = keys
        ensure_private_dir(path.parent)
        private_file(path, create=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._db.executescript(_SCHEMA)
        private_file(path)

    # ------------------------------------------------------------------ writes
    def append(self, tx_id: str, kind: str, payload: dict[str, Any]) -> Entry:
        ts = utcnow().isoformat(timespec="milliseconds")
        payload_json = _canonical(payload)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT hash FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
                prev = row["hash"] if row else GENESIS
                h = _entry_hash(prev, ts, tx_id, kind, payload_json)
                sig = self.keys.sign(bytes.fromhex(h)).hex()
                cur = self._db.execute(
                    "INSERT INTO entries(ts,tx_id,kind,payload,prev_hash,hash,sig,key_id) VALUES (?,?,?,?,?,?,?,?)",
                    (ts, tx_id, kind, payload_json, prev, h, sig, self.keys.key_id),
                )
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return Entry(cur.lastrowid, ts, tx_id, kind, payload, prev, h)

    def upsert_transaction(
        self, tx_id: str, *, state: str, occupant_id: str, payer_id: str, merchant_id: str, provider: str,
        total_minor: int, currency: str, tier: str, snapshot: dict[str, Any],
    ) -> None:
        now = utcnow().isoformat(timespec="milliseconds")
        with self._lock:
            self._db.execute(
                """INSERT INTO transactions(tx_id,state,occupant_id,payer_id,merchant_id,provider,total_minor,currency,
                                            tier,snapshot,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tx_id) DO UPDATE SET state=excluded.state, total_minor=excluded.total_minor,
                     tier=excluded.tier, snapshot=excluded.snapshot, updated_at=excluded.updated_at""",
                (tx_id, state, occupant_id, payer_id, merchant_id, provider, total_minor, currency, tier,
                 _canonical(snapshot), now, now),
            )

    # ------------------------------------------------------------------ reads
    def entries(self, tx_id: str | None = None) -> list[Entry]:
        q = "SELECT * FROM entries" + (" WHERE tx_id=?" if tx_id else "") + " ORDER BY seq"
        rows = self._db.execute(q, (tx_id,) if tx_id else ()).fetchall()
        return [Entry(r["seq"], r["ts"], r["tx_id"], r["kind"], json.loads(r["payload"]), r["prev_hash"], r["hash"])
                for r in rows]

    def open_transactions(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            f"SELECT * FROM transactions WHERE state NOT IN ({','.join('?' * len(TERMINAL_STATES))})",
            tuple(TERMINAL_STATES),
        ).fetchall()
        return [dict(r) | {"snapshot": json.loads(r["snapshot"])} for r in rows]

    def transaction(self, tx_id: str) -> dict[str, Any] | None:
        r = self._db.execute("SELECT * FROM transactions WHERE tx_id=?", (tx_id,)).fetchone()
        return dict(r) | {"snapshot": json.loads(r["snapshot"])} if r else None

    def recent_transactions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM transactions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"snapshot": json.loads(r["snapshot"])} for r in rows]

    def spent_since(self, occupant_id: str, since: datetime, *, tier: str | None = None) -> int:
        q = (f"SELECT COALESCE(SUM(total_minor),0) AS s FROM transactions WHERE occupant_id=? AND created_at>=? "
             f"AND state IN ({','.join('?' * len(SPENDING_STATES))})")
        args: list[Any] = [occupant_id, since.isoformat(timespec="milliseconds"), *SPENDING_STATES]
        if tier:
            q += " AND tier=?"
            args.append(tier)
        return int(self._db.execute(q, args).fetchone()["s"])

    def spent_today(self, occupant_id: str, *, tier: str | None = None) -> int:
        now = datetime.now(UTC)
        return self.spent_since(occupant_id, now - timedelta(hours=24), tier=tier)

    def has_completed_with(self, occupant_id: str, merchant_id: str) -> bool:
        r = self._db.execute(
            "SELECT 1 FROM transactions WHERE occupant_id=? AND merchant_id=? AND state IN ('captured','completed') LIMIT 1",
            (occupant_id, merchant_id),
        ).fetchone()
        return r is not None

    # ------------------------------------------------------------------ integrity
    def verify(self) -> int:
        """Recompute the hash chain and signatures. Returns entry count or raises LedgerTampered."""
        prev = GENESIS
        n = 0
        for r in self._db.execute("SELECT * FROM entries ORDER BY seq"):
            if r["prev_hash"] != prev:
                raise LedgerTampered(f"chain break at seq {r['seq']}")
            h = _entry_hash(r["prev_hash"], r["ts"], r["tx_id"], r["kind"], r["payload"])
            if h != r["hash"]:
                raise LedgerTampered(f"hash mismatch at seq {r['seq']}")
            if r["key_id"] == self.keys.key_id and not self.keys.verify(bytes.fromhex(h), bytes.fromhex(r["sig"])):
                raise LedgerTampered(f"bad signature at seq {r['seq']}")
            prev = h
            n += 1
        return n

    def close(self) -> None:
        with self._lock:
            self._db.close()
