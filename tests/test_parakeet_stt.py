"""Parakeet STT: config resolution, whisper fallback, wake aliases, name repair, privacy. No native library needed."""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

import numpy as np
import pytest

from driveos.agent.nova import OTP_VOCABULARY
from driveos.biometrics.liveness import DIGIT_PROMPT
from driveos.config import VoiceConfig
from driveos.core import logging_setup
from driveos.voice import stt as stt_mod
from driveos.voice.entity_fix import entities_from_vocabulary, fix_entities
from driveos.voice.parakeet_stt import ParakeetSTT, resolve_paths
from driveos.voice.wake import PhraseSpotter, split_wake

VOCAB = "Nova, DriveOS, coffee, sandwich, gas station, Face ID, one-time code, confirm, check out, take me there, " \
        "Boba Guys, Peninsula Creamery, Santana Row, Blue Bottle Coffee, flat white, masala chai, Halfmoon, cold brew, " \
        "Embarcadero Road, Mountain View"


class Fn:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *a):
        return self.fn(*a)


class FakeLib:
    def __init__(self, text=b"take me to boba guys", abi=5, load_ok=True):
        self._bufs = []
        self.text, self.freed, self.calls, self.loaded_path = text, [], [], None
        self.parakeet_capi_abi_version = Fn(lambda: abi)
        self.parakeet_capi_load = Fn(self._load if load_ok else (lambda p: None))
        self.parakeet_capi_free = Fn(lambda c: None)
        self.parakeet_capi_free_string = Fn(lambda p: self.freed.append(p))
        self.parakeet_capi_last_error = Fn(lambda c: b"bad gguf")
        self.parakeet_capi_transcribe_pcm = Fn(self._tr)

    def _load(self, path):
        self.loaded_path = path
        return 1234

    def _tr(self, ctx, ptr, n, sr, dec):
        self.calls.append((n, sr, dec))
        buf = ctypes.create_string_buffer(self.text)
        self._bufs.append(buf)
        return ctypes.addressof(buf)


def pcm(seconds=1.0):
    return (np.ones(int(16000 * seconds)) * 1000).astype(np.int16).tobytes()


def make(text=b"take me to boba guys", **kw):
    lib = FakeLib(text, **kw)
    return ParakeetSTT("/x/libparakeet.so", "/x/m.gguf", loader=lambda p: lib), lib


# ------------------------------------------------------------------ config resolution
def test_defaults():
    c = VoiceConfig()
    assert c.stt == "parakeet" and c.wake_engine == "parakeet" and c.parakeet_lib is None


def test_resolution_order(tmp_path, monkeypatch):
    models = tmp_path / "models"
    (models / "parakeet").mkdir(parents=True)
    d_lib, d_model = models / "parakeet" / "libparakeet.so", models / "parakeet" / "tdt_ctc-110m-q8_0.gguf"
    e_lib, e_model, c_lib, c_model = (tmp_path / n for n in ("e.so", "e.gguf", "c.so", "c.gguf"))
    for p in (d_lib, d_model, e_lib, e_model, c_lib, c_model):
        p.write_bytes(b"x")
    monkeypatch.delenv("DRIVEOS_PARAKEET_LIB", raising=False)
    monkeypatch.delenv("DRIVEOS_PARAKEET_MODEL", raising=False)
    assert resolve_paths(VoiceConfig(), models) == (d_lib, d_model)
    monkeypatch.setenv("DRIVEOS_PARAKEET_LIB", str(e_lib))
    monkeypatch.setenv("DRIVEOS_PARAKEET_MODEL", str(e_model))
    assert resolve_paths(VoiceConfig(), models) == (e_lib, e_model)
    assert resolve_paths(VoiceConfig(parakeet_lib=c_lib, parakeet_model=c_model), models) == (c_lib, c_model)
    # a configured path that does not exist falls through to the next source
    assert resolve_paths(VoiceConfig(parakeet_lib=tmp_path / "nope.so"), models)[0] == e_lib


def test_resolution_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("DRIVEOS_PARAKEET_LIB", raising=False)
    monkeypatch.delenv("DRIVEOS_PARAKEET_MODEL", raising=False)
    assert resolve_paths(VoiceConfig(), tmp_path) == (None, None)


# ------------------------------------------------------------------ the adapter
async def test_transcribe_passes_pcm_and_cleans_text():
    s, lib = make(b" take  me <|en|> to boba guys ")
    assert await s.transcribe(pcm(1.0), vocabulary="") == "take me to boba guys"
    assert lib.calls == [(16000, 16000, 0)] and len(lib.freed) == 1


async def test_vocabulary_is_not_a_prompt():
    s, lib = make(b"hello there")
    assert await s.transcribe(pcm(), vocabulary=VOCAB) == "hello there"


async def test_short_audio_is_empty_without_calling_lib():
    s, lib = make()
    assert await s.transcribe(b"\0\0" * 100) == ""
    assert lib.calls == []


def test_abi_too_old_and_load_failure():
    with pytest.raises(RuntimeError, match="ABI"):
        make(abi=4)
    with pytest.raises(RuntimeError, match="bad gguf"):
        make(load_ok=False)


async def test_serialised_across_threads():
    import asyncio
    import threading

    s, lib = make()
    active, peak = 0, 0
    guard = threading.Lock()
    orig = lib.parakeet_capi_transcribe_pcm.fn

    def slow(*a):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        import time
        time.sleep(0.02)
        r = orig(*a)
        with guard:
            active -= 1
        return r

    lib.parakeet_capi_transcribe_pcm.fn = slow
    await asyncio.gather(*(s.transcribe(pcm()) for _ in range(4)))
    assert peak == 1


async def test_names_repaired_through_transcribe():
    s, _ = make(b"take me to bob a guys")
    assert await s.transcribe(pcm(), vocabulary=VOCAB) == "take me to Boba Guys"


async def test_otp_pending_is_never_touched():
    s, _ = make(b"my code is four eight oh one")
    assert await s.transcribe(pcm(), vocabulary=OTP_VOCABULARY) == "my code is four eight oh one"
    assert await s.transcribe(pcm(), vocabulary=DIGIT_PROMPT) == "my code is four eight oh one"


async def test_no_transcript_in_logs(caplog, monkeypatch):
    monkeypatch.setattr(logging_setup, "_log_transcripts", False)
    s, _ = make(b"secret sauce boba guys")
    with caplog.at_level(logging.DEBUG, logger="driveos"):
        await s.transcribe(pcm(), vocabulary=VOCAB)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret" not in text and "boba" not in text.lower() and "chars" in text


# ------------------------------------------------------------------ fallback
def test_fallback_to_whisper_when_missing(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("DRIVEOS_PARAKEET_LIB", raising=False)
    monkeypatch.delenv("DRIVEOS_PARAKEET_MODEL", raising=False)
    built = []
    monkeypatch.setattr(stt_mod, "WhisperSTT", lambda cfg: built.append(cfg) or "whisper-stt")
    with caplog.at_level(logging.WARNING, logger="driveos.stt"):
        assert stt_mod.build_stt(VoiceConfig(), tmp_path) == "whisper-stt"
    assert built[0].whisper_model == "base.en"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "parakeet unavailable" in warns[0].getMessage()


def test_fallback_when_load_fails(tmp_path, monkeypatch):
    (tmp_path / "parakeet").mkdir()
    (tmp_path / "parakeet" / "libparakeet.so").write_bytes(b"not an elf")
    (tmp_path / "parakeet" / "tdt_ctc-110m-q8_0.gguf").write_bytes(b"x")
    monkeypatch.delenv("DRIVEOS_PARAKEET_LIB", raising=False)
    monkeypatch.delenv("DRIVEOS_PARAKEET_MODEL", raising=False)
    monkeypatch.setattr(stt_mod, "WhisperSTT", lambda cfg: "whisper-stt")
    assert stt_mod.build_stt(VoiceConfig(), tmp_path) == "whisper-stt"


def test_parakeet_selected_when_present(tmp_path, monkeypatch):
    (tmp_path / "parakeet").mkdir()
    (tmp_path / "parakeet" / "libparakeet.so").write_bytes(b"x")
    (tmp_path / "parakeet" / "tdt_ctc-110m-q8_0.gguf").write_bytes(b"x")
    monkeypatch.delenv("DRIVEOS_PARAKEET_LIB", raising=False)
    monkeypatch.delenv("DRIVEOS_PARAKEET_MODEL", raising=False)
    lib = FakeLib()
    from driveos.voice import parakeet_stt as pk
    real = pk.ParakeetSTT
    monkeypatch.setattr(pk, "ParakeetSTT", lambda lb, m: real(lb, m, loader=lambda p: lib))
    s = stt_mod.build_stt(VoiceConfig(), tmp_path)
    assert s.name == "parakeet" and lib.loaded_path == str(tmp_path / "parakeet" / "tdt_ctc-110m-q8_0.gguf").encode()


def test_none_and_explicit_whisper(tmp_path, monkeypatch):
    assert stt_mod.build_stt(VoiceConfig(stt="none"), tmp_path) is None
    monkeypatch.setattr(stt_mod, "WhisperSTT", lambda cfg: "whisper-stt")
    assert stt_mod.build_stt(VoiceConfig(stt="whisper"), tmp_path) == "whisper-stt"


# ------------------------------------------------------------------ wake
@pytest.mark.parametrize("heard,rest", [
    ("Hey Nova.", ""), ("Hey, Nova, find me a coffee.", "find me a coffee"), ("Hi Nova", ""),
    ("Hey Noah, take me home", "take me home"), ("Okay Nova.", ""), ("Nova, order a flat white", "order a flat white"),
])
def test_wake_phrases_and_aliases(heard, rest):
    c = VoiceConfig()
    assert split_wake(heard, c.wake_phrase, c.wake_aliases) == (True, rest)


@pytest.mark.parametrize("heard", ["Take me to Novato", "I saw a supernova", "Hey, no.", "Find a coffee"])
def test_wake_rejects_others(heard):
    c = VoiceConfig()
    assert split_wake(heard, c.wake_phrase, c.wake_aliases)[0] is False


async def test_spotter_shares_the_stt_model():
    s, lib = make(b"Hey Nova, find me a coffee.")
    sp = PhraseSpotter(VoiceConfig(), s)
    assert sp.engine == "parakeet" and sp.model is None
    woke, rest, heard = await sp.check(pcm())
    assert woke and rest == "find me a coffee" and heard.startswith("Hey Nova")
    assert len(lib.calls) == 1


# ------------------------------------------------------------------ name repair
ENT = entities_from_vocabulary(VOCAB)


def test_entities_skip_generic_words():
    assert "coffee" not in [e.lower() for e in ENT] and "Boba Guys" in ENT and "Face ID" not in ENT


@pytest.mark.parametrize("heard,fixed", [
    ("take me to bob a guys", "take me to Boba Guys"),
    ("navigate to peninsula cream ery", "navigate to Peninsula Creamery"),
    ("take me to santa na row", "take me to Santana Row"),
    ("half moon please", "Halfmoon please"),
    ("order a flat wite", "order a flat white"),
    ("one masala cha", "one masala chai"),
    ("Take me to Embarkadero Road", "Take me to Embarcadero Road"),
    ("stop at blue bottle cofee in mountain view", "stop at Blue Bottle Coffee in mountain view"),
    ("drive to peninsula creamry on hamilton avenue", "drive to Peninsula Creamery on hamilton avenue"),
])
def test_fuzzy_positive(heard, fixed):
    assert fix_entities(heard, ENT) == fixed


@pytest.mark.parametrize("heard", [
    "take me to the nearest gas station", "find me a good place to eat", "order a large coffee and a muffin",
    "a bottle of water", "take me to the cold lake", "I want a blue jacket",
])
def test_fuzzy_negative(heard):
    assert fix_entities(heard, ENT) == heard


@pytest.mark.parametrize("heard", [
    "my code is four eight two one", "pump 4 forty dollars", "two flat whites", "put 25 dollars of regular on pump 4",
    "it is one oh seven nine",
])
def test_digits_untouched(heard):
    assert fix_entities(heard, ENT + ["Pump Four", "Fifty Dollars"]) == heard


def test_exact_names_unchanged_and_empty_inputs():
    assert fix_entities("take me to Boba Guys", ENT) == "take me to Boba Guys"
    assert fix_entities("", ENT) == "" and fix_entities("hello", []) == "hello"


# ------------------------------------------------------------------ real library (skips when absent)
def _real_paths():
    lib, model = resolve_paths(VoiceConfig())
    return lib, model


@pytest.mark.skipif(None in _real_paths(), reason="libparakeet.so / GGUF not installed")
async def test_real_lib_smoke():
    lib, model = _real_paths()
    s = ParakeetSTT(lib, model)
    try:
        assert await s.transcribe(b"\0\0" * 16000) in ("", ".")      # one second of silence: loads and runs
        assert s.transcribe_sync(b"\0\0" * 100) == ""
    finally:
        s.close()


def test_fetch_model_checks_hash(tmp_path, monkeypatch):
    import huggingface_hub

    from driveos.voice import parakeet_stt as pk

    def fake(repo, name, revision, local_dir):
        p = Path(local_dir) / name
        p.write_bytes(b"corrupt")
        return str(p)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake)
    with pytest.raises(RuntimeError, match="sha256"):
        pk.fetch_model(tmp_path)
    assert os.path.isdir(tmp_path / "parakeet")
