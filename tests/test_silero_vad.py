"""Silero ONNX segmenter: deterministic windowing/pause/resume tests with a fake session,
plus a real-model smoke against models/silero_vad.onnx (no downloads, no files written)."""

from __future__ import annotations

import numpy as np
import pytest

from driveos.voice.silero_vad import (
    CONTEXT,
    STATE_SHAPE,
    WINDOW,
    SileroVadConfig,
    SileroVadSegmenter,
)

RATE = 16000


class FakeSession:
    """Scripted stand-in for ort.InferenceSession: one speech probability per run() call."""

    def __init__(self, probs: list[float] | None = None, default: float = 0.0):
        self.probs = list(probs or [])
        self.default = default
        self.widths: list[int] = []
        self.states: list[tuple[int, ...]] = []
        self.first_context: np.ndarray | None = None

    def run(self, _outputs, inputs):
        x = np.asarray(inputs["input"])
        self.widths.append(x.shape[1])
        state = np.asarray(inputs["state"])
        self.states.append(state.shape)
        if self.first_context is None:
            self.first_context = x[:, :CONTEXT].copy()
        prob = self.probs.pop(0) if self.probs else self.default
        return [np.array([[prob]], dtype=np.float32), state]


def _pcm(n_samples: int) -> bytes:
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def _make(probs: list[float] | None = None, default: float = 0.0, **cfg_kw) -> SileroVadSegmenter:
    cfg = SileroVadConfig(onnx_path="/nonexistent/fake.onnx", **cfg_kw)
    return SileroVadSegmenter(cfg, session=FakeSession(probs, default))


def _feed_windows(seg: SileroVadSegmenter, n: int) -> list:
    events = []
    for _ in range(n):
        events.extend(seg.feed(_pcm(WINDOW)))
    return events


def test_config_defaults_match_brief():
    cfg = SileroVadConfig()
    assert cfg.threshold == 0.55
    assert cfg.min_silence_ms == 600
    assert cfg.min_speech_ms == 400
    assert cfg.speech_pad_ms == 400
    assert cfg.max_utterance_s == 60.0
    assert cfg.onnx_path is None


def test_feed_consumes_audio_in_512_sample_windows():
    seg = _make(default=0.0)
    session = seg._model.session
    seg.feed(_pcm(1000))            # 1000 samples: one full window, 488 buffered
    assert len(session.widths) == 1
    seg.feed(_pcm(1000))            # 2000 total: three windows, 464 buffered
    assert len(session.widths) == 3
    assert all(w == WINDOW + CONTEXT for w in session.widths)
    assert all(s == STATE_SHAPE for s in session.states)
    assert not session.first_context.any()   # 64-sample context starts as zeros


def test_preroll_and_onset_are_retained_in_the_pause_audio():
    # 20 silence windows, 20 voiced windows (640 ms), 20 silence windows (600 ms+ gap).
    probs = [0.0] * 20 + [1.0] * 20 + [0.0] * 20
    seg = _make(probs)
    events = _feed_windows(seg, 60)
    kinds = [k for k, _ in events]
    assert kinds == ["speech_started", "pause"]
    assert events[0][1] is None
    audio = events[1][1]
    pad = RATE * 400 // 1000                       # 6400 samples
    speech = 20 * WINDOW                           # 10240 samples
    # 400 ms pre-roll before the onset plus the speech plus the 400 ms trailing pad.
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
    assert len(audio) == pad + speech + pad        # 23040


def test_600ms_low_probability_gap_is_required_for_pause():
    # A 10-window (320 ms) gap does not endpoint; the utterance continues.
    probs = [1.0] * 20 + [0.0] * 10 + [1.0] * 20 + [0.0] * 20
    seg = _make(probs)
    events = _feed_windows(seg, 70)
    kinds = [k for k, _ in events]
    assert kinds == ["speech_started", "pause"]
    audio = events[1][1]
    assert len(audio) > 2 * 20 * WINDOW   # both voiced halves in one utterance


def test_short_blip_produces_no_asr_worthy_audio():
    # 3 voiced windows (96 ms) clear the threshold but not the 400 ms min-speech gate.
    probs = [1.0] * 3 + [0.0] * 25
    seg = _make(probs)
    events = _feed_windows(seg, 28)
    kinds = [k for k, _ in events]
    assert kinds == ["speech_started"]
    assert all(audio is None for _, audio in events)


def test_resume_preserves_samples_after_an_incomplete_pause():
    script = [0.0] * 20 + [1.0] * 20 + [0.0] * 20 + [1.0] * 20 + [0.0] * 20

    seg = _make(list(script))
    events = _feed_windows(seg, 60)      # speech A, then the 600 ms gap
    assert [k for k, _ in events] == ["speech_started", "pause"]
    first = events[1][1]
    seg.resume()                          # the pause was incomplete: keep the utterance
    events = _feed_windows(seg, 40)      # speech B, then another gap
    kinds = [k for k, _ in events]
    assert kinds == ["pause"]            # no second speech_started: same utterance continued
    continued = events[0][1]
    assert len(continued) == len(first) + 20 * WINDOW + RATE * 400 // 1000

    seg = _make(list(script))
    events = _feed_windows(seg, 60)
    assert [k for k, _ in events] == ["speech_started", "pause"]
    events = _feed_windows(seg, 40)      # no resume(): speech B is a fresh utterance
    kinds = [k for k, _ in events]
    assert kinds == ["speech_started", "pause"]
    fresh = events[1][1]
    assert len(fresh) < len(continued) - 20 * WINDOW


def test_resume_after_speech_already_restarted_merges_the_undecided_pause():
    script = [0.0] * 20 + [1.0] * 20 + [0.0] * 20 + [1.0] * 20 + [0.0] * 20
    seg = _make(list(script))
    events = _feed_windows(seg, 60)
    first = events[1][1]
    events = _feed_windows(seg, 25)               # speech B starts before any turn decision arrived
    assert [k for k, _ in events] == ["speech_started"]
    seg.resume()                                  # the pipeline merges the earlier pause back in
    events = _feed_windows(seg, 15)
    assert [k for k, _ in events] == ["pause"]
    assert len(events[0][1]) >= len(first) + 20 * WINDOW


def test_reset_drops_audio_and_model_state():
    script = [0.0] * 20 + [1.0] * 20 + [0.0] * 20
    seg = _make(list(script) + list(script))
    events = _feed_windows(seg, 60)
    assert [k for k, _ in events] == ["speech_started", "pause"]
    seg.reset()
    assert seg._utterance == [] and seg._stash is None and len(seg._buf) == 0
    events = _feed_windows(seg, 60)      # same script again, from scratch
    kinds = [k for k, _ in events]
    assert kinds == ["speech_started", "pause"]
    assert len(events[1][1]) == 2 * (RATE * 400 // 1000) + 20 * WINDOW


def test_60s_ceiling_emits_exactly_one_speech_stopped():
    seg = _make(default=1.0)             # permanently voiced
    events = []
    for _ in range(22):                  # 2200 windows ≈ 70.4 s, fed 100 windows at a time
        events.extend(seg.feed(_pcm(100 * WINDOW)))
    stopped = [a for k, a in events if k == "speech_stopped"]
    assert len(stopped) == 1
    assert len(stopped[0]) == 60 * RATE
    assert [k for k, _ in events].count("speech_started") >= 1


def test_missing_model_raises_without_downloading(tmp_path):
    missing = tmp_path / "nope.onnx"
    with pytest.raises(FileNotFoundError):
        SileroVadSegmenter(SileroVadConfig(onnx_path=str(missing)))
    assert not missing.exists()




@pytest.mark.skipif(
    not (SileroVadConfig().onnx_path or __import__("pathlib").Path(
        __import__("driveos.voice.silero_vad", fromlist=["DEFAULT_ONNX"]).DEFAULT_ONNX
    ).exists()),
    reason="models/silero_vad.onnx not present",
)
def test_real_model_smoke(tmp_path, monkeypatch):
    """Tone bursts through the real ONNX model: valid probabilities, events, no files written."""
    pytest.importorskip("onnxruntime")
    monkeypatch.chdir(tmp_path)
    seg = SileroVadSegmenter(SileroVadConfig(threshold=0.4))
    t = np.arange(int(0.8 * RATE), dtype=np.float32) / RATE
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)).astype(np.float32)
    speech = (np.sin(2 * np.pi * 140 * t) * env * 0.4).astype(np.float32)
    silence = np.zeros(RATE, dtype=np.float32)     # 1.0 s > 600 ms min_silence
    pcm = np.concatenate([speech, silence])
    i16 = (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes()

    events = []
    for i in range(0, len(i16), 3200):
        events.extend(seg.feed(i16[i : i + 3200]))
        assert 0.0 <= seg.last_prob <= 1.0
    kinds = [k for k, _ in events]
    assert "speech_started" in kinds
    assert "pause" in kinds
    audio = next(a for k, a in events if k == "pause")
    assert audio is not None and len(audio) > 0.4 * RATE
    assert list(tmp_path.iterdir()) == []          # RAM-only: no audio or cache files
