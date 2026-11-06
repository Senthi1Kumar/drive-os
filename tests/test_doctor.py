from pydantic import SecretStr

from driveos.cli import _assistant_label
from driveos.config import Settings


def test_doctor_legacy_llm_engine_uses_openrouter_without_stale_attribute():
    settings = Settings()
    settings.nlu.engine = "llm"
    settings.nlu.openrouter.api_key = SecretStr("test-key")
    assert _assistant_label(settings) == "OpenRouter agent + on-device rules"


def test_doctor_reports_harness_agent_when_configured():
    settings = Settings()
    settings.nlu.engine = "harness"
    settings.nlu.openrouter.api_key = SecretStr("test-key")
    assert _assistant_label(settings) == "OpenRouter agent + on-device rules"


def test_doctor_reports_rules_without_an_agent_key():
    settings = Settings()
    settings.nlu.engine = "rules"
    assert _assistant_label(settings) == "on-device rules"
