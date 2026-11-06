"""Live camera preview for the Face ID sheet.

The F455 streams its colour image over USB video (UVC), separate from the serial authentication session, so the
preview runs while ``authenticate`` runs. Frames are cropped to a centred square, averaged down to a small size and
sent to the HMI as binary WebSocket messages (RGB565). They live in RAM only: nothing here writes a frame, or
anything derived from one, to disk, and nothing leaves the device.

Wire format of one frame (little endian, 24-byte header, then ``width * height * 2`` bytes of RGB565)::

    offset size field
    0      4    magic "DVPV"
    4      1    version (1)
    5      1    flags: bit0 = the face rectangle is valid
    6      2    reserved (0)
    8      4    seq (counts frames since start, wraps at 2**32)
    12     2    width
    14     2    height
    16     8    face rectangle x, y, w, h in preview pixels (u16 each; zeros when bit0 is clear)
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger("driveos.biometrics.preview")

MAGIC = b"DVPV"
VERSION = 1
FLAG_RECT = 1
_HEADER = struct.Struct("<4sBBHIHHHHHH")
HEADER_SIZE = _HEADER.size
# the F455 reports face rectangles in its 1080 x 1920 portrait frame whatever the preview resolution
SENSOR_REF = (1080, 1920)
RECT_FRESH_S = 0.6
RECT_QUANTUM = 0.02


@dataclass(frozen=True)
class FrameHeader:
    seq: int
    width: int
    height: int
    rect: tuple[int, int, int, int] | None
    payload_len: int


def pack_frame(seq: int, width: int, height: int, rgb565: bytes,
               rect: tuple[int, int, int, int] | None = None) -> bytes:
    x, y, w, h = (int(v) & 0xFFFF for v in rect) if rect else (0, 0, 0, 0)
    head = _HEADER.pack(MAGIC, VERSION, FLAG_RECT if rect else 0, 0, seq & 0xFFFFFFFF, width, height, x, y, w, h)
    return head + rgb565


def parse_header(buf: bytes) -> FrameHeader | None:
    """The header of a frame, or None when the bytes are not a whole version-1 frame."""
    if len(buf) < HEADER_SIZE:
        return None
    magic, ver, flags, _r, seq, w, h, rx, ry, rw, rh = _HEADER.unpack_from(buf)
    if magic != MAGIC or ver != VERSION or len(buf) - HEADER_SIZE < w * h * 2:
        return None
    return FrameHeader(seq, w, h, (rx, ry, rw, rh) if flags & FLAG_RECT else None, len(buf) - HEADER_SIZE)


def rgb_to_565(rgb: np.ndarray) -> bytes:
    r, g, b = (rgb[..., i].astype(np.uint16) for i in range(3))
    return (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)).astype("<u2").tobytes()


def _geometry(width: int, height: int, size: int) -> tuple[int, int, int, int]:
    """(output size, source pixels per output pixel, crop x0, crop y0) for a centred square crop."""
    side = min(width, height)
    out = min(size, side)
    step = max(1, side // out)
    crop = out * step
    return out, step, (width - crop) // 2, (height - crop) // 2


def square_downscale(rgb: np.ndarray, size: int) -> np.ndarray:
    """Centred square crop, area-averaged to ``size`` x ``size`` (never upscaled)."""
    h, w = rgb.shape[:2]
    out, step, x0, y0 = _geometry(w, h, size)
    block = rgb[y0:y0 + out * step, x0:x0 + out * step, :3]
    if step == 1:
        return np.ascontiguousarray(block)
    total = block.reshape(out, step, out, step, 3).sum(axis=(1, 3), dtype=np.uint32)
    return (total // (step * step)).astype(np.uint8)


def map_face_rect(rect: tuple[int, int, int, int] | None, src_w: int, src_h: int, size: int, *,
                  ref: tuple[int, int] = SENSOR_REF, mirror: bool = True) -> tuple[int, int, int, int] | None:
    """A face rectangle in sensor-frame pixels -> preview pixels (same crop, scale and mirroring as the image)."""
    if not rect or rect[2] <= 0 or rect[3] <= 0 or src_w <= 0 or src_h <= 0:
        return None
    out, step, x0, y0 = _geometry(src_w, src_h, size)
    sx, sy = src_w / ref[0], src_h / ref[1]
    x, y, w, h = rect
    left, top = (x * sx - x0) / step, (y * sy - y0) / step
    right, bottom = left + w * sx / step, top + h * sy / step
    left, top, right, bottom = max(0.0, left), max(0.0, top), min(float(out), right), min(float(out), bottom)
    if right - left < 1 or bottom - top < 1:
        return None
    if mirror:
        left, right = out - right, out - left
    return round(left), round(top), max(1, round(right - left)), max(1, round(bottom - top))


class PreviewHub:
    """The one place the HMI-facing face state lives: state, the last face rectangle, and the frame fan-out.

    Thread-safe: the SDK delivers frames and results on its own threads. State listeners and frame sinks are called
    on the calling thread, so a sink that needs the event loop must hand over with ``call_soon_threadsafe``."""

    def __init__(self, *, fps: float = 12.0, size: int = 240, mirror: bool = True, enabled: bool = True,
                 clock: Callable[[], float] = time.monotonic):
        self.fps, self.size, self.mirror, self.enabled = fps, size, mirror, enabled
        self._clock = clock
        self._lock = threading.Lock()
        self._state_listeners: list[Callable[[], None]] = []
        self._sinks: list[Callable[[bytes], None]] = []
        self._face_state = "none"
        self._name: str | None = None
        self._live = False
        self._seq = 0
        self._last_t = -1e9
        self._rect_src: tuple[tuple[int, int, int, int], int, int, float] | None = None
        self._rect_norm: dict[str, float] | None = None
        self.rect_px: tuple[int, int, int, int] | None = None
        self.last_frame: bytes | None = None

    def configure(self, *, enabled: bool, fps: float, size: int, mirror: bool = True) -> None:
        self.enabled, self.fps, self.size, self.mirror = enabled, fps, size, mirror

    # ---- listeners
    def add_state_listener(self, cb: Callable[[], None]) -> None:
        self._state_listeners.append(cb)

    def remove_state_listener(self, cb: Callable[[], None]) -> None:
        if cb in self._state_listeners:
            self._state_listeners.remove(cb)

    def add_frame_sink(self, cb: Callable[[bytes], None]) -> None:
        self._sinks.append(cb)

    def remove_frame_sink(self, cb: Callable[[bytes], None]) -> None:
        if cb in self._sinks:
            self._sinks.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._state_listeners):
            try:
                cb()
            except Exception:
                log.debug("preview state listener failed", exc_info=True)

    # ---- face state
    def begin_scan(self) -> None:
        with self._lock:
            if self._face_state == "scanning":
                return
            self._face_state, self._name = "scanning", None
            self._rect_src = self._rect_norm = self.rect_px = None
            self.last_frame = None
        self._notify()

    def finish(self, verified: bool, name: str | None = None) -> None:
        """The scan's outcome. Ignored unless a scan is in progress (a late result after a cancel is dropped)."""
        with self._lock:
            if self._face_state != "scanning":
                return
            self._face_state = "verified" if verified else "failed"
            self._name = (name or None) if verified else None
        self._notify()

    def reset(self) -> None:
        with self._lock:
            changed = self._face_state != "none" or self.last_frame is not None
            self._face_state, self._name = "none", None
            self._rect_src = self._rect_norm = self.rect_px = None
            self.last_frame = None
        if changed:
            self._notify()

    def source_started(self) -> None:
        with self._lock:
            self._live = True
        self._notify()

    def source_stopped(self) -> None:
        with self._lock:
            was, self._live = self._live, False
        if was:
            self._notify()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            frozen = self._face_state in ("verified", "failed")
            fresh = self._rect_src is not None and self._clock() - self._rect_src[3] <= RECT_FRESH_S
            rect = self._rect_norm if (fresh or frozen) else None
            return {"preview": self._live, "face_state": self._face_state, "face_rect": dict(rect) if rect else None,
                    "face_name": self._name}

    # ---- frames
    def set_faces(self, faces: list[Any], src_w: int = 0, src_h: int = 0) -> None:
        """Detected faces from the SDK (objects with x, y, w, h in sensor-frame pixels): follow the largest. The
        preview frame size is the current frame's unless given."""
        best = None
        for f in faces or []:
            try:
                r = (int(f.x), int(f.y), int(f.w), int(f.h))
            except (AttributeError, TypeError, ValueError):
                continue
            if best is None or r[2] * r[3] > best[2] * best[3]:
                best = r
        with self._lock:
            self._rect_src = (best, src_w, src_h, self._clock()) if best else None
            if best is None:
                self._rect_norm = self.rect_px = None

    def want_frame(self) -> bool:
        # 0.9 of the interval, so a camera running at exactly fps is not thinned to every other frame by jitter
        return self.enabled and self._clock() - self._last_t >= 0.9 / max(self.fps, 0.1)

    def feed(self, rgb: np.ndarray) -> bool:
        """One camera frame (H x W x 3, RGB). Rate-limited; returns whether a frame was sent."""
        if not self.want_frame():
            return False
        self._last_t = self._clock()
        h, w = rgb.shape[:2]
        sq = square_downscale(rgb, self.size)
        if self.mirror:
            sq = sq[:, ::-1]
        size = sq.shape[0]
        with self._lock:
            src = self._rect_src
            rect = None
            if src is not None and self._clock() - src[3] <= RECT_FRESH_S:
                rect = map_face_rect(src[0], src[1] or w, src[2] or h, self.size, mirror=self.mirror)
            self.rect_px = rect
            self._rect_norm = None if rect is None else {
                k: min(1.0, max(0.0, round(round(v / size / RECT_QUANTUM) * RECT_QUANTUM, 2)))
                for k, v in zip("xywh", rect, strict=True)}
            self._seq += 1
            seq = self._seq
        frame = pack_frame(seq, size, size, rgb_to_565(sq), rect)
        self.last_frame = frame
        for sink in list(self._sinks):
            try:
                sink(frame)
            except Exception:
                log.debug("preview frame sink failed", exc_info=True)
        return True


class SdkPreview:
    """``rsid_py.Preview`` pushing frames into a hub. ``start`` is best effort: no preview support in the SDK build,
    no video node or a busy camera mean no preview, never a failed scan."""

    def __init__(self, sdk: Any, device: Any, hub: PreviewHub):
        self.sdk, self.device, self.hub = sdk, device, hub
        self._preview: Any = None

    def start(self) -> bool:
        cls = getattr(self.sdk, "Preview", None)
        if cls is None:
            log.info("this rsid_py build has no Preview (rebuild the SDK with -DRSID_PREVIEW=ON)")
            return False
        try:
            cfg = self.sdk.PreviewConfig()
            cfg.device_type = self.device.device_type
            cfg.preview_mode = self.sdk.PreviewMode.MJPEG_720P
            cfg.portrait_mode = True
            p = cls(cfg)
            p.start(self._on_image, None)
        except Exception as e:
            log.info("F455 preview did not start: %s", e)
            return False
        self._preview = p
        self.hub.source_started()
        return True

    def stop(self) -> None:
        p, self._preview = self._preview, None
        if p is None:
            return
        try:
            p.stop()
        except Exception:
            log.debug("F455 preview stop failed", exc_info=True)
        self.hub.source_stopped()

    def _on_image(self, image: Any) -> None:
        try:
            if not self.hub.want_frame():
                return
            w, h, stride = int(image.width), int(image.height), int(image.stride)
            flat = np.frombuffer(image.get_buffer(), dtype=np.uint8)
            rgb = flat[:h * stride].reshape(h, stride)[:, :w * 3].reshape(h, w, 3)
            self.hub.feed(rgb)
        except Exception:
            log.debug("preview frame dropped", exc_info=True)


HUB = PreviewHub()
