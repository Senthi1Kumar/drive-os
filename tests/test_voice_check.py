"""CM observability, channel choice, per-device threshold, soft prefilter, digit biasing, and `driveos voice-check`."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from driveos.biometrics import voice_check as vc
from driveos.biometrics.capture import Capture
from driveos.biometrics.cm import CmResult, cm_input, judge
from driveos.biometrics.cm_calibration import CmCalibration
from driveos.biometrics.liveness import DIGIT_PROMPT, challenge_matches
from driveos.biometrics.store import VoiceTemplate
from driveos.config import Settings
from tests.test_biometrics import FakeCm, _run, rig, speechlike  # noqa: F401  (rig is a fixture)


class ChannelCm:
    """Bona fide probability depends on the channel's level, so the tests can see which one was read."""
    name = "fake"

    def __init__(self, table):
        self.table, self.seen = table, []

    def check(self, audio):
        self.seen.append(audio)
        rms = float(np.sqrt(np.mean(np.square(audio))))
        for lo, p in self.table:
            if rms >= lo:
                return CmResult("ok", p, False, f"bona fide {p:.2f}")
        return CmResult("ok", 0.0, False, "bona fide 0.00")


# ------------------------------------------------------------------ observability
async def test_log_line_has_cm_probability_and_channel_but_no_content(rig, caplog):  # noqa: F811
    v = rig.build(cm=FakeCm(CmResult("ok", 0.8123, False, "bona fide 0.81")))
    with caplog.at_level(logging.INFO, logger="driveos.biometrics.verifier"):
        await _run(rig, v)
    line = next(r.getMessage() for r in caplog.records if "cm_p=" in r.getMessage())
    assert "cm_p=0.812" in line and "cm_ch=ch0" in line and "cm_thr=0.500" in line
    digits = rig.state["challenge"]
    assert digits not in line and " ".join(digits) not in line
    assert "digits=4 chars" in line and "heard=" in line and "chars" in line.split("heard=")[1][:12]


async def test_log_line_when_cm_unavailable(rig, caplog):  # noqa: F811
    v = rig.build(cm=FakeCm(CmResult("unavailable", detail="x")))
    with caplog.at_level(logging.INFO, logger="driveos.biometrics.verifier"):
        await _run(rig, v)
    assert "cm_p=n/a" in " ".join(r.getMessage() for r in caplog.records)


# ------------------------------------------------------------------ channel selection
def test_cm_input_channels():
    a = np.full(100, 0.2, np.float32)
    b = np.full(100, 0.05, np.float32)
    assert cm_input(a, b, "ch0", 3.0)[1] == "ch0"
    x, ch = cm_input(a, b, "ch1", 3.0)
    assert ch == "ch1" and np.allclose(x, 0.15)
    x, ch = cm_input(a, b, "mix", 3.0)
    assert ch == "mix" and np.allclose(x, (0.2 + 0.15) / 2)
    assert cm_input(a, None, "ch1", 3.0)[1] == "ch0"          # single-channel device falls back
    assert float(cm_input(a, np.full(100, 0.9, np.float32), "ch1", 3.0)[0].max()) == 1.0   # clipped


async def test_verifier_feeds_configured_channel_to_cm(rig):  # noqa: F811
    rig.cfg.cm_channel = "ch1"
    rig.cfg.cm_ch1_gain = 1.0
    cm = ChannelCm([(0.0, 0.9)])
    v = rig.build(cm=cm)
    await _run(rig, v)
    cap = rig.state["cap"]
    assert np.allclose(cm.seen[0], cap.ch1) and v.last.cm_channel == "ch1"


# ------------------------------------------------------------------ calibrated threshold
def test_cm_calibration_roundtrip_is_private(tmp_path):
    CmCalibration({"ch1": 0.07}, 6).save(tmp_path, "d1")
    p = CmCalibration.path(tmp_path, "d1")
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    back = CmCalibration.load(tmp_path, "d1")
    assert back.threshold_for("ch1") == pytest.approx(0.07) and back.threshold_for("ch0") is None
    assert CmCalibration.load(tmp_path, "nobody").thresholds == {}


async def test_calibrated_threshold_is_loaded_and_decides(rig):  # noqa: F811
    CmCalibration({"ch0": 0.2}, 6).save(rig.cfg.home, rig.cfg.driver_id)
    v = rig.build(cm=FakeCm(CmResult("ok", 0.35, True, "x")))        # 0.35 is a spoof at 0.5, genuine at 0.2
    assert v.cm_threshold == pytest.approx(0.2)
    r = await _run(rig, v)
    assert r.success and not v.last.cm.spoof and v.last.cm_threshold == pytest.approx(0.2)


async def test_threshold_for_other_channel_is_ignored(rig):  # noqa: F811
    CmCalibration({"ch1": 0.01}, 6).save(rig.cfg.home, rig.cfg.driver_id)
    v = rig.build()
    assert v.cm_threshold == rig.cfg.cm_spoof_threshold            # cm_channel is ch0


# ------------------------------------------------------------------ safety rules
async def test_spoof_flag_never_yields_confident_score(rig):  # noqa: F811
    rig.cfg.cm_mode = "gate"
    rig.host.ctx["known"] = False
    rig.host.otp_ok = False
    v = rig.build(cm=FakeCm(CmResult("ok", 0.35, False, "x")))     # fake claims no spoof; the threshold decides
    await _run(rig, v)
    assert v.last.cm.spoof and not v.last.gates_ok
    d = v.seam.auth._last_result
    assert d.modality_scores["voice"]["score"] is None and not d.modality_scores["voice"]["conf"]


def test_judge_bands():
    r = CmResult("ok", 0.2, False, "x")
    assert judge(r, 0.5).spoof and not judge(r, 0.5).unsure and not judge(r, 0.5).hard_spoof
    j = judge(r, 0.5, hard_floor=0.1)
    assert j.spoof and j.unsure and not j.hard_spoof
    j = judge(CmResult("ok", 0.05, False, "x"), 0.5, hard_floor=0.1)
    assert j.spoof and j.hard_spoof and not j.unsure
    assert not judge(CmResult("ok", 0.9, False, "x"), 0.5, 0.1).spoof
    assert judge(CmResult("unavailable"), 0.5).status == "unavailable"


async def test_soft_band_steps_up_instead_of_rejecting(rig):  # noqa: F811
    rig.cfg.cm_mode = "gate"
    rig.cfg.cm_hard_floor = 0.1
    rig.host.ctx["known"] = False
    rig.host.otp_ok = False
    v = rig.build(cm=FakeCm(CmResult("ok", 0.3, False, "x")))
    await _run(rig, v)
    assert v.last.cm.unsure and "unsure" in v.last.failures[0]
    assert rig.host.otp_calls == 1                                  # went to DriveAuth's step-up
    d = v.seam.auth._last_result
    assert d.modality_scores["voice"]["score"] is None


async def test_below_hard_floor_rejects_without_step_up(rig):  # noqa: F811
    rig.cfg.cm_mode = "gate"
    rig.cfg.cm_hard_floor = 0.1
    rig.host.ctx["known"] = False
    v = rig.build(cm=FakeCm(CmResult("ok", 0.02, False, "x")))
    r = await _run(rig, v)
    assert not r.success and v.last.cm.hard_spoof and rig.host.otp_calls == 0


async def test_advisory_score_is_logged_but_voice_still_counts(rig, caplog):  # noqa: F811
    # default mode: a low anti-spoof score is recorded, the digit challenge + speaker match + DriveAuth decide
    rig.cfg.cm_hard_floor = 0.1
    v = rig.build(cm=FakeCm(CmResult("ok", 0.02, False, "x")))
    with caplog.at_level("INFO", logger="driveos.biometrics.verifier"):
        await _run(rig, v)
    assert v.last.cm.status == "advisory" and v.last.gates_ok and not v.last.cm.hard_spoof
    d = v.seam.auth._last_result
    assert d.modality_scores["voice"]["score"] is not None
    assert "cm_p=0.02" in caplog.text


# ------------------------------------------------------------------ digit challenge
@pytest.mark.parametrize("text,ok", [
    ("four two seven nine", True), ("for two seven nine", True), ("4, 2 7 9.", True), ("4279", True),
    ("forty-two seventy-nine", True), ("four to seven nine", True), ("four two seven eight", False),
])
def test_challenge_matching_uses_shared_otp_parser(text, ok):
    assert challenge_matches(text, "4279") is ok


async def test_digit_asr_gets_digit_prompt(rig):  # noqa: F811
    seen = {}

    class Spy:
        async def transcribe(self, pcm16, *, vocabulary=""):
            seen["vocab"] = vocabulary
            return " ".join(rig.state["challenge"])

    v = rig.build()
    v.stt = Spy()
    r = await v.verify(20)
    assert seen["vocab"] == DIGIT_PROMPT and r.success


def test_whisper_beam_size_is_passed(monkeypatch):
    import sys
    import types

    from driveos.config import VoiceConfig
    from driveos.voice import stt as smod

    calls = {}

    class M:
        def transcribe(self, audio, **kw):
            calls.update(kw)
            return [], None

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = object
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setattr(smod, "load_whisper", lambda *a, **k: M())
    s = smod.WhisperSTT(VoiceConfig(), beam_size=5)
    s._run(b"\0\0" * 1600, DIGIT_PROMPT)
    assert calls["beam_size"] == 5 and calls["initial_prompt"] == DIGIT_PROMPT
    assert smod.WhisperSTT(VoiceConfig())._run(b"\0\0" * 1600, "") == "" and calls["beam_size"] == 1


# ------------------------------------------------------------------ voice-check
def test_suggest_threshold():
    assert vc.suggest_threshold([0.9] * 6) == 0.5                  # never above the default cut-off
    assert vc.suggest_threshold([0.3, 0.2, 0.25, 0.28, 0.22, 0.26]) == pytest.approx(0.2 - 0.05 + 0.0, abs=0.02)
    assert vc.suggest_threshold([0.01, 0.02]) == 0.0
    assert vc.suggest_threshold([]) is None


class _Emb:
    name, dim = "fake", 8

    def embed(self, audio):
        v = np.zeros(8, np.float32)
        v[0] = 1.0
        return v


class _Cm:
    """Genuine takes (loud) read high on ch1 and low on ch0; the replay (quiet) is low everywhere."""
    name = "fake"

    def check(self, audio):
        rms = float(np.sqrt(np.mean(np.square(audio))))
        return CmResult("ok", min(0.99, rms * 4), False, "")


def _cap(level, ch1_level):
    a = speechlike()
    a = a / np.abs(a).max() * level
    return Capture(a, a / np.abs(a).max() * ch1_level, True, 3.0)


async def test_voice_check_summary_and_suggestions(tmp_path):
    settings = Settings()
    settings.biometrics.home = tmp_path
    tpl_vec = np.zeros(8, np.float32)
    tpl_vec[0] = 1.0
    tpl = VoiceTemplate("driver1", "A", "fake", tpl_vec, tpl_vec[None, :], True,
                        {"slope": 12.0, "mid": 0.45, "label": "demo-calibrated"})
    caps = iter([_cap(0.05, 0.2)] * 4 + [_cap(0.01, 0.01)] * 3)

    async def capture(src, **kw):
        return next(caps)

    async def ask(p):
        return ""

    async def asr(pcm):
        return "four two seven nine"

    lines: list[str] = []
    rc = await vc.run_check(settings, n=4, replay=True, apply=True, source=object(), embedder=_Emb(), cm=_Cm(),
                            asr=asr, tpl=tpl, ask=ask, out=lines.append, capture=capture)
    text = "\n".join(lines)
    assert rc == 0
    assert "Summary" in text and "suggested" in text and "demo calibration" in text and "not certification" in text
    assert "ch0" in text and "ch1" in text and "mix" in text and "replay max" in text
    # no content: neither the transcript nor any digit challenge is printed
    assert "four two seven nine" not in text
    cal = CmCalibration.load(tmp_path, "driver1")
    assert set(cal.thresholds) == {"ch0", "ch1", "mix"} and cal.n_genuine == 4
    assert oct(CmCalibration.path(tmp_path, "driver1").stat().st_mode & 0o777) == "0o600"
    s = vc.summarize([])
    assert s == {}


async def test_voice_check_without_apply_changes_nothing(tmp_path):
    settings = Settings()
    settings.biometrics.home = tmp_path
    v = np.zeros(8, np.float32)
    v[0] = 1.0
    tpl = VoiceTemplate("driver1", "A", "fake", v, v[None, :], True, {})

    async def capture(src, **kw):
        return _cap(0.05, 0.2)

    async def ask(p):
        return ""

    lines: list[str] = []
    rc = await vc.run_check(settings, n=3, replay=False, apply=False, source=object(), embedder=_Emb(), cm=_Cm(),
                            asr=None, tpl=tpl, ask=ask, out=lines.append, capture=capture)
    assert rc == 0 and not CmCalibration.path(tmp_path, "driver1").exists()
    assert "no replay was measured" in "\n".join(lines)


def test_recommend_channel_prefers_separation():
    def t(kind, c0, c1):
        return vc.Take(kind, 0.7, 0.9, {"ch0": c0, "ch1": c1, "mix": None}, None, 20.0, 3.0)
    takes = [t("genuine", 0.1, 0.9)] * 3 + [t("replay", 0.1, 0.1)] * 2
    assert vc.recommend_channel(vc.summarize(takes)) == "ch1"


def test_cli_wires_voice_check():
    from driveos import cli

    called = {}
    import driveos.biometrics.voice_check as m

    m_orig = m.cmd_voice_check
    m.cmd_voice_check = lambda a, s: called.setdefault("a", a) and 0
    try:
        assert cli.main(["voice-check", "--selftest", "--takes", "4", "--replay"]) == 0
    finally:
        m.cmd_voice_check = m_orig
    a = called["a"]
    assert a.selftest and a.replay and a.takes == 4 and not a.apply

