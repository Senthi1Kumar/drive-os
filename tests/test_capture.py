"""Biometric capture must ignore short noise blips and keep listening until real, sustained speech."""

from __future__ import annotations

import asyncio

import numpy as np

from driveos.biometrics.capture import capture_utterance
from driveos.voice.audio import FRAME_SAMPLES


def _frame(level: float) -> bytes:
    rng = np.random.default_rng(int(level * 1e6) % 2**32)
    x = rng.normal(0.0, level, FRAME_SAMPLES).clip(-1, 1)
    return (x * 32767).astype(np.int16).tobytes()


class FakeRing:
    """Replays a scripted list of frames, a few per poll, like the live mic ring buffer."""

    def __init__(self, frames: list[bytes], per_poll: int = 5):
        self._frames = frames
        self._per_poll = per_poll
        self._pushed = 0
        self.seq = 0

    def frames_since(self, seq: int):
        end = min(len(self._frames), self._pushed + self._per_poll)
        out = [(i, self._frames[i], self._frames[i]) for i in range(max(seq, 0), end)]
        self._pushed = end
        self.seq = end
        return out


QUIET, LOUD = 0.003, 0.2


def _speechy(frame: bytes) -> bool:
    # stand-in for webrtcvad: anything clearly above the quiet floor "sounds like speech"
    return float(np.abs(np.frombuffer(frame, np.int16)).mean()) > 300


def _run(frames, **kw):
    return asyncio.run(capture_utterance(FakeRing(frames), poll_s=0.0, speech_fn=_speechy, **kw))


def test_short_blip_is_ignored_and_real_speech_is_captured():
    frames = ([_frame(QUIET)] * 25            # 0.5 s room noise
              + [_frame(LOUD)] * 4            # 80 ms keyboard tap
              + [_frame(QUIET)] * 50          # 1 s pause while the driver reads the prompt
              + [_frame(LOUD)] * 125          # 2.5 s of speech
              + [_frame(QUIET)] * 50)         # trailing silence
    cap = _run(frames, min_speech_s=1.0, end_silence_ms=700)
    assert cap.voiced
    assert 2.3 <= cap.seconds <= 3.6           # the speech (plus lead-in/tail), not the 80 ms blip


def test_only_noise_times_out_unvoiced():
    frames = [_frame(QUIET)] * 200 + [_frame(LOUD)] * 3 + [_frame(QUIET)] * 200
    cap = _run(frames, start_timeout_s=3.0, max_s=4.0, min_speech_s=0.6)
    assert not cap.voiced


def test_speech_shorter_than_minimum_is_rejected_then_next_take_wins():
    frames = ([_frame(QUIET)] * 25 + [_frame(LOUD)] * 15 + [_frame(QUIET)] * 50   # 0.3 s "uh"
              + [_frame(LOUD)] * 60 + [_frame(QUIET)] * 50)                         # 1.2 s digits
    cap = _run(frames, min_speech_s=0.6, end_silence_ms=500)
    assert cap.voiced
    assert cap.seconds >= 1.2


def test_two_channels_are_kept_aligned():
    frames = [_frame(QUIET)] * 25 + [_frame(LOUD)] * 80 + [_frame(QUIET)] * 50
    cap = _run(frames, min_speech_s=1.0)
    assert cap.ch1 is not None and len(cap.ch1) == len(cap.ch0)
