"""Smart Turn adapter: left-pad/truncate, feature shape, threshold, serialization, no files written."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from driveos.voice.smart_turn import DEFAULT_ONNX, MAX_SAMPLES, SmartTurnAnalyzer
from driveos.voice.whisper_features import compute_whisper_log_mel_features


class FakeSession:
    def __init__(self, prob: float = 0.9, delay: float = 0.0):
        self.prob = prob
        self.delay = delay
        self.seen: list[np.ndarray] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def run(self, _outputs, inputs):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.seen.append(np.asarray(inputs["input_features"]))
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return [np.array([[self.prob]], dtype=np.float32)]


def _pcm(n: int) -> bytes:
    return (np.arange(n, dtype=np.int16) % 200).tobytes()


def test_short_audio_is_left_padded_to_8s():
    a = np.ones(1000, dtype=np.float32)
    out = SmartTurnAnalyzer.prepare(a)
    assert len(out) == MAX_SAMPLES == 128000
    assert not out[: MAX_SAMPLES - 1000].any() and out[-1000:].all()


def test_long_audio_keeps_the_last_8s():
    a = np.arange(200000, dtype=np.float32)
    out = SmartTurnAnalyzer.prepare(a)
    assert len(out) == 128000 and out[0] == 72000 and out[-1] == 199999


def test_features_shape_and_dtype():
    f = compute_whisper_log_mel_features(np.random.default_rng(0).standard_normal(128000).astype(np.float32))
    assert f.shape == (80, 800) and f.dtype == np.float32


@pytest.mark.parametrize("prob,expected", [(0.5, False), (0.2, False), (0.51, True), (0.99, True)])
def test_threshold_is_strictly_above_half(prob, expected):
    sess = FakeSession(prob)
    assert SmartTurnAnalyzer(session=sess).complete(_pcm(16000)) is expected
    assert sess.seen[0].shape == (1, 80, 800)


def test_empty_audio_is_incomplete_without_inference():
    sess = FakeSession(0.9)
    assert SmartTurnAnalyzer(session=sess).complete(b"") is False
    assert sess.seen == []


def test_concurrent_calls_are_serialized():
    sess = FakeSession(0.9, delay=0.05)
    an = SmartTurnAnalyzer(session=sess)
    threads = [threading.Thread(target=an.complete, args=(_pcm(8000),)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(sess.seen) == 4 and sess.max_active == 1


def test_missing_model_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        SmartTurnAnalyzer(tmp_path / "nope.onnx")


def test_debug_audio_logging_env_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPECAT_SMART_TURN_LOG_DATA", "1")
    monkeypatch.chdir(tmp_path)
    SmartTurnAnalyzer(session=FakeSession(0.9)).complete(_pcm(16000))
    time.sleep(0.1)
    assert list(Path(tmp_path).iterdir()) == []
    assert "smart_turn_audio_log" not in {p.name for p in Path(tmp_path).rglob("*")}


def test_no_pipecat_dependency():
    import driveos.voice.smart_turn as m
    import driveos.voice.whisper_features as w
    for mod in (m, w):
        src = Path(mod.__file__).read_text()
        assert not re.search(r"^\s*(import|from)\s+(pipecat|soxr|transformers|torch)\b", src, re.M)


@pytest.mark.skipif(not DEFAULT_ONNX.exists(), reason="models/smart-turn-v3.2-cpu.onnx not present")
def test_real_model_smoke(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    an = SmartTurnAnalyzer()
    t = np.arange(24000, dtype=np.float32) / 16000
    pcm = (np.sin(2 * np.pi * 150 * t) * 8000).astype(np.int16).tobytes()
    assert isinstance(an.complete(pcm), bool)
    assert 0.0 <= an.last_probability <= 1.0
    assert list(tmp_path.iterdir()) == []
