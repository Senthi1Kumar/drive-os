from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from driveos.core import logging_setup
from driveos.core.keys import FileKeyStore
from driveos.core.private_fs import harden_tree, private_file
from driveos.wallet.ledger import Ledger


def mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_harden_tree_fixes_existing_modes(tmp_path):
    d = tmp_path / "home"
    (d / "voice").mkdir(parents=True)
    f = d / "voice" / "x.tpl"
    f.write_text("x")
    os.chmod(f, 0o644)
    os.chmod(d / "voice", 0o755)
    harden_tree(d)
    assert mode(f) == 0o600 and mode(d / "voice") == 0o700 and mode(d) == 0o700


def test_private_file_creates_and_covers_sidecars(tmp_path):
    p = tmp_path / "a.db"
    private_file(p, create=True)
    (tmp_path / "a.db-wal").write_text("w")
    os.chmod(tmp_path / "a.db-wal", 0o644)
    private_file(p)
    assert mode(p) == 0o600 and mode(tmp_path / "a.db-wal") == 0o600


def test_ledger_files_private(tmp_path):
    d = tmp_path / "data"
    led = Ledger(d / "ledger.sqlite3", FileKeyStore(d / "device_key.pem"))
    assert mode(d / "ledger.sqlite3") == 0o600 and mode(d) == 0o700
    led.append("tx1", "k", {})
    assert mode(d / "ledger.sqlite3") == 0o600


def test_existing_ledger_fixed_at_open(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    keys = FileKeyStore(d / "device_key.pem")
    Ledger(d / "ledger.sqlite3", keys)
    os.chmod(d / "ledger.sqlite3", 0o644)
    Ledger(d / "ledger.sqlite3", keys)
    assert mode(d / "ledger.sqlite3") == 0o600


def test_session_store_private_and_fixed(tmp_path):
    from driveos.agent.harness.memory import SessionStore
    p = tmp_path / "h" / "sessions.db"
    s = SessionStore(p)
    s.close()
    assert mode(p) == 0o600 and mode(p.parent) == 0o700
    os.chmod(p, 0o644)
    SessionStore(p).close()
    assert mode(p) == 0o600


def test_face_user_map_private(tmp_path):
    from driveos.biometrics.f455 import FaceUserMap
    m = FaceUserMap(tmp_path / "h")
    m.set("driver1", "alex_1")
    assert mode(m.path) == 0o600 and mode(m.path.parent) == 0o700
    os.chmod(m.path, 0o644)
    m.drop("nobody")
    m.link("driver1", "alex_1")
    assert mode(m.path) == 0o600


def test_template_store_dirs_private(tmp_path):
    from driveos.biometrics.store import TemplateStore
    t = TemplateStore(tmp_path / "h")
    t._fernet()
    assert mode(t.home) == 0o700 and mode(t.key_path) == 0o600


# ---------------------------------------------------------------- P2
@pytest.fixture
def transcripts():
    logging_setup.set_log_transcripts(False)
    yield logging_setup.set_log_transcripts
    logging_setup.set_log_transcripts(False)


def test_describe_text(transcripts):
    assert logging_setup.describe_text("Can you find a sandwich") == "23 chars"
    transcripts(True)
    assert logging_setup.describe_text("Can you find a sandwich") == "'Can you find a sandwich'"


@pytest.fixture
async def nova(settings):
    from driveos.container import build_services
    settings.vehicle.simulator_time_scale = 25
    s = build_services(settings)
    await s.start()
    yield s.nova
    await s.aclose()


async def test_heard_log_has_no_utterance_by_default(caplog, transcripts, nova):
    caplog.set_level(logging.INFO)
    await nova.handle_text("find me a sandwich please")
    text = caplog.text
    assert "heard 25 chars" in text and "sandwich please" not in text


async def test_heard_log_full_text_with_flag(caplog, transcripts, nova):
    transcripts(True)
    caplog.set_level(logging.INFO)
    await nova.handle_text("find me a sandwich please")
    assert "find me a sandwich please" in caplog.text


async def test_null_speaker_does_not_log_reply(caplog, transcripts):
    from driveos.agent.nova import NullSpeaker
    caplog.set_level(logging.INFO)
    await NullSpeaker().say("Hello Alex your code is 123456")
    assert "Alex" not in caplog.text and "123456" not in caplog.text


def test_settings_logging_flag(monkeypatch):
    from driveos.config import load_settings
    assert load_settings().logging.transcripts is False
    monkeypatch.setenv("DRIVEOS_LOG_TRANSCRIPTS", "1")
    assert load_settings().logging.transcripts is True


# ---------------------------------------------------------------- P3 / P5
def test_retention_purges_old_turns(tmp_path):

    from driveos.agent.harness.memory import SessionStore
    now = 1_000_000_000.0
    st = SessionStore(tmp_path / "s.db", clock=lambda: now)
    sid, _ = st.open_session(1800)
    st.flush()
    for tid, t in (("old", now - 40 * 86400), ("new", now - 86400)):
        st._w("INSERT INTO turn(turn_id, session_id, t_start) VALUES (?,?,?)", (tid, sid, t))
    st.flush()
    st.purge(30, 90)
    st.flush()
    ids = [r[0] for r in st._db.execute("SELECT turn_id FROM turn")]
    assert ids == ["new"]
    st.close()


def test_stt_loader_tries_local_first():
    from driveos.voice.stt import load_whisper
    calls = []

    def factory(name, **kw):
        calls.append(kw["local_files_only"])
        return "model"

    assert load_whisper(factory, "base.en", device="cpu", compute_type="int8") == "model"
    assert calls == [True]


def test_stt_loader_downloads_when_not_cached():
    from driveos.voice.stt import load_whisper
    calls = []

    def factory(name, **kw):
        calls.append(kw["local_files_only"])
        if kw["local_files_only"]:
            raise RuntimeError("not cached")
        return "model"

    assert load_whisper(factory, "base.en", device="cpu", compute_type="int8") == "model"
    assert calls == [True, False]


def test_zdr_in_request_body():
    from driveos.agent.harness.openrouter import Breaker, OpenRouterClient
    from driveos.config import OpenRouterConfig
    for zdr in (True, False):
        c = OpenRouterClient(OpenRouterConfig(zdr=zdr), None, breaker=Breaker(2, 30))
        body = c.build_body([{"role": "user", "content": "hi"}], models=["a/b"], tools=None)
        assert body["provider"].get("zdr", False) is zdr


def test_voice_adapters_never_write_audio():
    """Silero and Smart Turn keep audio and features in RAM: no wav/file writers, no Pipecat debug logger."""
    import re

    root = Path(__file__).resolve().parents[1] / "src" / "driveos" / "voice"
    for name in ("silero_vad.py", "smart_turn.py", "whisper_features.py", "pipeline.py"):
        src = (root / name).read_text()
        assert not re.search(r"\bimport\s+(wave|soundfile)\b|\bopen\(|write_bytes|write_text|np\.save|tofile\(", src), name
        assert "smart_turn_audio_log" not in src and "LOG_DATA" not in src


def test_blank_env_example_keys_mean_no_keys(tmp_path, monkeypatch):
    # the shipped .env.example has empty KEY= lines; copying it as-is must not create fake keys
    import shutil
    from pathlib import Path

    from driveos.config import load_settings

    env = tmp_path / ".env"
    shutil.copy(Path(__file__).resolve().parents[1] / ".env.example", env)
    for k in ("DRIVEOS_MAPBOX__TOKEN", "OPENROUTER_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DRIVEOS_ENV_FILE", str(env))
    monkeypatch.chdir(tmp_path)
    s = load_settings()
    assert s.mapbox.token is None
    assert s.nlu.openrouter.api_key is None
    assert not s.nlu.exa.active
