"""Vehicle signing identity.

`FileKeyStore` keeps an Ed25519 key on disk (0600). On production hardware, implement `KeyStore`
against the secure element / TPM / PKCS#11 HSM so the private key never leaves hardware — callers only
need `sign()` and `public_key_pem()`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .private_fs import ensure_private_dir


class KeyStore(Protocol):
    def sign(self, data: bytes) -> bytes: ...
    def verify(self, data: bytes, signature: bytes) -> bool: ...
    def public_key_pem(self) -> str: ...
    @property
    def key_id(self) -> str: ...


class FileKeyStore:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self._key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(self._key, Ed25519PrivateKey):
                raise ValueError("device key must be Ed25519")
        else:
            ensure_private_dir(path.parent)
            self._key = Ed25519PrivateKey.generate()
            pem = self._key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
        self._pub: Ed25519PublicKey = self._key.public_key()
        raw = self._pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self._kid = raw.hex()[:16]

    @property
    def key_id(self) -> str:
        return self._kid

    def sign(self, data: bytes) -> bytes:
        return self._key.sign(data)

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self._pub.verify(signature, data)
            return True
        except InvalidSignature:
            return False

    def public_key_pem(self) -> str:
        return self._pub.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
