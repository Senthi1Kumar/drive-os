"""Voice ID enrollment over /v1/ui (hmi_lvgl/ENROLL_CONTRACT.md) with the mic and the speaker model mocked."""

from __future__ import annotations

import numpy as np
import pytest

from driveos.biometrics import enroll as em
from driveos.biometrics.capture import Capture
from driveos.biometrics.quality import Quality
from driveos.biometrics.store import TemplateStore
from tests.test_ws_contract import Client, connect

pytestmark = pytest.mark.asyncio


class _Emb:
    name, dim = "fake", 8

    def embed(self, audio):
        v = np.zeros(8, np.float32)
        v[int(round(abs(float(audio[0])) * 10)) % 8] = 1.0
        return v + 0.01


class _Mic:
    seq = 0

    def __init__(self):
        self.drained = 0

    def frames_since(self, seq):
        return []

    def drain(self):
        self.drained += 1


class _Pipeline:
    available = True

    def __init__(self):
        self.mic, self.stt, self.paused = _Mic(), None, False


@pytest.fixture
async def rig(settings, monkeypatch):
    from driveos.server import build_backend

    settings.vehicle.simulator_time_scale = 25
    b = build_backend(settings, voice=False, host="127.0.0.1", port=0)
    pipe = _Pipeline()
    b.server.commands.pipeline = pipe
    svc = b.server.commands.enroll
    svc.pipeline = pipe
    svc.embedder_factory = lambda cfg: _Emb()
    quality = {"next": []}

    def fake_assess(audio):
        ok = bool(quality["next"].pop(0)) if quality["next"] else True
        return Quality(ok, 3.0, 20.0, 0.0, [] if ok else ["too noisy"])

    monkeypatch.setattr(em, "assess", fake_assess)
    tones = {"next": []}

    async def fake_capture(src, **kw):
        k = tones["next"].pop(0) if tones["next"] else 1
        return Capture(np.full(16000 * 3, k / 10.0, dtype=np.float32), None, True, 3.0)

    svc.capture = fake_capture
    await b.start()
    async with connect(f"ws://127.0.0.1:{b.server.port}/v1/ui") as ws:
        await ws.recv()
        c = Client(ws)
        await c.wait(lambda: c.state, what="first state")
        c.backend, c.pipe, c.quality, c.tones = b, pipe, quality, tones
        yield c
        c._task.cancel()
    await b.stop()


async def _cmd(c, cmd, **args):
    """Client.cmd takes ``name`` for the command name, which collides with the ``name`` argument."""
    import json

    c._id += 1
    await c.ws.send(json.dumps({"v": 1, "type": "cmd", "id": c._id, "name": cmd, "args": args}))
    await c.wait(lambda: c._id in c.acks, what=f"ack {cmd}")
    return c.acks[c._id]


async def _ready(c):
    ack = await _cmd(c, "enroll_start", name="Sam", consent=True)
    assert ack["ok"] is True, ack
    await c.wait(lambda: (c.state.get("enroll") or {}).get("stage") == "ready", what="ready")


async def _take(c, n):
    await c.cmd("enroll_take")
    await c.wait(lambda: len(c.state["enroll"]["quality"]) >= n and c.state["enroll"]["stage"] != "recording"
                 and c.state["enroll"]["stage"] != "analyzing", what=f"take {n}")


async def test_state_enroll_null_by_default(rig):
    assert rig.state["enroll"] is None


async def test_consent_required(rig):
    ack = await _cmd(rig, "enroll_start", name="Sam", consent=False)
    assert ack["ok"] is False and "consent" in ack["error"]
    ack = await _cmd(rig, "enroll_start", name="Sam")
    assert ack["ok"] is False
    assert rig.state["enroll"] is None and rig.pipe.paused is False


async def test_start_ready_pauses_voice_loop(rig):
    await _ready(rig)
    e = rig.state["enroll"]
    assert e["take"] == 1 and e["total"] == 5 and e["kept"] == 0 and e["quality"] == [] and e["error"] == ""
    assert "Sam" in e["prompt"]
    assert rig.pipe.paused is True


async def test_second_start_rejected_while_active(rig):
    await _ready(rig)
    ack = await _cmd(rig, "enroll_start", name="Sam", consent=True)
    assert ack["ok"] is False


async def test_start_rejected_mid_checkout(rig):
    rig.backend.services.nova.bio_capture = True
    ack = await _cmd(rig, "enroll_start", name="Sam", consent=True)
    assert ack["ok"] is False and rig.pipe.paused is False


async def test_take_without_session_or_mic(rig):
    assert (await rig.cmd("enroll_take"))["ok"] is False
    await _ready(rig)
    rig.pipe.mic = None
    ack = await rig.cmd("enroll_take")
    assert ack["ok"] is False and ack["error"] == "voice unavailable"


async def test_take_progression_and_quality(rig):
    await _ready(rig)
    await _take(rig, 1)
    e = rig.state["enroll"]
    assert e["stage"] == "ready" and e["take"] == 2 and e["kept"] == 1
    assert set(e["quality"][0]) == {"duration_s", "snr_db", "clipping_pct", "outlier", "ok"}
    assert e["quality"][0]["ok"] is True and e["quality"][0]["duration_s"] == 3.0


async def test_rejected_take_does_not_advance(rig):
    await _ready(rig)
    rig.quality["next"] = [False]
    await _take(rig, 1)
    e = rig.state["enroll"]
    assert e["quality"][0]["ok"] is False and e["take"] == 1 and e["kept"] == 0 and e["stage"] == "ready"


async def test_done_saves_template_and_enables_voice_id(rig, settings):
    rig.tones["next"] = [1, 1, 1, 1, 5]            # last take is the odd one out
    await _ready(rig)
    for i in range(1, 6):
        await _take(rig, i) if i < 5 else None
    await rig.cmd("enroll_take")
    await rig.wait(lambda: rig.state["enroll"]["stage"] == "done", what="done")
    e = rig.state["enroll"]
    assert e["total"] == 5 and e["kept"] == 4 and [q["outlier"] for q in e["quality"]] == [False] * 4 + [True]
    store = TemplateStore(settings.biometrics.home)
    t = store.load(settings.biometrics.driver_id)
    assert t is not None and t.consent and t.name == "Sam"
    assert rig.pipe.paused is False and rig.pipe.mic.drained >= 1
    assert rig.backend.services.nova.face is rig.backend.services.face


async def test_done_swaps_face_verifier(rig, settings, monkeypatch):
    built = []

    class _VF:
        name = "voice_id"
        seam = type("S", (), {"version": "test"})()

        def bind(self, host):
            pass

    from driveos.biometrics import factory

    monkeypatch.setattr(factory, "build_voice_first",
                        lambda cfg, require_enrollment=True: (built.append(1) or _VF(), "ok"))
    await _ready(rig)
    for i in range(1, 5):
        await _take(rig, i)
    await rig.cmd("enroll_take")
    await rig.wait(lambda: rig.state["enroll"]["stage"] == "done", what="done")
    assert built and isinstance(rig.backend.services.nova.face, _VF)


async def test_cancel_resumes_voice_and_clears(rig):
    await _ready(rig)
    await _take(rig, 1)
    ack = await rig.cmd("enroll_cancel")
    assert ack["ok"] is True
    await rig.wait(lambda: rig.state["enroll"] is None, what="cleared")
    assert rig.pipe.paused is False


async def test_cancel_during_recording(rig):
    import asyncio

    gate = asyncio.Event()

    async def slow(src, **kw):
        await gate.wait()

    rig.backend.server.commands.enroll.capture = slow
    await _ready(rig)
    await rig.cmd("enroll_take")
    await rig.wait(lambda: rig.state["enroll"]["stage"] == "recording", what="recording")
    await rig.cmd("enroll_cancel")
    await rig.wait(lambda: rig.state["enroll"] is None, what="cleared")
    assert rig.pipe.paused is False


async def test_not_enough_speech_fails_and_resumes(rig, settings):
    rig.quality["next"] = [False] * 9
    await _ready(rig)
    for i in range(1, 10):
        await _take(rig, i)
        if rig.state["enroll"]["stage"] == "failed":
            break
    e = rig.state["enroll"]
    assert e["stage"] == "failed" and e["error"]
    assert rig.pipe.paused is False
    assert not TemplateStore(settings.biometrics.home).exists(settings.biometrics.driver_id)
    # a finished session does not block a new one
    assert (await _cmd(rig, "enroll_start", name="Sam", consent=True))["ok"] is True


async def test_missing_speaker_model_fails_with_error(rig):
    rig.backend.server.commands.enroll.embedder_factory = lambda cfg: None
    await _cmd(rig, "enroll_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll"] or {}).get("stage") == "failed", what="failed")
    assert "model" in rig.state["enroll"]["error"] and rig.pipe.paused is False


# ------------------------------------------------------------------ face enrollment (F455) + simulated outcomes
class _FaceClient:
    def __init__(self, present=True, status="Success", auth_user_id="existing_1", remove_result=True, block=False):
        import threading

        self.present, self.status, self.removed, self.cancelled = present, status, [], 0
        self.auth_user_id, self.remove_result = auth_user_id, remove_result
        self._gate = threading.Event() if block else None

    def device_present(self):
        return self.present

    def enroll(self, user_id, timeout_s=30.0):
        from driveos.biometrics.f455 import F455Result
        if self._gate is not None:
            self._gate.wait(5)
        ok = self.status == "Success"
        return F455Result(matched=ok, user_id=user_id if ok else None, status=self.status)

    def authenticate(self, timeout_s=10.0):
        from driveos.biometrics.f455 import F455Result
        ok = self.auth_user_id is not None
        return F455Result(matched=ok, user_id=self.auth_user_id if ok else None, status="Success" if ok else "Timeout")

    def remove_user(self, uid):
        self.removed.append(uid)
        return self.remove_result

    def cancel(self):
        self.cancelled += 1
        if self._gate is not None:
            self._gate.set()


async def test_face_id_status_default_and_persisted(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    assert rig.state["face_id"] == {"enrolled": False, "name": "", "linked": False}
    users = FaceUserMap(settings.biometrics.home)
    users.link(settings.biometrics.driver_id, "aibox_1")
    users.set_name(settings.biometrics.driver_id, "Sam")
    rig.backend.server.mark_dirty()
    await rig.wait(lambda: rig.state["face_id"]["enrolled"], what="persisted face status")
    assert rig.state["face_id"] == {"enrolled": True, "name": "Sam", "linked": True}


async def test_face_reenroll_removes_only_owned_old_template(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    client = _FaceClient()
    rig.backend.server.commands.enroll.face.client = client
    users.set(settings.biometrics.driver_id, "owned_old")
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="owned replacement")
    assert client.removed == ["owned_old"]

    users.link(settings.biometrics.driver_id, "aibox_old")
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="linked replacement")
    assert client.removed == ["owned_old"]


async def test_face_duplicate_requires_confirmed_link(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    client = _FaceClient(status="DuplicateFaceprints")
    rig.backend.server.commands.enroll.face.client = client
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "link_confirm", what="link confirmation")
    users = FaceUserMap(settings.biometrics.home)
    assert users.get(settings.biometrics.driver_id) is None
    assert "user_id" not in rig.state["enroll_face"]
    assert (await _cmd(rig, "enroll_face_link", confirm=False))["ok"] is False
    assert (await _cmd(rig, "enroll_face_link", confirm=True))["ok"] is True
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="linked")
    assert users.get(settings.biometrics.driver_id) == "existing_1"
    assert users.is_linked("existing_1") and client.removed == []


async def test_face_duplicate_cancel_and_expiry_preserve_mapping(rig, settings, monkeypatch):
    from driveos.biometrics.f455 import FaceUserMap
    from driveos.server import enroll as enroll_module

    monkeypatch.setattr(enroll_module, "FACE_LINK_TTL_S", 0.1)
    rig.backend.server.commands.enroll.face.client = _FaceClient(status="DuplicateFaceprints")
    users = FaceUserMap(settings.biometrics.home)
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "link_confirm", what="link cancel")
    await rig.cmd("enroll_face_cancel")
    await rig.wait(lambda: rig.state["enroll_face"] is None, what="link cancelled")
    assert users.get(settings.biometrics.driver_id) is None

    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "failed", what="link expiry")
    assert "timed out" in rig.state["enroll_face"]["error"]
    assert users.get(settings.biometrics.driver_id) is None


async def test_face_delete_owned_and_confirmation(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set(settings.biometrics.driver_id, "owned_1")
    client = _FaceClient()
    rig.backend.server.commands.enroll.face.client = client
    assert (await _cmd(rig, "enroll_face_delete", confirm=False))["ok"] is False
    assert users.get(settings.biometrics.driver_id) == "owned_1" and client.removed == []
    assert (await _cmd(rig, "enroll_face_delete", confirm=True))["ok"] is True
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="deleted")
    assert client.removed == ["owned_1"] and users.get(settings.biometrics.driver_id) is None
    assert rig.state["enroll_face"]["operation"] == "delete"


async def test_face_delete_linked_only_unlinks(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.link(settings.biometrics.driver_id, "aibox_1")
    client = _FaceClient()
    rig.backend.server.commands.enroll.face.client = client
    await _cmd(rig, "enroll_face_delete", confirm=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="unlinked")
    assert client.removed == [] and users.get(settings.biometrics.driver_id) is None


async def test_face_delete_failure_preserves_mapping(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set(settings.biometrics.driver_id, "owned_1")
    client = _FaceClient(remove_result=False)
    rig.backend.server.commands.enroll.face.client = client
    await _cmd(rig, "enroll_face_delete", confirm=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "failed", what="delete failure")
    assert users.get(settings.biometrics.driver_id) == "owned_1"


async def test_face_enrollment_busy_guards(rig):
    client = _FaceClient(block=True)
    rig.backend.server.commands.enroll.face.client = client
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "capturing", what="face capturing")
    assert (await _cmd(rig, "enroll_start", name="Sam", consent=True))["ok"] is False
    assert rig.backend.services.nova.capture_busy() is True
    await rig.cmd("enroll_face_cancel")

    await _ready(rig)
    assert (await _cmd(rig, "enroll_face_start", name="Sam", consent=True))["ok"] is False
    await rig.cmd("enroll_cancel")
    rig.backend.services.nova.shop.checkout = object()
    assert (await _cmd(rig, "enroll_face_start", name="Sam", consent=True))["ok"] is False
    rig.backend.services.nova.shop.checkout = None


async def test_enroll_face_requires_consent(rig):
    ack = await _cmd(rig, "enroll_face_start", name="Sam", consent=False)
    assert ack["ok"] is False and "consent" in ack["error"]
    assert rig.state["enroll_face"] is None


async def test_enroll_face_success_records_user_id(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    rig.backend.server.commands.enroll.face.client = _FaceClient()
    ack = await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    assert ack["ok"] is True
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="face done")
    uid = FaceUserMap(settings.biometrics.home).get(settings.biometrics.driver_id)
    assert uid and uid.startswith("sam_")
    assert rig.state["enroll"] is None


async def test_enroll_face_failure_and_absent_device(rig):
    rig.backend.server.commands.enroll.face.client = _FaceClient(status="Timeout")
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "failed", what="failed")
    assert "see a face" in rig.state["enroll_face"]["error"]
    rig.backend.server.commands.enroll.face.client = _FaceClient(present=False)
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: "not found" in ((rig.state["enroll_face"] or {}).get("error") or ""), what="absent")


async def test_sim_outcome_rejected_when_live(rig):
    ack = await _cmd(rig, "sim_outcome", modality="voice", outcome="spoof")
    assert ack["ok"] is False and "not simulated" in ack["error"]


async def test_sim_outcome_sets_scripted_outcome(rig, settings):
    from driveos.biometrics.sim import SimulatedVoiceFirstVerifier

    v = SimulatedVoiceFirstVerifier(settings.biometrics, step_s=0)
    rig.backend.services.nova.set_face(v)
    assert (await _cmd(rig, "sim_outcome", modality="face", outcome="spoof"))["ok"] is True
    assert v.outcomes["face"] == "spoof" and v.rung.outcome == "spoof"
    assert (await _cmd(rig, "sim_outcome", modality="iris", outcome="spoof"))["ok"] is False
    assert (await _cmd(rig, "sim_outcome", modality="voice", outcome="maybe"))["ok"] is False


async def test_face_enrollment_finishes_preview_hub(rig):
    from driveos.biometrics.preview import PreviewHub

    hub = PreviewHub()
    client = _FaceClient()
    client.hub = hub
    rig.backend.server.commands.enroll.face.client = client
    hub.begin_scan()
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="preview done")
    assert hub.snapshot()["face_state"] == "verified" and hub.snapshot()["face_name"] == "Sam"

    client.status = "Timeout"
    hub.begin_scan()
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "failed", what="preview failed")
    assert hub.snapshot()["face_state"] == "failed"


async def test_face_enrollment_cancel_resets_preview_hub(rig):
    from driveos.biometrics.preview import PreviewHub

    hub = PreviewHub()
    client = _FaceClient(block=True)
    client.hub = hub
    rig.backend.server.commands.enroll.face.client = client
    hub.begin_scan()
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "capturing", what="preview capturing")
    await rig.cmd("enroll_face_cancel")
    await rig.wait(lambda: rig.state["enroll_face"] is None, what="preview cancelled")
    assert hub.snapshot()["face_state"] == "none"


async def test_face_duplicate_of_owned_mapping_stays_owned(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set(settings.biometrics.driver_id, "existing_1")
    client = _FaceClient(status="DuplicateFaceprints", auth_user_id="existing_1")
    rig.backend.server.commands.enroll.face.client = client
    await _cmd(rig, "enroll_face_start", name="Sam", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="owned duplicate")
    assert users.get(settings.biometrics.driver_id) == "existing_1"
    assert not users.is_linked("existing_1") and client.removed == []


async def test_checkout_and_auth_rejected_during_enrollment(rig):
    nova = rig.backend.services.nova
    nova.capture_busy = lambda: True
    await nova.checkout()
    assert "enrollment" in nova.voice.text
    nova.shop.checkout = object()
    await nova.choose_auth("face")
    assert "enrollment" in nova.voice.text
    nova.shop.checkout = None


async def test_face_profile_add_edit_select_and_limit(rig, settings):
    assert rig.state["face_profiles"] == {"items": [], "selected_id": "", "max": 5}
    rig.backend.server.commands.enroll.face.client = _FaceClient()
    ack = await _cmd(rig, "enroll_face_start", profile_id="new", name="Sam", role="driver", consent=True)
    assert ack["ok"] is True
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "done", what="profile added")
    first = rig.state["face_profiles"]["items"][0]
    assert first["name"] == "Sam" and first["role"] == "driver" and not first["linked"]
    assert rig.state["face_profiles"]["selected_id"] == first["id"]

    assert (await _cmd(rig, "face_profile_edit", profile_id=first["id"], name="Sam", role="passenger"))["ok"] is True
    await rig.wait(lambda: rig.state["face_profiles"]["items"][0]["role"] == "passenger", what="role edited")
    assert (await _cmd(rig, "face_profile_edit", profile_id=first["id"], name="", role="driver"))["ok"] is False
    assert (await _cmd(rig, "face_profile_edit", profile_id=first["id"], name="Sam", role="owner"))["ok"] is False

    from driveos.biometrics.f455 import FaceUserMap
    users = FaceUserMap(settings.biometrics.home)
    for i in range(2, 6):
        users.set(f"profile{i}", f"user_{i}")
        users.set_name(f"profile{i}", f"User {i}")
    ack = await _cmd(rig, "enroll_face_start", profile_id="new", name="Sixth", role="driver", consent=True)
    assert ack["ok"] is False and "five" in ack["error"]


async def test_face_profile_duplicate_of_other_profile_fails(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set("driver1", "existing_1")
    users.set_name("driver1", "Existing")
    rig.backend.server.commands.enroll.face.client = _FaceClient(status="DuplicateFaceprints", auth_user_id="existing_1")
    await _cmd(rig, "enroll_face_start", profile_id="new", name="Other", role="passenger", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "failed", what="profile conflict")
    assert "another DriveOS profile" in rig.state["enroll_face"]["error"]
    assert len(users.profiles()) == 1


async def test_face_profile_select_edit_blocked_during_operation(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set("driver1", "existing_1")
    users.set("profile2", "existing_2")
    client = _FaceClient(block=True)
    rig.backend.server.commands.enroll.face.client = client
    await _cmd(rig, "enroll_face_start", profile_id="driver1", name="Sam", role="driver", consent=True)
    await rig.wait(lambda: (rig.state["enroll_face"] or {}).get("stage") == "capturing", what="profile busy")
    assert (await _cmd(rig, "face_profile_select", profile_id="profile2"))["ok"] is False
    assert (await _cmd(rig, "face_profile_edit", profile_id="profile2", name="Bob", role="driver"))["ok"] is False
    await rig.cmd("enroll_face_cancel")


async def test_selected_passenger_cannot_checkout(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set("passenger1", "person_1")
    users.set_role("passenger1", "passenger")
    users.select("passenger1")
    nova = rig.backend.services.nova
    await nova.checkout()
    assert "driver" in nova.voice.text and nova.shop.checkout is None


async def test_face_profile_name_only_edit_preserves_role(rig, settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.set("passenger1", "person_1")
    users.set_name("passenger1", "Bob")
    users.set_role("passenger1", "passenger")
    assert (await _cmd(rig, "face_profile_edit", profile_id="passenger1", name="Robert"))["ok"] is True
    assert users.role("passenger1") == "passenger"


async def test_face_profile_store_rejects_corrupt_file(settings):
    from driveos.biometrics.f455 import FaceUserMap

    users = FaceUserMap(settings.biometrics.home)
    users.path.parent.mkdir(parents=True, exist_ok=True)
    users.path.write_text("{broken")
    try:
        users.set("driver1", "new_1")
    except ValueError:
        pass
    else:
        raise AssertionError("corrupt profile store was overwritten")
    assert users.path.read_text() == "{broken"
