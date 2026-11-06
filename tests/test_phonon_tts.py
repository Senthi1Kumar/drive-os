import asyncio
import sys
import threading
import time
import types

import numpy as np

from driveos.config import VoiceConfig
from driveos.voice import tts
from driveos.voice.tts import PhononSpeaker, Pyttsx3Speaker, SilentSpeaker, build_speaker, split_clauses


class FakeStream:
    sample_rate = 24000

    def __init__(self, n, delay):
        self.n, self.delay = n, delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for _ in range(self.n):
            time.sleep(self.delay)
            yield np.zeros(480, dtype=np.float32)


class FakeModel:
    def __init__(self, n=5, delay=0.0):
        self.calls, self.n, self.delay = [], n, delay

    def stream(self, text, voice=None):
        self.calls.append(text)
        return FakeStream(self.n, self.delay)


class FakeSink:
    def __init__(self, write_delay=0.0):
        self.writes, self.aborted, self.closed, self.write_delay = 0, False, False, write_delay

    def write(self, samples):
        time.sleep(self.write_delay)
        self.writes += 1

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


def test_split_clauses_punctuation_and_word_cap():
    assert split_clauses("I found three shops. Your order is confirmed, pickup soon.") == [
        "I found three shops.", "Your order is confirmed,", "pickup soon."]
    long = "one two three four five six seven eight nine ten"
    assert split_clauses(long) == ["one two three four five six seven eight", "nine ten"]
    assert split_clauses("   ") == []
    assert split_clauses("Hello there") == ["Hello there"]


def test_warmup_once_and_no_playback():
    model, made = FakeModel(), []
    PhononSpeaker(VoiceConfig(), model=model, sink_factory=lambda r: made.append(r))
    assert model.calls == ["Ready."]
    assert made == []


async def test_say_streams_clauses_in_order():
    model, sink = FakeModel(), FakeSink()
    sp = PhononSpeaker(VoiceConfig(), model=model, sink_factory=lambda r: sink)
    await sp.say("Found three shops. Pickup in eight minutes.")
    assert model.calls == ["Ready.", "Found three shops.", "Pickup in eight minutes."]
    assert sink.writes == 10 and sink.closed


async def test_stop_cancels_promptly():
    model, sink = FakeModel(n=200, delay=0.02), FakeSink(write_delay=0.02)
    sp = PhononSpeaker(VoiceConfig(), model=FakeModel(), sink_factory=lambda r: sink)
    sp._model = model
    task = asyncio.create_task(sp.say("a long reply that goes on. and on and on."))
    await asyncio.sleep(0.3)
    t0 = time.perf_counter()
    threading.Thread(target=sp.stop).start()
    await asyncio.wait_for(task, 1.0)
    assert time.perf_counter() - t0 < 0.5
    assert sink.aborted and sink.closed and sink.writes < 100


def test_build_speaker_default_is_phonon(monkeypatch):
    monkeypatch.setattr(tts, "load_phonon", lambda delta=-0.25: FakeModel())
    assert isinstance(build_speaker(VoiceConfig()), PhononSpeaker)


def test_build_speaker_falls_back_to_pyttsx3(monkeypatch):
    monkeypatch.setitem(sys.modules, "ptts", None)
    eng = types.SimpleNamespace(setProperty=lambda *a: None)
    monkeypatch.setitem(sys.modules, "pyttsx3", types.SimpleNamespace(init=lambda: eng))
    assert isinstance(build_speaker(VoiceConfig()), Pyttsx3Speaker)


def test_build_speaker_silent_when_nothing_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "ptts", None)
    monkeypatch.setitem(sys.modules, "pyttsx3", None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert isinstance(build_speaker(VoiceConfig()), SilentSpeaker)
