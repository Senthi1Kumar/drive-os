"""Anti-spoof mode: gate (blocks voice on a spoof flag), advisory (scored and logged, never blocks), off (not run)."""

from __future__ import annotations

from driveos.biometrics.cm import CmResult, apply_mode
from driveos.biometrics.verifier import Evidence, evaluate_gates
from driveos.config import BiometricsConfig


def _ev(cm: CmResult) -> Evidence:
    ev = Evidence(digits="4150")
    ev.digits_ok = True
    ev.cm = cm
    return ev


def test_default_mode_is_advisory():
    # the user's choice after voice-check on the XVF3800: AASIST-L scored the live driver 0.00 on ch0 and ranked a
    # phone replay above live speech on ch1, so it is recorded, not used as a gate
    assert BiometricsConfig().cm_mode == "advisory"


def test_gate_mode_blocks_a_spoof_flag():
    r = apply_mode(CmResult("ok", 0.02), "gate", threshold=0.5)
    assert r.status == "ok" and r.spoof
    assert evaluate_gates(_ev(r), cm_required=False) == ["the audio looks synthetic or replayed"]


def test_advisory_mode_keeps_the_score_but_never_blocks():
    r = apply_mode(CmResult("ok", 0.02), "advisory", threshold=0.5, hard_floor=0.3)
    assert r.status == "advisory" and r.bonafide == 0.02
    assert not r.spoof and not r.hard_spoof and not r.unsure
    assert evaluate_gates(_ev(r), cm_required=True) == []


def test_off_mode_has_no_score_and_never_blocks():
    r = apply_mode(CmResult("ok", 0.02), "off", threshold=0.5)
    assert r.status == "off" and r.bonafide is None and not r.spoof
    assert evaluate_gates(_ev(r), cm_required=True) == []


def test_unavailable_model_still_fails_when_required_in_gate_mode():
    r = apply_mode(CmResult("unavailable"), "gate", threshold=0.5)
    assert evaluate_gates(_ev(r), cm_required=True) == ["the anti-spoof check is unavailable"]


def test_digits_and_injection_still_gate_in_advisory_mode():
    ev = _ev(apply_mode(CmResult("ok", 0.9), "advisory", threshold=0.5))
    ev.digits_ok = False
    assert evaluate_gates(ev, cm_required=False) == ["the spoken digits did not match"]
