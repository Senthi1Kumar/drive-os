"""Encrypted voice template store: Fernet (cryptography) with a 0600 key file under the biometrics home."""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

from ..core.private_fs import ensure_private_dir


@dataclass
class VoiceTemplate:
    driver_id: str
    name: str
    model: str
    template: np.ndarray                 # mean of L2-normed embeddings, re-normed
    embeddings: np.ndarray               # (n, dim) kept per-utterance embeddings
    consent: bool
    calibration: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


class TemplateStore:
    def __init__(self, home: Path):
        self.home = home
        self.key_path = home / ".template_key"

    def _fernet(self) -> Fernet:
        ensure_private_dir(self.home)
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
        return Fernet(self.key_path.read_bytes())

    def path(self, driver_id: str) -> Path:
        return self.home / "voice" / f"{driver_id}.tpl"

    def exists(self, driver_id: str) -> bool:
        return self.path(driver_id).is_file()

    def save(self, t: VoiceTemplate) -> Path:
        buf = io.BytesIO()
        meta = {"driver_id": t.driver_id, "name": t.name, "model": t.model, "consent": t.consent,
                "calibration": t.calibration, "stats": t.stats}
        np.savez(buf, template=t.template, embeddings=t.embeddings, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))
        p = self.path(t.driver_id)
        ensure_private_dir(p.parent)
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self._fernet().encrypt(buf.getvalue()))
        os.replace(tmp, p)
        return p

    def load(self, driver_id: str) -> VoiceTemplate | None:
        p = self.path(driver_id)
        if not p.is_file():
            return None
        try:
            raw = self._fernet().decrypt(p.read_bytes())
        except InvalidToken:
            return None
        z = np.load(io.BytesIO(raw))
        meta = json.loads(bytes(z["meta"]).decode())
        return VoiceTemplate(meta["driver_id"], meta["name"], meta["model"], z["template"], z["embeddings"],
                             bool(meta["consent"]), meta.get("calibration", {}), meta.get("stats", {}))

    def delete(self, driver_id: str) -> None:
        self.path(driver_id).unlink(missing_ok=True)
