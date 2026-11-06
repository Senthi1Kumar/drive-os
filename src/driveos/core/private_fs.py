"""Owner-only filesystem helpers for everything that holds personal data (sessions, ledger, templates, logs)."""

from __future__ import annotations

import os
from pathlib import Path

DIR_MODE = 0o700
FILE_MODE = 0o600
_SIDECARS = ("-wal", "-shm", "-journal")
_SKIP_DIRS = {"models", "cache", "__pycache__"}


def tighten_umask() -> None:
    """Files this process creates from now on are owner-only (covers store files written by dependencies)."""
    os.umask(0o077)


def ensure_private_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, DIR_MODE)
    except OSError:
        pass
    return p


def private_file(path: Path | str, *, create: bool = False) -> Path:
    """Make `path` (and SQLite sidecars) 0600. With create=True an empty file is made first, so a database
    opened afterwards, and its -wal/-shm files, inherit the mode."""
    p = Path(path)
    if create and not p.exists():
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT, FILE_MODE))
    for q in (p, *(p.with_name(p.name + s) for s in _SIDECARS)):
        try:
            if q.is_file() and not q.is_symlink():
                os.chmod(q, FILE_MODE)
        except OSError:
            pass
    return p


def write_private_text(path: Path | str, text: str) -> None:
    p = Path(path)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    private_file(p)


def harden_tree(root: Path | str) -> None:
    """Directories 0700, files 0600, under `root` (model and cache folders are not personal and are skipped)."""
    r = Path(root)
    if not r.is_dir():
        return
    try:
        os.chmod(r, DIR_MODE)
    except OSError:
        pass
    for dirpath, dirnames, filenames in os.walk(r):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for d in dirnames:
            try:
                if not os.path.islink(os.path.join(dirpath, d)):
                    os.chmod(os.path.join(dirpath, d), DIR_MODE)
            except OSError:
                pass
        for f in filenames:
            private_file(Path(dirpath) / f)
