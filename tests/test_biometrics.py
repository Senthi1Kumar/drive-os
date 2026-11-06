"""Voice-first verification: liveness, calibration, enrollment, and the real DriveAuth decision engine
behind the adapter seam (only the ONNX models, ASR and the face camera are faked)."""

from __future__ import annotations

import numpy as np
import pytest

from driveos.biometrics import driveauth_adapter as da
from driveos.biometrics import verifier as vmod
from driveos.biometrics.calibrate import Calibrator
from driveos.biometrics.capture import Capture
from driveos.biometrics.cm import CmResult
from driveos.biometrics.enroll import ConsentRequired, EnrollmentSession, NotEnoughSpeech, drop_outliers
from driveos.biometrics.liveness import (
    channel_check,
    digits_from_text,
    generate_challenge,
    spoken_digits,
)
from driveos.biometrics.quality import Quality
from driveos.biometrics.rung import AiBoxFaceRung
from driveos.biometrics.store import TemplateStore, VoiceTemplate
from driveos.biometrics.sv import fit_window, l2
from driveos.config import BiometricsConfig
from driveos.domain.models import AuthTier

RNG = np.random.default_rng(7)


def speechlike(seconds=3.0, seed=1):
    r = np.random.default_rng(seed)
    t = np.arange(int(16000 * seconds)) / 16000
    env = (np.sin(2 * np.pi * 3 * t) > -0.2).astype(np.float32)
    return (0.2 * np.sin(2 * np.pi * 180 * t) * env + 0.002 * r.standard_normal(len(t))).astype(np.float32)


# ------------------------------------------------------------------ liveness
@pytest.mark.parametrize("text,want", [
    ("four two seven nine", "4279"), ("4279", "4279"), ("4 2 7 9.", "4279"),
    ("forty-two seventy-nine", "4279"), ("Forty two seventy nine", "4279"),
    ("oh five three eight", "0538"), ("the code is six one four", "614"), ("hello there", ""),
    ("twenty", "20"), ("nineteen eighty", "1980"),
])
def test_digit_normalisation(text, want):
    assert digits_from_text(text) == want


def test_challenge_has_no_repeats_or_adjacent_digits():
    for _ in range(300):
        c = generate_challenge(4)
        assert len(c) == 4 and len(set(c)) == 4
        assert all(abs(int(a) - int(b)) != 1 for a, b in zip(c, c[1:], strict=False))
    assert spoken_digits("4279") == "four two seven nine"


def test_channel_check_variants():
    a = speechlike()
    assert channel_check(a, a.copy()).status == "fail"                 # bit-identical
    assert channel_check(a, a * 0.5).status == "fail"                  # perfectly correlated at zero lag
    other = a * 0.8 + 0.05 * np.random.default_rng(3).standard_normal(len(a)).astype(np.float32)
    assert channel_check(a, other).status == "pass"
    assert channel_check(a, None).status == "n/a"                      # 1-channel laptop mic is not a failure


# ------------------------------------------------------------------ calibration
def test_calibration_monotonic_and_bounded():
    for cal in (Calibrator(), Calibrator.fit([0.7, 0.75, 0.8, 0.72, 0.78], [0.05, 0.1, 0.0, 0.2, 0.15, 0.12])):
        xs = np.linspace(-1, 1, 81)
        ps = [cal(x) for x in xs]
        assert all(b >= a for a, b in zip(ps, ps[1:], strict=False)) and 0.0 <= ps[0] <= ps[-1] <= 1.0
    fit = Calibrator.fit([0.7, 0.75, 0.8, 0.72, 0.78], [0.05, 0.1, 0.0, 0.2, 0.15, 0.12])
    assert fit.label == "demo-calibrated" and fit(0.75) > 0.9 and fit(0.1) < 0.1
    assert abs(fit(fit.cosine_for(0.72)) - 0.72) < 1e-6


# ------------------------------------------------------------------ enrollment
def test_outlier_drop():
    base = l2(RNG.standard_normal(192))
    embs = [l2(base + 0.05 * RNG.standard_normal(192)) for _ in range(4)] + [l2(RNG.standard_normal(192))]
    assert drop_outliers(np.stack(embs)) == [4]
    assert drop_outliers(np.stack(embs[:3])) == []


class _Emb:
    name, dim = "fake", 8

    def embed(self, audio):
        v = np.zeros(8, np.float32)
        v[int(round(abs(float(audio[0])) * 10)) % 8] = 1.0
        return v + 0.01


def _tone(k):
    return np.full(16000 * 3, k / 10.0, dtype=np.float32)


def test_enrollment_session(tmp_path, monkeypatch):
    from driveos.biometrics import enroll as em

    monkeypatch.setattr(em, "assess", lambda a: Quality(True, 3.0, 20.0, 0.0))
    store = TemplateStore(tmp_path)
    with pytest.raises(ConsentRequired):
        EnrollmentSession(_Emb(), store, driver_id="d", name="A", consent=False)
    s = EnrollmentSession(_Emb(), store, driver_id="d", name="Asha", consent=True)
    s.add(_tone(1))
    s.add(_tone(1))
    with pytest.raises(NotEnoughSpeech):
        s.finalize()
    for k in (1, 1, 1, 5):
        s.add(_tone(k))
    res = s.finalize()
    assert res.dropped_outliers == [5] or res.kept >= 3
    assert store.exists("d") and oct(store.key_path.stat().st_mode & 0o777) == "0o600"
    back = store.load("d")
    assert back is not None and back.name == "Asha" and back.consent and back.embeddings.shape[1] == 8
    assert b"Asha" not in store.path("d").read_bytes()          # encrypted at rest


def test_enrollment_rejects_poor_utterance(tmp_path):
    s = EnrollmentSession(_Emb(), TemplateStore(tmp_path), driver_id="d", name="A", consent=True)
    r = s.add(np.zeros(16000, np.float32))
    assert not r.accepted and r.quality.reasons


def test_fit_window_fills_and_crops():
    assert len(fit_window(np.ones(16000, np.float32), 96000)) == 96000
    assert len(fit_window(np.ones(200000, np.float32), 96000)) == 96000


# ------------------------------------------------------------------ verifier + real DriveAuth
class FakeHost:
    def __init__(self, otp_ok=True):
        self.statuses, self.otp_ok, self.otp_calls, self.said, self.capturing = [], otp_ok, 0, [], []
        self.ctx = {"amount": 8.5, "merchant": "Plur Coffee", "known": True}

    async def announce(self, text):
        self.said.append(text)

    def set_status(self, stage, challenge, detail):
        self.statuses.append((stage, challenge, detail))

    def set_capturing(self, on):
        self.capturing.append(on)

    async def otp_roundtrip(self, timeout_s):
        self.otp_calls += 1
        return self.otp_ok

    def payment_context(self):
        return dict(self.ctx)


class FakeStt:
    def __init__(self):
        self.text = None

    async def transcribe(self, pcm16, *, vocabulary=""):
        return self.text


class FakeCm:
    name = "fake"

    def __init__(self, result=None):
        self.result = result or CmResult("ok", 0.97, False, "bona fide 0.97")

    def check(self, audio):
        return self.result


class FakeRung:
    name = "fake"

    def __init__(self, kind="unavailable"):
        self.kind = kind

    def probe(self):
        return {"unavailable": da.unavailable, "match": lambda: da.confident_score(0.95),
                "nomatch": da.not_confident}[self.kind]()


class MatchEmb:
    """Returns the enrolled voice for the genuine clip and an unrelated voice otherwise."""
    name, dim = "fake", 8

    def __init__(self, genuine=True):
        self.genuine = genuine

    def embed(self, audio):
        v = np.zeros(8, np.float32)
        v[0 if self.genuine else 5] = 1.0
        return v


@pytest.fixture
def rig(tmp_path, monkeypatch):
    cfg = BiometricsConfig(home=tmp_path / "drive-os", channel_check="always")
    store = TemplateStore(cfg.home)
    tpl = np.zeros(8, np.float32)
    tpl[0] = 1.0
    store.save(VoiceTemplate("driver1", "Asha", "fake", tpl, tpl[None, :], True,
                             {"slope": 12.0, "mid": 0.45, "label": "demo-calibrated"}))
    state = {"cap": None, "challenge": None}

    async def fake_capture(src, **kw):
        return state["cap"]

    monkeypatch.setattr(vmod, "capture_utterance", fake_capture)
    host, stt = FakeHost(), FakeStt()

    def build(*, genuine=True, cm=None, rung="unavailable", mature=True, ch1="distinct"):
        a = speechlike()
        second = (a * 0.7 + 0.03 * np.random.default_rng(9).standard_normal(len(a)).astype(np.float32)
                  if ch1 == "distinct" else a.copy())
        state["cap"] = Capture(a, second, True, 3.0)
        v = vmod.VoiceFirstVerifier(cfg, embedder=MatchEmb(genuine), cm=cm or FakeCm(), store=store,
                                    rung=FakeRung(rung), stt=stt, source=object())
        if mature:
            v.seam.auth._profile.seed_mature()
        v.bind(host)
        orig = vmod.generate_challenge
        monkeypatch.setattr(vmod, "generate_challenge", lambda n=4: (state.__setitem__("challenge", orig(n)) or state["challenge"]))
        return v

    return type("Rig", (), {"build": staticmethod(build), "host": host, "stt": stt, "state": state, "cfg": cfg})


def _says(rig):
    rig.stt.text = " ".join(rig.state["challenge"])


class _SayingStt(FakeStt):
    pass


async def _run(rig, v):
    # the transcript must match whatever challenge the verifier draws
    class Echo:
        async def transcribe(self, pcm16, *, vocabulary=""):
            return rig.stt.text if rig.stt.text is not None else spoken_digits(rig.state["challenge"])

    v.stt = Echo()
    return await v.verify(20)


async def test_accept_path(rig):
    v = rig.build()
    r = await _run(rig, v)
    assert r.success and r.tier == AuthTier.T2_STRONG and r.method == "voice_id"
    assert "demo-calibrated" in r.detail and "anti-spoof" in r.detail
    stages = [s for s, _, _ in rig.host.statuses]
    assert stages[0] == "challenge" and "verifying" in stages
    ch = rig.host.statuses[0][1]
    assert ch.isdigit() and len(ch) == 4 and rig.host.said[0].startswith("Please say:")
    assert rig.host.capturing[0] is True and rig.host.capturing[-1] is False
    assert rig.host.otp_calls == 0


async def test_impostor_voice_rejected(rig):
    v = rig.build(genuine=False)
    r = await _run(rig, v)
    assert not r.success


async def test_spoof_is_not_confident_and_steps_up(rig):
    rig.cfg.cm_mode = "gate"                                 # gate semantics; the device default is advisory
    rig.host.ctx["known"] = False                            # new payee: DriveAuth requires stage-3
    v = rig.build(cm=FakeCm(CmResult("ok", 0.05, True, "bona fide 0.05")))
    rig.host.otp_ok = False
    r = await _run(rig, v)
    assert not r.success and rig.host.otp_calls == 1          # went to the code, never accepted on voice
    assert v.last.failures and "synthetic" in v.last.failures[0]
    d = v.seam.auth._last_result
    assert d.modality_scores["voice"]["score"] is None and not d.modality_scores["voice"]["conf"]


async def test_wrong_digits_step_up(rig):
    rig.host.ctx["known"] = False                            # new payee: DriveAuth requires stage-3
    v = rig.build()
    rig.stt.text = "one one one one"
    rig.host.otp_ok = False
    r = await _run(rig, v)
    assert not r.success and rig.host.otp_calls == 1 and "digits" in v.last.failures[0]


async def test_duplicated_channels_fail(rig):
    rig.host.ctx["known"] = False                            # new payee: DriveAuth requires stage-3
    v = rig.build(ch1="same")
    rig.host.otp_ok = False
    r = await _run(rig, v)
    assert not r.success and v.last.channel.status == "fail" and rig.host.otp_calls == 1


async def test_channel_check_not_applied_to_laptop_mic(rig):
    rig.cfg.channel_check = "auto"
    v = rig.build(ch1="same")
    v.source = type("S", (), {"device_name": "Built-in Audio"})()
    r = await _run(rig, v)
    assert r.success and v.last.channel.status == "n/a"


async def test_bootstrap_two_pass_otp_accepts(rig):
    v = rig.build(mature=False)
    r = await _run(rig, v)
    assert r.success and rig.host.otp_calls == 1
    d = v.seam.auth._last_result
    assert d.stage3_method == "otp_bluetooth" and d.modality_scores["otp"]["conf"]


async def test_bootstrap_otp_failure_rejects(rig):
    v = rig.build(mature=False)
    rig.host.otp_ok = False
    r = await _run(rig, v)
    assert not r.success and rig.host.otp_calls == 1


async def test_face_rung_match_reaches_second_modality(rig):
    v = rig.build(mature=False, rung="match")
    r = await _run(rig, v)
    assert r.success and rig.host.otp_calls == 1               # bootstrap still forces the code
    assert "face" in [s for s, _, _ in rig.host.statuses]


async def test_unavailable_cm_flagged_not_hidden(rig):
    v = rig.build(cm=FakeCm(CmResult("unavailable", None, False, "model off")))
    r = await _run(rig, v)
    assert r.success and "anti-spoof: off" in r.detail
    rig.cfg.cm_required = True
    v2 = rig.build(cm=FakeCm(CmResult("unavailable", None, False, "model off")))
    rig.host.otp_ok = False
    assert not (await _run(rig, v2)).success


async def test_not_enrolled_fails_closed(rig, tmp_path):
    v = rig.build()
    v.store.delete("driver1")
    r = await _run(rig, v)
    assert not r.success and "isn't set up" in r.detail


def test_unavailable_face_rung_when_box_absent():
    r = AiBoxFaceRung("http://127.0.0.1:9/v1/face/authenticate", "asha", 0.3).probe()
    assert not r.available and r.score is None


def test_driveauth_version_pin():
    assert da.check_version().startswith("driveauth")
    assert da.PINNED_COMMIT == "76f33c8"


# ------------------------------------------------------------------ real models (skipped when not downloaded)
def _models():
    return BiometricsConfig().models_dir


@pytest.mark.skipif(not (_models() / "wespeaker-r34" / "wespeaker-resnet34.onnx").is_file(), reason="models not fetched")
@pytest.mark.parametrize("backend,dim", [("wespeaker", 256), ("redimnet2", 192)])
def test_real_speaker_models_embed_deterministically(backend, dim):
    from driveos.biometrics.sv import build_embedder, cosine

    e = build_embedder(BiometricsConfig(sv_backend=backend))
    if e is None or e.dim != dim:
        pytest.skip(f"{backend} not available")
    a, b = speechlike(3.0, 1), speechlike(3.0, 2)
    assert cosine(e.embed(a), e.embed(a)) > 0.999 and cosine(e.embed(a), e.embed(b)) < 0.999


@pytest.mark.skipif(not (_models() / "aasist-l" / "aasist-l.onnx").is_file(), reason="models not fetched")
def test_aasist_runs_on_speechlike_audio():
    from driveos.biometrics.cm import build_cm

    cm = build_cm(BiometricsConfig()).check(speechlike(3.0, 1))
    assert cm.status == "ok" and 0.0 <= cm.bonafide <= 1.0


# ------------------------------------------------------------------ location into DriveAuth risk
def _risk(v, **ctx):
    """DriveAuth risk for one payment with the given vehicle / merchant location."""
    v.seam.set_context(**da.vehicle_risk_kwargs(ctx.get("vehicle"), ctx.get("merchant_location"), 5.0))
    v.seam.begin(da.confident_score(0.9))
    return v.seam.authenticate(speechlike(), amount=8.5, beneficiary="Plur Coffee", beneficiary_known=True)


NEAR = {"lat": 37.2260, "lon": -121.9740}
CAFE = {"lat": 37.2262, "lon": -121.9742}
FAR = {"lat": 37.4000, "lon": -122.1500}


def test_vehicle_kwargs_reset_every_call():
    kw = da.vehicle_risk_kwargs({**NEAR, "speed_kmh": 90, "parked": False}, {**FAR}, 5.0)
    assert kw["speed_kmh"] == 90 and kw["in_trusted_zone"] is False and kw["dist_from_home_km"] > 5
    clean = da.vehicle_risk_kwargs({**NEAR, "speed_kmh": 0, "parked": True}, CAFE, 5.0)
    assert clean["in_trusted_zone"] is True and clean["speed_kmh"] == 0 and clean["dist_from_home_km"] == 0
    assert da.vehicle_risk_kwargs(None, None, 5.0)["gps_lat"] is None


def test_parked_near_merchant_is_lower_risk_than_fast_and_far(rig):
    v = rig.build()
    normal = _risk(v, vehicle={**NEAR, "speed_kmh": 0, "parked": True}, merchant_location=CAFE)
    fast = _risk(v, vehicle={**NEAR, "speed_kmh": 110, "parked": False}, merchant_location=CAFE)
    far = _risk(v, vehicle={**NEAR, "speed_kmh": 0, "parked": True}, merchant_location=FAR)
    assert fast.risk > normal.risk
    assert far.risk > normal.risk
    assert normal.verdict == "ACCEPT"


async def test_risk_reported_to_host_without_biometrics(rig):
    v = rig.build()
    rig.host.ctx.update(vehicle={**NEAR, "speed_kmh": 0, "parked": True}, merchant_location=CAFE)
    got = []
    rig.host.set_risk = got.append
    r = await _run(rig, v)
    assert r.success
    assert set(got[0]) == {"score", "rule"} and 0.0 <= got[0]["score"] <= 1.0 and isinstance(got[0]["rule"], str)
    assert v.last_risk == got[0]
    ctx = v.seam.auth._risk_ctx
    assert ctx.gps_lat == NEAR["lat"] and ctx.in_trusted_zone is True
