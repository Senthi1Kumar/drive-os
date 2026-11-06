"""On-disk thumbnail cache for the HMI.

* Real photos (Square item and brand images) are downloaded in the background once, centre-cropped to a
  square and written as small baseline JPEGs (160 px and 320 px) under ``<root>/img/``; the cache is capped
  and the least recently used files go first.
* Generated food images shipped with the HMI are resampled into ``<root>/food/`` once.

The HMI loads files from ``<root>/<relative path>``; the snapshot only carries relative paths and the root
is sent once in ``hello``. Nothing here blocks the snapshot: ``thumb`` is a dictionary lookup and queues the
download when the file is not there yet.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.private_fs import DIR_MODE, FILE_MODE, ensure_private_dir
from .matcher import KEYS

log = logging.getLogger("driveos.images")

SIZES = (160, 320)
MAX_DOWNLOAD = 3_000_000
JPEG_QUALITY = 72
RETRY_AFTER_S = 600.0
TOUCH_AFTER_S = 600.0

Fetch = Callable[[str], Awaitable[bytes]]


@dataclass(frozen=True)
class Thumb:
    small: str   # relative to the image root, 160 px
    large: str   # 320 px


def _square_jpeg(data: bytes, size: int) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as im:
        im.load()
        im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    im = im.resize((size, size), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=False)
    return out.getvalue()


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    os.chmod(path, FILE_MODE)


class ImageStore:
    def __init__(self, root: Path, *, generated_dir: Path | None = None, max_bytes: int = 64 * 1024 * 1024,
                 fetch: Fetch | None = None, max_parallel: int = 3, on_ready: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.root = Path(root)
        self.generated_dir = Path(generated_dir) if generated_dir else None
        self.max_bytes = max_bytes
        self.fetch = fetch
        self.on_ready = on_ready
        self._clock = clock
        self._gate: asyncio.Semaphore | None = None
        self._max_parallel = max_parallel
        self._ready: dict[str, Thumb] = {}
        self._queued: set[str] = set()
        self._failed: dict[str, float] = {}
        self._touched: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._gen: dict[str, Thumb] = {}
        ensure_private_dir(self.root)
        self._img = ensure_private_dir(self.root / "img")
        self._food = ensure_private_dir(self.root / "food")
        os.chmod(self.root, DIR_MODE)

    # ------------------------------------------------------------------ generated images
    def generated(self, key: str) -> Thumb | None:
        if key in self._gen:
            t = self._gen[key]
            if self._present(t):
                return t
            del self._gen[key]       # the cache directory was cleaned under us: rebuild, or report nothing
        if self.generated_dir is None:
            return None
        src = self.generated_dir / f"{key}.jpg"
        if not src.is_file():
            return None
        names = {s: f"food/{key}_{s}.jpg" for s in SIZES}
        try:
            data = None
            for s, rel in names.items():
                dst = self.root / rel
                if dst.is_file() and dst.stat().st_size > 0 and dst.stat().st_mtime >= src.stat().st_mtime:
                    continue
                data = data or src.read_bytes()
                _write(dst, _square_jpeg(data, s))
        except Exception:
            log.exception("generated image %s", key)
            return None
        self._gen[key] = Thumb(names[SIZES[0]], names[SIZES[1]])
        return self._gen[key]

    def _present(self, t: Thumb) -> bool:
        """Both files exist and are not empty: a path the HMI cannot open must never be sent."""
        try:
            return all((self.root / rel).stat().st_size > 0 for rel in (t.small, t.large))
        except OSError:
            return False

    def prepare_generated(self) -> int:
        return sum(1 for k in KEYS if self.generated(k))

    # ------------------------------------------------------------------ real photos
    @staticmethod
    def _id(url: str) -> str:
        return hashlib.sha1(url.encode()).hexdigest()[:12]

    def _paths(self, url: str) -> Thumb:
        i = self._id(url)
        return Thumb(f"img/{i}_{SIZES[0]}.jpg", f"img/{i}_{SIZES[1]}.jpg")

    def thumb(self, url: str | None) -> Thumb | None:
        """The cached thumbnail for ``url`` if it is on disk; otherwise queue the download and return None."""
        if not url or not url.startswith("https://"):
            return None
        t = self._ready.get(url)
        if t is not None and not self._present(t):
            self._ready.pop(url, None)
            t = None
        if t is None:
            p = self._paths(url)
            if (self.root / p.small).is_file() and (self.root / p.large).is_file():
                t = self._ready[url] = p
        if t is not None:
            now = self._clock()
            if now - self._touched.get(url, -1e9) > TOUCH_AFTER_S:
                self._touched[url] = now
                for rel in (t.small, t.large):
                    try:
                        os.utime(self.root / rel)
                    except OSError:
                        pass
            return t
        self.want(url)
        return None

    def want(self, url: str) -> None:
        if self.fetch is None or url in self._queued:
            return
        if self._clock() - self._failed.get(url, -1e9) < RETRY_AFTER_S:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._queued.add(url)
        task = loop.create_task(self._download(url))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _download(self, url: str) -> None:
        try:
            if self._gate is None:
                self._gate = asyncio.Semaphore(self._max_parallel)
            async with self._gate:
                data = await self.fetch(url)
                if len(data) > MAX_DOWNLOAD:
                    raise ValueError("image too large")
                p = self._paths(url)
                for s, rel in zip(SIZES, (p.small, p.large), strict=True):
                    jpg = await asyncio.to_thread(_square_jpeg, data, s)
                    _write(self.root / rel, jpg)
            self._ready[url] = p
            await asyncio.to_thread(self.evict)
            if self.on_ready:
                self.on_ready()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed[url] = self._clock()
            log.info("image %s: %s", type(e).__name__, str(e)[:80])
        finally:
            self._queued.discard(url)

    async def settle(self) -> None:
        """Wait for queued downloads (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------ size cap
    def size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self._img.glob("*.jpg"))

    def evict(self) -> int:
        """Delete least-recently-used cached photos until the cache fits ``max_bytes``. Returns files removed."""
        files = []
        for f in self._img.glob("*.jpg"):
            try:
                st = f.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, f))
        total = sum(s for _, s, _ in files)
        removed = 0
        for _, size, f in sorted(files):
            if total <= self.max_bytes:
                break
            try:
                f.unlink()
                total -= size
                removed += 1
            except OSError:
                pass
        if removed:
            live = {u for u, t in self._ready.items() if not (self.root / t.small).is_file()
                    or not (self.root / t.large).is_file()}
            for u in live:
                self._ready.pop(u, None)
        return removed

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()


def http_fetcher(http) -> Fetch:
    """Fetch through the shared ``Http`` (timeouts, redacted logging); no redirects to other hosts."""
    async def fetch(url: str) -> bytes:
        r = await http.request("GET", url, service="images", retries=1)
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        return r.content
    return fetch
