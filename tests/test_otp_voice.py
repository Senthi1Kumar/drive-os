"""Entering the one-time code by voice: the digit parser, the OTP-pending turn rules, attempts, no leaks."""

from __future__ import annotations

import asyncio
import logging

import pytest

from driveos.agent.intents import Context, IntentParser, is_approval, parse_code
from driveos.wallet.auth import OtpService, PhoneNotificationDelivery
from tests.test_biometric_checkout import ScriptedVerifier, _auth, _checkout
from tests.test_e2e import _wait

p = IntentParser()
OTP = Context(view="checkout", checkout_stage="otp", otp_length=6)


# ---------------------------------------------------------------- the digit parser
@pytest.mark.parametrize("spoken,code", [
    ("four seven two nine one zero", "472910"),
    ("472910", "472910"),
    ("it's 4 7 2 9 1 0", "472910"),
    ("the code is four seven two nine one oh", "472910"),
    ("4 7 2 9 1 oh", "472910"),
    ("double five three one eight two", "553182"),
    ("triple seven one two three", "777123"),
    ("five five double three one eight", "553318"),
    ("472-910", "472910"),
    ("4,729,10", "472910"),
    ("um, four, seven, two, nine, one, zero, thanks", "472910"),
    ("four seven to nine one zero", "472910"),
    ("for seven two nine one zero", "472910"),
    ("oh four seven two nine one", "047291"),
    ("one two three four five ate", "123458"),
    ("O four seven two nine one", "047291"),
])
def test_parse_code(spoken, code):
    assert parse_code(spoken, 6) == code


def test_oh_that_is_only_a_filler_is_not_a_digit():
    assert parse_code("oh it's four seven two nine one zero", 6) == "472910"
    assert parse_code("oh I think it is 4 7 2 9 1 0", 6) == "472910"


def test_homophones_alone_are_not_digits():
    assert parse_code("I'll read it to you for a second", 6) == ""
    assert parse_code("approve", 6) == ""


def test_parse_keeps_what_it_heard_when_too_short_or_long():
    assert parse_code("four seven two nine", 6) == "4729"
    assert parse_code("4 7 2 9 1 0 5", 6) == "4729105"


# ---------------------------------------------------------------- the intent parser at the OTP stage
def test_digits_at_otp_stage_are_a_code():
    i = p.parse("it's 4 7 2 9 1 0", OTP)
    assert i.name == "otp_code" and i.slots["code"] == "472910"


def test_short_digit_utterance_asks_again_without_submitting():
    i = p.parse("four seven two nine", OTP)
    assert i.name == "otp_partial" and i.slots["heard"] == 4 and i.slots["need"] == 6


def test_too_many_digits_is_not_guessed():
    i = p.parse("4 7 2 9 1 0 5 5", OTP)
    assert i.name == "otp_partial" and i.slots["heard"] == 8


@pytest.mark.parametrize("text", ["approve", "yes", "confirm", "yeah", "i approve", "go ahead", "pay", "do it"])
def test_approval_words_at_otp_stage_prompt_for_the_code(text):
    assert p.parse(text, OTP).name == "otp_prompt"


@pytest.mark.parametrize("text", ["cancel", "no", "never mind", "cancel it"])
def test_cancel_at_otp_stage_cancels(text):
    assert p.parse(text, OTP).name == "cancel_checkout"


def test_resend_at_otp_stage():
    i = p.parse("send me a new code", OTP)
    assert i.name == "auth_method" and i.slots.get("resend")


@pytest.mark.parametrize("stage", ["choose", "otp", "face", "voice", ""])
@pytest.mark.parametrize("text", ["turn right", "turn right at the next light", "take a right", "right"])
def test_turn_right_never_approves(stage, text):
    assert not is_approval(text)
    assert p.parse(text, Context(view="checkout", checkout_stage=stage)).name not in ("approve", "otp_prompt")


def test_digits_outside_the_otp_stage_are_not_a_code():
    assert p.parse("4 7 2 9 1 0", Context(view="checkout", checkout_stage="choose")).name != "otp_code"
    assert p.parse("4 7 2 9 1 0", Context(view="menu")).name != "otp_code"


# ---------------------------------------------------------------- Nova: OTP pending
async def _pending(settings):
    v = ScriptedVerifier(with_otp=True)
    s, n, sink = await _checkout(settings, v)
    from tests.agent_util import RecSpeaker

    n.speaker = RecSpeaker()
    t = asyncio.ensure_future(n.handle_text("use voice"))
    assert await _wait(lambda: n.shop.checkout and n.shop.checkout.bio, 5)
    v.release.set()
    assert await _wait(lambda: n.phone_note is not None and n.shop.checkout.stage == "otp", 5)
    return s, n, v, t


def spaced(code: str) -> str:
    return " ".join(code)


async def test_spoken_code_is_submitted_and_pays(settings):
    s, n, v, t = await _pending(settings)
    try:
        await n.handle_text(f"it's {spaced(n.phone_note['code'])}")
        await asyncio.wait_for(t, 10)
        assert v.otp_ok is True and n.shop.view == "order" and n.shop.tx is not None
    finally:
        await s.aclose()


async def test_wrong_spoken_code_says_attempts_left_and_decrements(settings):
    s, n, v, t = await _pending(settings)
    try:
        real = n.phone_note["code"]
        wrong = "".join(str((int(c) + 1) % 10) for c in real)
        await n.handle_text(spaced(wrong))
        assert n.shop.checkout.otp.attempts_left == 2
        assert "2 tries left" in n.speaker.said[-1]
        assert "2 tries left" in _auth(n)["detail"]
        await n.handle_text(spaced(wrong))
        assert n.shop.checkout.otp.attempts_left == 1
        assert "1 try left" in n.speaker.said[-1]
        await n.handle_text(f"{real}")           # the right one still works with an attempt left
        await asyncio.wait_for(t, 10)
        assert v.otp_ok is True
    finally:
        await s.aclose()


async def test_three_wrong_spoken_codes_lock_the_challenge(settings):
    s, n, v, t = await _pending(settings)
    try:
        real = n.phone_note["code"]
        wrong = "".join(str((int(c) + 1) % 10) for c in real)
        for _ in range(3):
            await n.handle_text(spaced(wrong))
        await asyncio.wait_for(t, 10)
        assert v.otp_ok is False and n.shop.tx is None
    finally:
        await s.aclose()


async def test_short_utterance_does_not_burn_an_attempt(settings):
    s, n, v, t = await _pending(settings)
    try:
        await n.handle_text("four seven two")
        assert n.shop.checkout.otp.attempts_left == 3
        assert "3 digits" in n.speaker.said[-1] and "6" in n.speaker.said[-1]
        v.release.set()
        n.otp.verify(n.shop.checkout.otp.challenge_id, "0")      # keep the test tidy: nothing else pending
    finally:
        await n.cancel_checkout(quiet=True)
        await s.aclose()


async def test_approve_while_otp_pending_does_not_rescan_or_pay(settings):
    s, n, v, t = await _pending(settings)
    try:
        before = (v.calls, n.shop.checkout.otp.challenge_id)
        for word in ("approve", "yes", "confirm"):
            await asyncio.wait_for(n.handle_text(word), 3)
        assert (v.calls, n.shop.checkout.otp.challenge_id) == before
        assert n.shop.checkout.stage == "otp" and n.shop.tx is None
        assert "code" in n.speaker.said[-1].lower()
    finally:
        await n.cancel_checkout(quiet=True)
        await s.aclose()


async def test_cancel_while_otp_pending_cancels(settings):
    s, n, v, t = await _pending(settings)
    try:
        await asyncio.wait_for(n.handle_text("cancel"), 3)
        assert n.shop.checkout is None
        await asyncio.wait_for(t, 5)
        assert v.otp_ok is False
    finally:
        await s.aclose()


async def test_otp_utterances_never_reach_the_agent(settings):
    class Brain:
        busy = False
        turns: list = []

        async def turn(self, text, intent):
            Brain.turns.append(text)
            return True

        def cancel(self):
            pass

    s, n, v, t = await _pending(settings)
    try:
        n.brain = Brain()
        for text in ("it's 4 7 2 9 1 0", "approve", "four seven", "what is the weather", "blah"):
            await n.handle_text(text)
        assert Brain.turns == []
    finally:
        await n.cancel_checkout(quiet=True)
        await s.aclose()


async def test_code_never_in_logs_or_transcript(settings, caplog):
    caplog.set_level(logging.DEBUG)
    s, n, v, t = await _pending(settings)
    try:
        code = n.phone_note["code"]
        await n.handle_text(f"it's {spaced(code)}")
        await asyncio.wait_for(t, 10)
        text = caplog.text
        assert code not in text and spaced(code) not in text
        assert code not in n.voice.heard and spaced(code) not in n.voice.heard
        assert n.voice.heard == "•" * 6                  # masked, one dot per digit it understood
        assert all(code not in m.text and spaced(code) not in m.text for m in n.transcripts)
    finally:
        await s.aclose()


async def test_stt_is_biased_to_digits_only_while_the_code_is_pending(settings):
    s, n, v, t = await _pending(settings)
    try:
        vocab = n.vocabulary()
        assert "zero one two three four five six seven eight nine" in vocab and "coffee" not in vocab
        await n.cancel_checkout(quiet=True)
        assert "coffee" in n.vocabulary()
    finally:
        await s.aclose()


async def test_on_screen_code_entry_still_works(settings):
    s, n, v, t = await _pending(settings)
    try:
        for ch in n.phone_note["code"]:
            await n.otp_key(ch)
        await asyncio.wait_for(t, 10)
        assert v.otp_ok is True
    finally:
        await s.aclose()


async def test_snapshot_tells_the_sheet_to_say_or_type_the_code(settings):
    s, n, v, t = await _pending(settings)
    try:
        a = _auth(n)
        assert a["stage"] == "otp" and a["voice_entry"] is True
    finally:
        await n.cancel_checkout(quiet=True)
        await s.aclose()


# ---------------------------------------------------------------- the service's attempt limit still holds
async def test_attempt_limit_is_unchanged():
    svc = OtpService(PhoneNotificationDelivery(), max_attempts=3)
    ch = await svc.issue("+15550001111", "x")
    details = [svc.verify(ch.challenge_id, "000000").detail for _ in range(3)]
    assert details == ["mismatch:2", "mismatch:1", "locked"]
