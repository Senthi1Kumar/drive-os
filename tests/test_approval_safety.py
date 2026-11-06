from __future__ import annotations

import pytest

from driveos.agent.intents import Context, IntentParser

p = IntentParser()
CO = Context(view="checkout", checkout_stage="choose")


@pytest.mark.parametrize("text", [
    "turn right at the light", "ok so what's next", "I'll pay later", "don't pay", "no don't confirm",
    "not yet", "sure why not let me think about it first", "right", "ok", "okay", "pay later",
    "and get him down at home yes can you find me coffee nearby",
])
def test_incidental_speech_does_not_approve(text):
    assert p.parse(text, CO).name != "approve"


@pytest.mark.parametrize("text", [
    "approve", "approve it", "confirm", "confirm payment", "yes please", "yes", "pay now", "yes pay",
    "yes confirm", "go ahead and pay", "Approve.",
])
def test_explicit_approval(text):
    assert p.parse(text, CO).name == "approve"
