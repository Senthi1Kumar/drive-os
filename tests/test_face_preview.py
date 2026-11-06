"""Live F455 preview for the checkout sheet: frame encoding, the hub's face state, SDK start/stop around a scan,
the driver's display name, the snapshot fields and the binary WS frames. RAM only; no real camera."""

from __future__ import annotations

import asyncio
import enum
import json
import struct
import threading

import numpy as np
import pytest
from websockets.asyncio.client import connect

from driveos.biometrics import preview as pv
from driveos.biometrics.f455 import F455Client, F455Rung, FaceUserMap, driver_display_name
from driveos.biometrics.preview import PreviewHub

# ---------------------------------------------------------------- encoding


def test_header_layout_is_24_bytes_little_endian():
    assert pv.HEADER_SIZE == 24
    buf = pv.pack_frame(7, 4, 2, b"\x00" * 16, rect=(1, 0, 3, 2))
    assert len(buf) == 24 + 16
    magic, ver, flags, _r, seq, w, h, rx, ry, rw, rh = struct.unpack("<4sBBHIHHHHHH", buf[:24])
    assert (magic, ver, flags, seq, w, h, (rx, ry, rw, rh)) == (b"DVPV", 1, 1, 7, 4, 2, (1, 0, 3, 2))


GOLDEN_HEADER = "445650560101" "0000" "04030201" "f000" "f000" "0100" "0200" "0300" "0400"


def test_golden_header_bytes_match_the_c_parser_test():
    # hmi_lvgl/tests/test_preview_proto.c parses these very bytes
    buf = pv.pack_frame(0x01020304, 240, 240, b"", rect=(1, 2, 3, 4))
    assert buf.hex() == GOLDEN_HEADER


def test_header_without_rect_clears_the_flag():
    buf = pv.pack_frame(1, 2, 2, b"\x00" * 8)
    assert buf[5] == 0 and pv.parse_header(buf).rect is None


def test_parse_header_roundtrip_and_rejects_garbage():
    buf = pv.pack_frame(9, 3, 2, b"\x01" * 12, rect=(0, 1, 2, 1))
    h = pv.parse_header(buf)
    assert (h.seq, h.width, h.height, h.rect, h.payload_len) == (9, 3, 2, (0, 1, 2, 1), 12)
    assert pv.parse_header(buf[:10]) is None
    assert pv.parse_header(b"XXXX" + buf[4:]) is None
    assert pv.parse_header(buf[:-1]) is None            # payload shorter than the header says
    bad = bytearray(buf)
    bad[4] = 2                                          # unknown version
    assert pv.parse_header(bytes(bad)) is None


def test_rgb565_little_endian_values():
    rgb = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]]], dtype=np.uint8)
    out = np.frombuffer(pv.rgb_to_565(rgb), dtype="<u2")
    assert out.tolist() == [0xF800, 0x07E0, 0x001F, 0xFFFF]


def test_square_downscale_is_a_centred_area_average():
    img = np.zeros((12, 6, 3), dtype=np.uint8)            # portrait 6 wide, 12 tall
    img[3:9, :, 0] = 200                                   # the centre 6x6 block is red
    out = pv.square_downscale(img, 3)
    assert out.shape == (3, 3, 3) and (out[:, :, 0] == 200).all() and (out[:, :, 1:] == 0).all()
    avg = pv.square_downscale(np.tile(np.array([[10, 30]], dtype=np.uint8)[:, :, None], (4, 2, 3))[:, :, :], 1)
    assert avg.shape == (1, 1, 3) and int(avg[0, 0, 0]) == 20


def test_square_downscale_never_upscales():
    assert pv.square_downscale(np.zeros((8, 4, 3), dtype=np.uint8), 240).shape == (4, 4, 3)


# ---------------------------------------------------------------- face rectangle mapping (sensor 1080x1920 portrait)
def test_rect_in_the_centre_maps_into_the_square():
    # 540x960 frame is half the 1080x1920 reference; a 400x400 rect centred at (540, 960) is 200 px wide there
    r = pv.map_face_rect((340, 760, 400, 400), 540, 960, 100, mirror=False)
    # the crop is 500x500 (5 source pixels per preview pixel), so the rect is 200 / 5 = 40 px wide
    assert r is not None
    x, y, w, h = r
    assert (w, h) == (40, 40)
    assert abs((x + w / 2) - 50) <= 1 and abs((y + h / 2) - 50) <= 1


def test_rect_is_mirrored_with_the_image():
    plain = pv.map_face_rect((100, 760, 300, 300), 1080, 1920, 108, mirror=False)
    mirrored = pv.map_face_rect((100, 760, 300, 300), 1080, 1920, 108, mirror=True)
    assert plain and mirrored and abs(mirrored[0] - (108 - plain[0] - plain[2])) <= 1
    assert mirrored[1:] == plain[1:]


def test_rect_outside_the_crop_is_dropped_and_partial_is_clamped():
    assert pv.map_face_rect((0, 0, 200, 200), 1080, 1920, 100, mirror=False) is None     # above the centre square
    x, y, w, h = pv.map_face_rect((900, 700, 400, 400), 1080, 1920, 108, mirror=False)
    assert x + w <= 108 and y >= 0 and w > 0 and h > 0


def test_degenerate_rect_is_none():
    assert pv.map_face_rect((10, 10, 0, 40), 1080, 1920, 100) is None
    assert pv.map_face_rect(None, 1080, 1920, 100) is None


# ---------------------------------------------------------------- the hub
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeRect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h


def img(w=48, h=96, v=50):
    return np.full((h, w, 3), v, dtype=np.uint8)


def test_hub_rate_limits_frames_and_numbers_them():
    clk = Clock()
    hub = PreviewHub(fps=10, size=24, clock=clk)
    got: list[bytes] = []
    hub.add_frame_sink(got.append)
    assert hub.want_frame()
    assert hub.feed(img()) is True
    clk.t = 0.05
    assert not hub.want_frame() and hub.feed(img()) is False
    clk.t = 0.11
    assert hub.feed(img()) is True
    assert [pv.parse_header(b).seq for b in got] == [1, 2]
    h = pv.parse_header(got[0])
    assert (h.width, h.height, h.payload_len) == (24, 24, 24 * 24 * 2)


def test_hub_face_state_machine_and_snapshot():
    hub = PreviewHub()
    assert hub.snapshot() == {"preview": False, "face_state": "none", "face_rect": None, "face_name": None}
    hub.begin_scan()
    assert hub.snapshot()["face_state"] == "scanning"
    hub.finish(True, "alex")
    s = hub.snapshot()
    assert s["face_state"] == "verified" and s["face_name"] == "alex"
    hub.begin_scan()
    assert hub.snapshot()["face_name"] is None
    hub.finish(False, "ignored")
    s = hub.snapshot()
    assert s["face_state"] == "failed" and s["face_name"] is None       # a name only goes with a match
    hub.reset()
    assert hub.snapshot()["face_state"] == "none"


def test_hub_snapshot_reports_preview_while_frames_flow_and_normalised_rect():
    hub = PreviewHub(fps=1000, size=100)
    hub.begin_scan()
    hub.source_started()
    hub.set_faces([FakeRect(340, 760, 400, 400)], 540, 960)
    hub.feed(img(540, 960))
    s = hub.snapshot()
    assert s["preview"] is True
    r = s["face_rect"]
    assert set(r) == {"x", "y", "w", "h"} and all(0.0 <= r[k] <= 1.0 for k in r)
    hub.source_stopped()
    assert hub.snapshot()["preview"] is False


def test_hub_notifies_listeners_on_state_changes_not_per_frame():
    hub = PreviewHub(fps=1000, size=8)
    calls = []
    hub.add_state_listener(lambda: calls.append(1))
    hub.begin_scan()
    hub.source_started()
    n = len(calls)
    for _ in range(5):
        hub.feed(img(16, 32))
    assert len(calls) == n
    hub.finish(True, "a")
    assert len(calls) == n + 1


def test_sink_failure_does_not_break_the_hub():
    hub = PreviewHub(fps=1000, size=8)
    hub.add_frame_sink(lambda b: 1 / 0)
    ok: list[bytes] = []
    hub.add_frame_sink(ok.append)
    assert hub.feed(img(16, 32)) is True and len(ok) == 1


def test_last_frame_is_kept_in_ram_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    hub = PreviewHub(fps=1000, size=8)
    hub.feed(img(16, 32))
    assert hub.last_frame is not None
    assert list(tmp_path.rglob("*")) == []


# ---------------------------------------------------------------- the SDK around a scan (fake rsid_py)
class St(enum.Enum):
    Success = 1
    Forbidden = 2


class Mode(enum.Enum):
    MJPEG_1080P = 0
    MJPEG_720P = 1


class Cfg:
    device_type = None
    preview_mode = None
    portrait_mode = False
    camera_number = -1


class FakeImage:
    def __init__(self, w=36, h=64):
        self.width, self.height, self.stride = w, h, w * 3
        self.size = w * 3 * h
        self._b = bytes([90]) * self.size
        self.number = 0

    def get_buffer(self):
        return memoryview(self._b)


EVENTS: list[str] = []


class FakePreview:
    instances: list[FakePreview] = []
    fail_start = False

    def __init__(self, cfg):
        self.cfg, self.cb = cfg, None
        FakePreview.instances.append(self)

    def start(self, cb, snap=None):
        if FakePreview.fail_start:
            raise RuntimeError("StartPreview failed")
        EVENTS.append("preview_start")
        self.cb = cb

    def stop(self):
        EVENTS.append("preview_stop")


class FakeAuth:
    script = (St.Success, "alice_1")
    block = False
    raises: str | None = None
    faces = None
    late = False                     # deliver the verdict only after a cancel/timeout released the call
    gate = threading.Event()

    def __init__(self, *a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cancel(self):
        FakeAuth.gate.set()

    def authenticate(self, on_result=None, on_hint=None, on_faces=None, **kw):
        EVENTS.append("authenticate")
        if FakePreview.instances and FakePreview.instances[-1].cb:
            FakePreview.instances[-1].cb(FakeImage())
        if FakeAuth.faces and on_faces:
            on_faces(FakeAuth.faces, 1)
        if FakeAuth.raises:
            raise RuntimeError(FakeAuth.raises)
        if FakeAuth.block:
            FakeAuth.gate.wait(5)
            if FakeAuth.late:
                on_result(*FakeAuth.script)      # the SDK answers after we already gave up on it
            return
        on_result(*FakeAuth.script)


class FakeSdk:
    PreviewConfig = Cfg
    PreviewMode = Mode
    Preview = FakePreview
    FaceAuthenticator = FakeAuth
    devices = [type("D", (), {"device_type": 1, "serial_port": "/dev/ttyACM0"})()]

    def discover_devices(self):
        return list(self.devices)


class NoPreviewSdk(FakeSdk):
    Preview = None


@pytest.fixture(autouse=True)
def _reset():
    EVENTS.clear()
    FakePreview.instances.clear()
    FakePreview.fail_start = False
    FakeAuth.script, FakeAuth.block, FakeAuth.raises, FakeAuth.faces = (St.Success, "alice_1"), False, None, None
    FakeAuth.late = False
    FakeAuth.gate.clear()


def client(sdk=None, hub=None):
    hub = hub or PreviewHub(fps=1000, size=24)
    return F455Client(sdk=sdk or FakeSdk(), hub=hub), hub


def test_preview_starts_before_authenticate_and_stops_after():
    c, hub = client()
    res = c.authenticate(2.0)
    assert res.matched
    assert EVENTS == ["preview_start", "authenticate", "preview_stop"]
    cfg = FakePreview.instances[0].cfg
    assert cfg.portrait_mode is True and cfg.preview_mode == Mode.MJPEG_720P and cfg.device_type == 1


def test_frames_reach_the_hub_while_scanning():
    c, hub = client()
    got: list[bytes] = []
    hub.add_frame_sink(got.append)
    c.authenticate(2.0)
    assert len(got) == 1 and pv.parse_header(got[0]).width == 24


def test_preview_stops_on_cancel():
    FakeAuth.block = True
    c, hub = client()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", c.authenticate(5.0)))
    t.start()
    for _ in range(100):
        if "authenticate" in EVENTS:
            break
        threading.Event().wait(0.02)
    c.cancel()
    t.join(5)
    assert not out["r"].matched
    assert EVENTS[-1] == "preview_stop" and EVENTS.count("preview_stop") == 1
    assert hub.snapshot()["preview"] is False


def test_preview_stops_when_the_scan_raises():
    FakeAuth.raises = "boom"
    c, hub = client()
    res = c.authenticate(2.0)
    assert not res.available
    assert EVENTS[-1] == "preview_stop"


def test_preview_stops_on_timeout():
    FakeAuth.block = True
    c, hub = client()
    res = c.authenticate(0.2)
    assert res.status == "Timeout" and EVENTS[-1] == "preview_stop"


def test_a_late_result_after_a_timeout_does_not_match():
    FakeAuth.block = True
    FakeAuth.late = True                              # the verdict arrives only after we already gave up
    c, hub = client()
    r = F455Rung(c, "alice_1", 0.2).authenticate()
    assert r.status == "Timeout" and not r.matched
    assert hub.snapshot()["face_state"] == "failed"   # a timed-out scan is a failed scan, never a verified one


def test_a_late_result_after_a_cancel_does_not_match():
    FakeAuth.block = True
    FakeAuth.late = True
    c, hub = client()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", F455Rung(c, "alice_1", 5.0).authenticate()))
    t.start()
    for _ in range(100):
        if "authenticate" in EVENTS:
            break
        threading.Event().wait(0.02)
    c.cancel()
    t.join(5)
    r = out["r"]
    assert not r.matched and r.status != "Success"
    assert hub.snapshot()["face_state"] != "verified"


def test_cancel_waits_for_an_in_flight_result_and_the_outcome_is_then_final(monkeypatch):
    """A result callback that is already being applied when cancel() is called must finish first: cancel()
    returns only when the scan's outcome can no longer change. Otherwise a cancelled scan can still flip to
    matched after cancel() has returned."""
    from driveos.biometrics import f455 as f455_mod

    entered, release = threading.Event(), threading.Event()
    real_status_name = f455_mod.status_name

    def slow_status_name(status):                     # pause the callback after its decision check,
        entered.set()                                 # before it writes the outcome
        release.wait(5)
        return real_status_name(status)

    monkeypatch.setattr(f455_mod, "status_name", slow_status_name)
    FakeAuth.block = True
    FakeAuth.late = True
    c, hub = client()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", F455Rung(c, "alice_1", 5.0).authenticate()))
    t.start()
    for _ in range(100):
        if "authenticate" in EVENTS:
            break
        threading.Event().wait(0.02)
    FakeAuth.gate.set()                                # the device finishes on its own; the callback starts
    assert entered.wait(5)                             # it is now provably between the check and the write
    cancelled = threading.Event()
    ct = threading.Thread(target=lambda: (c.cancel(), cancelled.set()))
    ct.start()
    threading.Event().wait(0.2)
    assert not cancelled.is_set()                      # cancel must wait for the in-flight decision
    release.set()
    ct.join(5)
    t.join(5)
    assert cancelled.is_set()
    assert out["r"].matched                            # the result was already being applied: it stands
    assert hub.snapshot()["face_state"] == "verified"  # ... and it does not change after cancel() returned


def test_scan_works_without_preview_support_in_the_sdk():
    c, hub = client(NoPreviewSdk())
    assert c.authenticate(2.0).matched
    assert EVENTS == ["authenticate"] and hub.snapshot()["preview"] is False


def test_scan_works_when_the_preview_cannot_start():
    FakePreview.fail_start = True
    c, hub = client()
    assert c.authenticate(2.0).matched
    assert "preview_stop" not in EVENTS and hub.snapshot()["preview"] is False


def test_enrolment_does_not_start_a_preview():
    c, hub = client()
    FakeAuth.enroll = lambda self, **kw: kw["on_result"](St.Success)
    c.enroll("carol_3", 1.0)
    assert "preview_start" not in EVENTS


def test_face_rect_is_mapped_into_the_frame_header():
    FakeAuth.faces = [FakeRect(500, 800, 200, 200)]       # near the centre of the 1080x1920 sensor frame
    clk = Clock()
    c, hub = client(hub=PreviewHub(fps=10, size=24, clock=clk))
    got: list[bytes] = []
    hub.add_frame_sink(got.append)
    c.authenticate(2.0)
    clk.t += 1.0                                          # the next frame follows the face the SDK just reported
    hub.set_faces(FakeAuth.faces)
    hub.feed(np.full((64, 36, 3), 90, dtype=np.uint8))
    hdr = pv.parse_header(got[-1])
    assert hdr.rect is not None and hdr.width == 24


def test_scan_begins_and_hub_state_follows_the_rung_result():
    c, hub = client()
    rung = F455Rung(c, "alice_1", 2.0, display_name=lambda: "Alice")
    r = rung.authenticate()
    assert r.matched
    s = hub.snapshot()
    assert s["face_state"] == "verified" and s["face_name"] == "Alice"


def test_other_user_is_a_failed_scan_with_no_name():
    FakeAuth.script = (St.Success, "bob_2")
    c, hub = client()
    F455Rung(c, "alice_1", 2.0, display_name=lambda: "Alice").authenticate()
    s = hub.snapshot()
    assert s["face_state"] == "failed" and s["face_name"] is None


def test_forbidden_is_failed_and_unavailable_device_leaves_no_face_state():
    FakeAuth.script = (St.Forbidden, "")
    c, hub = client()
    F455Rung(c, "alice_1", 2.0).authenticate()
    assert hub.snapshot()["face_state"] == "failed"
    FakeSdk.devices = []
    try:
        c2, hub2 = client()
        F455Rung(c2, "alice_1", 2.0).authenticate()
        assert hub2.snapshot()["face_state"] == "none"
    finally:
        FakeSdk.devices = [type("D", (), {"device_type": 1, "serial_port": "/dev/ttyACM0"})()]


# ---------------------------------------------------------------- the driver's display name
def test_name_comes_from_the_enrolment_name(tmp_path):
    m = FaceUserMap(tmp_path)
    m.set("driver1", "alex_1af812")
    m.set_name("driver1", "Alex")
    assert FaceUserMap(tmp_path).get_name("driver1") == "Alex"
    assert driver_display_name(tmp_path, "driver1", "alex_1af812") == "Alex"
    m.drop("driver1")
    assert m.get_name("driver1") is None


def _voice(tmp_path, name):
    from driveos.biometrics.store import TemplateStore, VoiceTemplate

    TemplateStore(tmp_path).save(VoiceTemplate("driver1", name, "m", np.ones(4, dtype=np.float32),
                                               np.ones((1, 4), dtype=np.float32), True))


def test_name_falls_back_to_the_voice_template_name_when_the_id_has_no_stem(tmp_path):
    _voice(tmp_path, "Asha")
    assert driver_display_name(tmp_path, "driver1", None) == "Asha"
    assert driver_display_name(tmp_path, "driver1", "plainid") == "Asha"


def test_voice_name_keeps_its_case_when_it_matches_the_id_stem(tmp_path):
    _voice(tmp_path, "Asha Rao")
    assert driver_display_name(tmp_path, "driver1", "asharao_00ff2a") == "Asha Rao"


def test_a_generic_voice_name_does_not_hide_the_name_the_face_was_enrolled_under(tmp_path):
    _voice(tmp_path, "Driver")
    assert driver_display_name(tmp_path, "driver1", "alex_1af812") == "alex"


def test_name_falls_back_to_the_user_id_stem_and_never_shows_the_raw_id(tmp_path):
    assert driver_display_name(tmp_path, "driver1", "alex_1af812") == "alex"
    assert driver_display_name(tmp_path, "driver1", "asharao_00ff2a") == "asharao"
    assert driver_display_name(tmp_path, "driver1", "plainid") is None      # not our id shape: no name
    assert driver_display_name(tmp_path, "driver1", None) is None


def test_user_map_names_survive_link_and_set(tmp_path):
    m = FaceUserMap(tmp_path)
    m.set_name("d1", "Asha")
    m.link("d1", "asha_ab12cd")
    m.set("d2", "bob_ab12cd")
    assert m.get_name("d1") == "Asha" and m.get("d1") == "asha_ab12cd" and m.is_linked("asha_ab12cd")
    assert m.get("_names") is None and m.get("_linked") is None       # bookkeeping keys are never a driver


# ---------------------------------------------------------------- snapshot fields (checkout.auth)
async def test_snapshot_fields_follow_the_hub_only_for_the_face_method(settings):
    from tests.test_biometric_checkout import ScriptedVerifier, _auth, _checkout

    s, n, sink = await _checkout(settings, ScriptedVerifier())
    try:
        n.preview = PreviewHub()
        a = _auth(n)
        assert a["preview"] is False and a["face_state"] == "none" and a["face_rect"] is None and a["face_name"] is None
        n.shop.checkout.method = "face"
        n.preview.begin_scan()
        n.preview.source_started()
        a = _auth(n)
        assert a["preview"] is True and a["face_state"] == "scanning" and a["face_name"] is None
        n.preview.finish(True, "alex")
        a = _auth(n)
        assert a["face_state"] == "verified" and a["face_name"] == "alex"
        n.shop.checkout.method = "otp"
        assert _auth(n)["face_state"] == "none"                 # another method: the sheet shows no face panel
    finally:
        await s.aclose()


async def test_no_checkout_still_has_the_fields(settings):
    from tests.test_biometric_checkout import ScriptedVerifier, _auth, _checkout

    s, n, sink = await _checkout(settings, ScriptedVerifier())
    try:
        n.shop.checkout = None
        a = _auth(n)
        assert (a["preview"], a["face_state"], a["face_rect"], a["face_name"]) == (False, "none", None, None)
    finally:
        await s.aclose()


class FaceScripted:
    """A face verifier that reports its result through the hub like F455Rung does."""

    name = "face-f455"
    human_errors = True

    def __init__(self, hub, ok=True):
        self.hub, self.ok, self.cancelled = hub, ok, False
        self.release = asyncio.Event()

    def bind(self, host):
        self.host = host

    def cancel(self):
        self.cancelled = True
        self.release.set()

    async def verify(self, timeout):
        from driveos.domain.models import AuthTier
        from driveos.wallet.auth import AuthResult

        self.hub.begin_scan()
        self.host.set_status("face", None, "Look at the camera")
        await self.release.wait()
        if self.cancelled:
            return AuthResult(AuthTier.T2_STRONG, self.name, False, detail="Cancelled")
        self.hub.finish(self.ok, "alex" if self.ok else None)
        return AuthResult(AuthTier.T2_STRONG, self.name, self.ok, detail="" if self.ok else "Face didn't match")


async def test_verified_state_is_held_on_screen_before_the_order_goes_through(settings):
    from tests.test_biometric_checkout import _auth, _checkout

    hub = PreviewHub()
    v = FaceScripted(hub)
    s, n, sink = await _checkout(settings, v)
    try:
        n.preview, n.verified_hold_s = hub, 0.3
        t = asyncio.ensure_future(n.handle_text("use face id"))
        for _ in range(100):
            if hub.snapshot()["face_state"] == "scanning":
                break
            await asyncio.sleep(0.02)
        seen = set()
        v.release.set()
        while not t.done():
            a = _auth(n)
            seen.add((a["stage"], a["face_state"]))
            await asyncio.sleep(0.02)
        assert ("face", "verified") in seen
        assert n.shop.view == "order" and hub.snapshot()["face_state"] == "none"
    finally:
        await s.aclose()


async def test_failed_face_stays_red_on_the_rejected_sheet_until_retry_or_cancel(settings):
    from tests.test_biometric_checkout import _auth, _checkout

    hub = PreviewHub()
    v = FaceScripted(hub, ok=False)
    s, n, sink = await _checkout(settings, v)
    try:
        n.preview = hub
        t = asyncio.ensure_future(n.handle_text("use face id"))
        for _ in range(100):
            if hub.snapshot()["face_state"] == "scanning":
                break
            await asyncio.sleep(0.02)
        v.release.set()
        await asyncio.wait_for(t, 5)
        a = _auth(n)
        assert a["stage"] == "rejected" and a["face_state"] == "failed" and a["face_name"] is None
        await n.cancel_checkout(quiet=True)
        assert hub.snapshot()["face_state"] == "none"
    finally:
        await s.aclose()


async def test_cancelling_the_checkout_resets_the_face_state(settings):
    from tests.test_biometric_checkout import _checkout

    hub = PreviewHub()
    v = FaceScripted(hub)
    s, n, sink = await _checkout(settings, v)
    try:
        n.preview = hub
        t = asyncio.ensure_future(n.handle_text("use face id"))
        for _ in range(100):
            if hub.snapshot()["face_state"] == "scanning":
                break
            await asyncio.sleep(0.02)
        await asyncio.wait_for(n.handle_text("cancel"), 3)
        assert v.cancelled and hub.snapshot()["face_state"] == "none"
        await asyncio.wait_for(t, 3)
    finally:
        await s.aclose()


# ---------------------------------------------------------------- binary frames over the UI socket
@pytest.fixture
async def backend(settings):
    from driveos.server import build_backend

    settings.vehicle.simulator_time_scale = 25
    b = build_backend(settings, voice=False, host="127.0.0.1", port=0)
    await b.start()
    yield b
    await b.stop()


async def test_frames_go_out_as_binary_messages_and_state_stays_json(backend):
    server = backend.server
    async with connect(f"ws://127.0.0.1:{server.port}/v1/ui") as ws:
        json.loads(await ws.recv())            # hello
        json.loads(await ws.recv())            # first state
        frame = pv.pack_frame(1, 2, 2, b"\x01" * 8)
        server.push_frame(frame)
        got = await asyncio.wait_for(ws.recv(), 3)
        assert isinstance(got, bytes) and got == frame


async def test_hub_frames_are_forwarded_and_slow_clients_drop_frames(backend):
    server = backend.server
    hub = backend.services.nova.preview
    async with connect(f"ws://127.0.0.1:{server.port}/v1/ui") as ws:
        json.loads(await ws.recv())
        json.loads(await ws.recv())
        hub.feed_enabled = True
        hub.fps = 1000
        hub.feed(img(48, 96))
        got = await asyncio.wait_for(ws.recv(), 3)
        assert isinstance(got, bytes) and pv.parse_header(got) is not None


async def test_a_state_change_from_another_thread_republishes_on_the_loop(backend, monkeypatch):
    server = backend.server
    hub = backend.services.nova.preview
    hub.reset()
    woke = asyncio.Event()
    monkeypatch.setattr(server, "mark_dirty", woke.set)
    t = threading.Thread(target=hub.begin_scan)
    t.start()
    await asyncio.wait_for(woke.wait(), 3)
    t.join()
    hub.reset()


async def test_a_client_joining_mid_scan_gets_the_state_and_the_last_frame_at_once(backend):
    server = backend.server
    hub = backend.services.nova.preview
    hub.reset()
    hub.fps = 1000
    hub.begin_scan()
    hub.feed(img(48, 96))
    try:
        async with connect(f"ws://127.0.0.1:{server.port}/v1/ui") as ws:
            json.loads(await ws.recv())            # hello
            json.loads(await ws.recv())            # first state
            got = await asyncio.wait_for(ws.recv(), 3)
            assert isinstance(got, bytes) and pv.parse_header(got) is not None
    finally:
        hub.reset()


async def test_a_slow_client_drops_frames_instead_of_queueing_them(backend):
    server = backend.server

    class Slow:
        def __init__(self):
            self.sent: list = []
            self.gate = asyncio.Event()

        async def send(self, data):
            self.sent.append(data)
            await self.gate.wait()                 # a link too slow to finish the first frame

    slow = Slow()
    server.clients.add(slow)
    frames = [pv.pack_frame(s, 2, 2, bytes([s]) * 8) for s in (1, 2, 3)]
    try:
        server.push_frame(frames[0])
        await asyncio.sleep(0.05)                  # the send of frame 1 is in flight and stuck
        server.push_frame(frames[1])               # frame 2 finds the client busy: dropped, not queued
        await asyncio.sleep(0.05)
        slow.gate.set()
        await asyncio.sleep(0.05)
        server.push_frame(frames[2])               # the client is free again: frame 3 goes out
        await asyncio.sleep(0.1)
        got = [f for f in slow.sent if isinstance(f, bytes)]
        assert got == [frames[0], frames[2]]
    finally:
        slow.gate.set()
        server.clients.discard(slow)


# ---------------------------------------------------------------- the verified face is shown before the code screen
async def test_verifier_holds_the_verified_face_before_asking_for_the_code(tmp_path):
    from driveos.biometrics.verifier import FaceFirstVerifier
    from driveos.config import BiometricsConfig
    from tests.test_biometrics import FakeHost, FakeRung

    order: list[str] = []

    class Host(FakeHost):
        async def hold_face_result(self):
            order.append("hold")

        def set_status(self, stage, challenge, detail):
            order.append(stage)
            super().set_status(stage, challenge, detail)

    host = Host()
    host.ctx["known"] = False                      # a new payee: DriveAuth steps up to the code
    v = FaceFirstVerifier(BiometricsConfig(home=tmp_path / "d"), rung=FakeRung("match"))
    v.seam.auth._profile.seed_mature()
    v.bind(host)
    r = await v.verify(10)
    assert r.success and host.otp_calls == 1
    assert order.index("hold") < order.index("otp")


async def test_nova_holds_a_verified_face_only_for_the_face_method(settings):
    import time

    from tests.test_biometric_checkout import ScriptedVerifier, _checkout

    s, n, sink = await _checkout(settings, ScriptedVerifier())
    try:
        hub = n.preview = PreviewHub()
        n.verified_hold_s = 0.2
        hub.begin_scan()
        hub.finish(True, "alex")
        n.shop.checkout.method = "otp"
        t0 = time.perf_counter()
        await n.hold_face_result()
        assert time.perf_counter() - t0 < 0.1
        n.shop.checkout.method = "face"
        t0 = time.perf_counter()
        await n.hold_face_result()
        assert time.perf_counter() - t0 >= 0.18
        hub.reset()
        hub.begin_scan()
        hub.finish(False)
        t0 = time.perf_counter()
        await n.hold_face_result()                  # a failed face is shown too (red) before the code screen
        assert time.perf_counter() - t0 >= 0.18
        t0 = time.perf_counter()
        await n.hold_face_result(("verified",))     # ... but not after the driver typed the code
        assert time.perf_counter() - t0 < 0.1
        hub.reset()
        t0 = time.perf_counter()
        await n.hold_face_result()                  # no face result: no wait
        assert time.perf_counter() - t0 < 0.1
    finally:
        await s.aclose()


def test_preview_runs_at_the_camera_rate_without_skipping_every_other_frame():
    clk = Clock()
    hub = PreviewHub(fps=15, size=8, clock=clk)
    sent = 0
    for _ in range(30):
        clk.t += 1 / 15                              # the F455 delivers 15 frames per second
        sent += hub.feed(img(16, 32))
    assert sent == 30
