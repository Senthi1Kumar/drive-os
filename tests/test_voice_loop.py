"""Voice loop without a microphone: synthetic voiced audio through VAD, wake spotting and follow-up turns."""

from __future__ import annotations

import asyncio
import math
import struct
import time

from driveos.config import VoiceConfig
from driveos.voice.pipeline import VoicePipeline

SILENCE = b"\x00\x00" * 320


def _voiced(n: int, f0: float = 140.0) -> list[bytes]:
    out, t = [], 0
    for _ in range(n):
        s = []
        for i in range(320):
            x = sum(math.sin(2 * math.pi * f0 * k * (t + i) / 16000) / k for k in range(1, 12))
            s.append(int(9000 * x * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * (t + i) / 16000)) / 2.5))
        t += 320
        out.append(struct.pack("<320h", *s))
    return out


class FakeMic:
    """Plays a script of frames in real time-ish, then silence forever."""

    def __init__(self, script: list[bytes]):
        self.q: asyncio.Queue = asyncio.Queue()
        for f in script:
            self.q.put_nowait(f)

    def feed(self, frames: list[bytes]) -> None:
        for f in frames:
            self.q.put_nowait(f)

    async def start(self): ...
    async def stop(self): ...

    async def frames(self):
        while True:
            try:
                yield self.q.get_nowait()
            except asyncio.QueueEmpty:
                yield SILENCE
            await asyncio.sleep(0.0005)


class FakeSpotter:
    kind = "phrase"

    def __init__(self, heard: str):
        self.heard = heard
        self.calls = 0

    async def check(self, pcm):
        self.calls += 1
        from driveos.voice.wake import split_wake

        woke, rest = split_wake(self.heard, "hey nova", [])
        return woke, rest, self.heard


class FakeStt:
    name = "fake"

    def __init__(self, replies: list[str]):
        self.replies = replies

    async def transcribe(self, pcm, *, vocabulary=""):
        return self.replies.pop(0) if self.replies else ""


class FakeNova:
    def __init__(self, mic: FakeMic):
        self.voice = type("V", (), {"mode": "idle"})()
        self.heard: list[str] = []
        self.modes: list[str] = []
        self.mode_times: list[tuple[str, float]] = []
        self.said: list[tuple[str, bool]] = []
        self.mic = mic
        self.speaker = type("S", (), {"stop": lambda self: None})()
        self.request_listen = None
        self.request_follow_up = None

    def set_voice_available(self, *a, **k): ...
    def vocabulary(self): return ""
    def otp_pending(self): return False

    def set_voice(self, mode, heard=None, *, keep=False):
        self.voice.mode = mode
        self.modes.append(mode)
        self.mode_times.append((mode, time.monotonic()))

    def barge_in(self):
        self.speaker.stop()
        self.barge_ins = getattr(self, "barge_ins", 0) + 1

    async def say(self, text, *, listen=False, transcript=True):
        self.said.append((text, listen))
        if listen and self.request_follow_up:
            self.request_follow_up()

    async def wake(self, rest=""):
        self.modes.append("woke")

    async def handle_text(self, text):
        self.heard.append(text)
        # Nova answers with a question -> follow-up listening without the wake phrase
        if len(self.heard) == 1:
            self.request_follow_up()
            self.mic.feed([SILENCE] * 20 + _voiced(40) + [SILENCE] * 60)


async def _run_until(cond, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


async def test_wake_phrase_with_request_in_one_breath_then_follow_up():
    mic = FakeMic([SILENCE] * 20 + _voiced(60) + [SILENCE] * 40)
    nova = FakeNova(mic)
    pipe = VoicePipeline(VoiceConfig(vad="webrtc", end_silence_ms=400, follow_up_s=3), nova, mic=mic,
                         stt=FakeStt(["the first one"]), wake=FakeSpotter("Hey Nova, find me a coffee"))
    await pipe.start()
    try:
        assert await _run_until(lambda: len(nova.heard) >= 2)
        assert nova.heard == ["find me a coffee", "the first one"]
        assert "woke" in nova.modes and "listening" in nova.modes
    finally:
        await pipe.stop()


async def test_speech_without_wake_phrase_is_ignored():
    mic = FakeMic([SILENCE] * 20 + _voiced(60) + [SILENCE] * 40)
    nova = FakeNova(mic)
    spot = FakeSpotter("so I told him about the coffee")
    pipe = VoicePipeline(VoiceConfig(vad="webrtc", end_silence_ms=400), nova, mic=mic, stt=FakeStt([]), wake=spot)
    await pipe.start()
    try:
        assert await _run_until(lambda: spot.calls >= 1)
        await asyncio.sleep(0.2)
        assert nova.heard == [] and "listening" not in nova.modes
    finally:
        await pipe.stop()


async def test_wheel_button_opens_a_turn():
    mic = FakeMic([SILENCE] * 10)
    nova = FakeNova(mic)
    pipe = VoicePipeline(VoiceConfig(vad="webrtc", end_silence_ms=400), nova, mic=mic, stt=FakeStt(["I need gas"]), wake=None)
    await pipe.start()
    try:
        pipe.listen()
        mic.feed([SILENCE] * 10 + _voiced(40) + [SILENCE] * 40)
        assert await _run_until(lambda: nova.heard == ["I need gas"])
    finally:
        await pipe.stop()


async def test_empty_transcriptions_do_not_cause_repeated_apologies():
    """Empty ASR: at most one non-repeating apology after the wheel button; continuous empties stay silent."""
    mic = FakeMic([SILENCE] * 10)
    nova = FakeNova(mic)
    stt = FakeStt(["", ""])
    pipe = VoicePipeline(VoiceConfig(vad="webrtc", end_silence_ms=400, follow_up_s=1), nova, mic=mic, stt=stt, wake=None)
    await pipe.start()
    try:
        pipe.listen()        # explicit wheel-button turn
        mic.feed([SILENCE] * 5 + _voiced(40) + [SILENCE] * 40)
        assert await _run_until(lambda: len(nova.said) >= 1)
        nova.request_follow_up()     # a continuous (follow-up) turn, no button press
        mic.feed([SILENCE] * 5 + _voiced(40) + [SILENCE] * 40)
        assert await _run_until(lambda: not stt.replies)
        await asyncio.sleep(0.3)     # give any recursive apology loop time to speak again
        assert nova.said == [("I didn't catch that; please try again.", False)]
    finally:
        await pipe.stop()


async def test_empty_transcriptions_rearm_on_a_new_button_press():
    """The one-shot apology latch resets on a new explicit press, so the next button turn may apologize once."""
    mic = FakeMic([SILENCE] * 10)
    nova = FakeNova(mic)
    stt = FakeStt(["", ""])
    pipe = VoicePipeline(VoiceConfig(vad="webrtc", end_silence_ms=400, follow_up_s=1), nova, mic=mic, stt=stt, wake=None)
    await pipe.start()
    try:
        for presses in range(1, 3):
            pipe.listen()
            mic.feed([SILENCE] * 5 + _voiced(40) + [SILENCE] * 40)
            assert await _run_until(lambda p=presses: len(nova.said) >= p)
        await asyncio.sleep(0.3)
        assert nova.said == [("I didn't catch that; please try again.", False)] * 2
    finally:
        await pipe.stop()


async def test_frames_queued_while_speaking_never_enter_the_next_turn():
    """Half-duplex + echo tail: playback frames are dropped, queued echo is drained, and listening
    opens only once echo_tail_ms has elapsed after Nova stops speaking."""
    mic = FakeMic([SILENCE] * 10)
    nova = FakeNova(mic)
    stt = FakeStt(["stale echo"])
    pipe = VoicePipeline(VoiceConfig(vad="webrtc", end_silence_ms=400, echo_tail_ms=400), nova, mic=mic, stt=stt,
                         wake=None)
    mic.drains = 0

    def _drain():
        mic.drains += 1
        while not mic.q.empty():
            mic.q.get_nowait()

    mic.drain = _drain
    await pipe.start()
    try:
        nova.voice.mode = "speaking"
        mic.feed(_voiced(30))            # her own voice leaking back into the mic
        await asyncio.sleep(0.1)
        assert stt.replies == ["stale echo"] and nova.heard == []   # dropped while she speaks
        t0 = time.monotonic()
        pipe.listen()                    # the driver presses the button right as she finishes
        nova.voice.mode = "idle"
        assert await _run_until(lambda: "listening" in nova.modes, timeout=2.0)
        listened_at = next(t for m, t in nova.mode_times if m == "listening")
        assert listened_at - t0 >= 0.3   # the echo tail kept the mic closed first
        assert mic.drains >= 1           # queued echo was drained before listening
        mic.feed([SILENCE] * 400)        # only room tone afterwards: the turn must time out quietly
        assert await _run_until(lambda: pipe.state == "armed", timeout=3.0)
        assert stt.replies == ["stale echo"]   # stale frames never became an utterance
        assert nova.heard == [] and nova.said == []
    finally:
        await pipe.stop()


# ---------------------------------------------------------------- continuous conversation (Silero + Smart Turn)
class FakeSeg:
    """Scripted Silero stand-in: a non-silent 20 ms frame is voiced; `pause_after` silent frames end a pause.
    Mirrors the real segmenter's pause/resume/reset contract."""

    def __init__(self, pause_after: int = 5):
        self.pause_after = pause_after
        self.fed = 0
        self.resets = 0
        self.trig = False
        self.sil = 0
        self.frames: list[bytes] = []
        self.stash: list[bytes] | None = None
        self.resume_pending = False

    @property
    def speaking(self) -> bool:
        return self.trig

    def _audio(self):
        import numpy as np

        return np.frombuffer(b"".join(self.frames), dtype=np.int16).astype(np.float32) / 32768.0

    def feed(self, frame: bytes):
        import numpy as np

        self.fed += 1
        voiced = frame != SILENCE
        events = []
        if voiced:
            self.sil = 0
            if not self.trig:
                self.trig = True
                if self.resume_pending and self.stash is not None:
                    self.resume_pending = False
                else:
                    self.frames = []
                    events.append(("speech_started", None))
            self.frames.append(frame)
        elif self.trig:
            self.frames.append(frame)
            self.sil += 1
            if self.sil >= self.pause_after:
                self.trig = False
                self.stash = list(self.frames)
                events.append(("pause", np.array(self._audio(), dtype=np.float32)))
        return events

    def resume(self):
        if self.stash is None:
            return
        if self.trig:
            self.frames = self.stash + self.frames
            self.stash = None
        else:
            self.resume_pending = True

    def reset(self):
        self.resets += 1
        self.trig, self.sil, self.frames, self.stash, self.resume_pending = False, 0, [], None, False


class FakeTurn:
    def __init__(self, answers: list[bool] | None = None, delay: float = 0.0):
        self.answers = list(answers or [])
        self.delay = delay
        self.calls: list[int] = []

    def complete(self, pcm: bytes) -> bool:
        self.calls.append(len(pcm))
        if self.delay:
            time.sleep(self.delay)
        return self.answers.pop(0) if self.answers else True


class SpyStt:
    name = "spy"

    def __init__(self, replies: list[str], delay: float = 0.0):
        self.replies = list(replies)
        self.delay = delay
        self.lengths: list[int] = []

    async def transcribe(self, pcm, *, vocabulary=""):
        self.lengths.append(len(pcm))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.replies.pop(0) if self.replies else "ok"


class ContNova(FakeNova):
    def __init__(self, mic):
        super().__init__(mic)
        self.otp = False
        self.voice.user_speaking = False

    def otp_pending(self):
        return self.otp

    async def handle_text(self, text):
        self.heard.append(text)


def _cont(mic, nova, *, seg=None, turn=None, stt=None, wake=None, **cfg):
    cfg = {"echo_tail_ms": 50, "end_silence_ms": 400, "conversation_idle_s": 30.0, **cfg}
    seg = seg if seg is not None else FakeSeg()
    turn = turn if turn is not None else FakeTurn()
    pipe = VoicePipeline(VoiceConfig(**cfg), nova, mic=mic, stt=stt or SpyStt(["hello there"]), wake=wake,
                         segmenter=seg, turn=turn)
    return pipe, seg, turn


async def test_one_wake_then_continuous_with_long_utterances_and_no_wake_spotting():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["first request", "second request"])
    spot = FakeSpotter("Hey Nova")
    pipe, seg, turn = _cont(mic, nova, stt=stt, wake=spot)
    await pipe.start()
    try:
        mic.feed([SILENCE] * 20 + _voiced(30) + [SILENCE] * 30)
        assert await _run_until(lambda: spot.calls >= 1)                    # woke once
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed([SILENCE] * 400)                                           # far past the old 6 s start deadline
        mic.feed(_voiced(600) + [SILENCE] * 30)                             # 12 s: past the old 9 s cap
        assert await _run_until(lambda: nova.heard == ["first request"])
        assert stt.lengths[0] > 9 * 32000
        calls = spot.calls
        mic.feed([SILENCE] * 30 + _voiced(40) + [SILENCE] * 30)             # follow-up with no wake phrase
        assert await _run_until(lambda: nova.heard == ["first request", "second request"])
        assert spot.calls == calls and pipe.conversation_active           # the spotter runs only while armed
        assert "listening" in nova.modes and nova.voice.continuous is True
    finally:
        await pipe.stop()


async def test_conversation_closes_after_exactly_the_idle_timeout():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    pipe, seg, turn = _cont(mic, nova, conversation_idle_s=0.6)
    await pipe.start()
    try:
        pipe.listen()
        t0 = time.monotonic()
        assert await _run_until(lambda: pipe.conversation_active)
        await asyncio.sleep(0.3)
        assert pipe.conversation_active                                     # still open before the timeout
        assert await _run_until(lambda: not pipe.conversation_active, timeout=3.0)
        assert 0.55 <= time.monotonic() - t0 <= 1.2
        assert pipe.state == "armed" and nova.voice.mode == "idle" and nova.voice.continuous is False
    finally:
        await pipe.stop()


async def test_end_and_mute_clear_work_and_unmute_returns_to_armed_wake_gating():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    spot = FakeSpotter("so I told him about the coffee")
    stt = SpyStt(["x"])
    pipe, seg, turn = _cont(mic, nova, seg=FakeSeg(pause_after=10**9), stt=stt, wake=spot)
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed(_voiced(10))                                               # mid-utterance
        assert await _run_until(lambda: seg.trig)
        pipe.end()
        assert not pipe.conversation_active and pipe.state == "armed" and not seg.trig
        mic.feed([SILENCE] * 30)
        await asyncio.sleep(0.2)
        assert stt.lengths == [] and nova.heard == []                       # the half-spoken turn was discarded
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        pipe.set_muted(True)
        assert not pipe.conversation_active and pipe.muted
        n = spot.calls
        pipe.listen()
        mic.feed([SILENCE] * 20 + _voiced(40) + [SILENCE] * 40)
        await asyncio.sleep(0.3)
        assert spot.calls == n and not pipe.conversation_active            # muted: not even the wake phrase
        pipe.set_muted(False)
        assert not pipe.conversation_active and pipe.state == "armed"      # unmute does not reopen the old chat
        mic.feed([SILENCE] * 20 + _voiced(60) + [SILENCE] * 40)
        assert await _run_until(lambda: spot.calls > n)                     # wake spotting is back
    finally:
        await pipe.stop()


async def test_incomplete_pause_resumes_same_utterance():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["order a flat white"])
    turn = FakeTurn([False, True])
    pipe, seg, turn = _cont(mic, nova, stt=stt, turn=turn)
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed(_voiced(20) + [SILENCE] * 8)
        assert await _run_until(lambda: len(turn.calls) == 1)
        await asyncio.sleep(0.1)
        assert stt.lengths == [] and nova.heard == []                       # INCOMPLETE: nothing committed
        mic.feed(_voiced(20) + [SILENCE] * 8)                               # the driver carries on
        assert await _run_until(lambda: nova.heard == ["order a flat white"])
        assert len(turn.calls) == 2 and turn.calls[1] > turn.calls[0]       # the second judgement saw both halves
        assert len(stt.lengths) == 1 and stt.lengths[0] >= 40 * 640         # ASR got one merged utterance
    finally:
        await pipe.stop()


async def test_incomplete_pause_that_nobody_continues_is_committed_once(monkeypatch):
    import driveos.voice.pipeline as mod

    monkeypatch.setattr(mod, "INCOMPLETE_COMMIT_S", 0.3)
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["set the temperature"])
    pipe, seg, turn = _cont(mic, nova, stt=stt, turn=FakeTurn([False]))
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed(_voiced(20) + [SILENCE] * 8)
        assert await _run_until(lambda: nova.heard == ["set the temperature"], timeout=3.0)
        assert len(stt.lengths) == 1
    finally:
        await pipe.stop()


async def test_new_speech_discards_a_late_turn_decision():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["one utterance"])
    pipe, seg, turn = _cont(mic, nova, stt=stt, turn=FakeTurn([True, True], delay=0.25))
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed(_voiced(10) + [SILENCE] * 8)
        assert await _run_until(lambda: len(turn.calls) == 1)
        mic.feed(_voiced(10) + [SILENCE] * 8)                               # speech again while inference runs
        assert await _run_until(lambda: nova.heard == ["one utterance"], timeout=5.0)
        await asyncio.sleep(0.4)
        assert len(stt.lengths) == 1 and stt.lengths[0] >= 20 * 640         # the stale COMPLETE never committed
        assert len(turn.calls) == 2                                         # and the worker never backed up
    finally:
        await pipe.stop()


async def test_end_discards_pending_turn_work():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["never"])
    pipe, seg, turn = _cont(mic, nova, stt=stt, turn=FakeTurn([True], delay=0.3))
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed(_voiced(20) + [SILENCE] * 8)
        assert await _run_until(lambda: len(turn.calls) == 1)               # inference pending
        pipe.end()
        modes = len(nova.modes)
        await asyncio.sleep(0.6)
        assert stt.lengths == [] and nova.heard == []
        assert not pipe.conversation_active and pipe.state == "armed"
        assert "listening" not in nova.modes[modes:] and "processing" not in nova.modes[modes:]
    finally:
        await pipe.stop()


async def test_end_during_transcription_drops_the_late_commit():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["never"], delay=0.3)
    pipe, seg, turn = _cont(mic, nova, stt=stt)
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        mic.feed(_voiced(20) + [SILENCE] * 8)
        assert await _run_until(lambda: len(stt.lengths) == 1)
        pipe.end()
        await asyncio.sleep(0.6)
        assert nova.heard == [] and not pipe.conversation_active and pipe.state == "armed"
        assert nova.voice.mode == "idle"
    finally:
        await pipe.stop()


async def test_empty_result_in_a_conversation_is_silent_and_keeps_listening():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["", "", "real words"])
    pipe, seg, turn = _cont(mic, nova, stt=stt)
    await pipe.start()
    try:
        pipe.listen()
        for _ in range(3):
            mic.feed(_voiced(20) + [SILENCE] * 8)
            await asyncio.sleep(0.25)
        assert await _run_until(lambda: nova.heard == ["real words"])
        assert nova.said == [("I didn't catch that; please try again.", False)]   # one explicit-turn hint at most
    finally:
        await pipe.stop()


async def test_spoken_otp_uses_legacy_local_capture():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    nova.otp = True
    stt = SpyStt(["4 7 2 9 1 0"])
    pipe, seg, turn = _cont(mic, nova, stt=stt)
    await pipe.start()
    try:
        pipe.listen()                       # follow-up after the code prompt
        mic.feed([SILENCE] * 10 + _voiced(40) + [SILENCE] * 40)
        assert await _run_until(lambda: nova.heard == ["4 7 2 9 1 0"])
        assert turn.calls == [] and seg.fed == 0                            # Smart Turn / Silero never saw the digits
        assert not pipe.conversation_active
    finally:
        await pipe.stop()


async def test_spoken_otp_inside_a_running_conversation_bypasses_smart_turn(caplog):
    caplog.set_level("DEBUG")
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["4 7 2 9 1 0"])
    pipe, seg, turn = _cont(mic, nova, stt=stt)
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        fed = seg.fed
        nova.otp = True                     # a code was just sent while the chat is open
        mic.feed([SILENCE] * 10 + _voiced(40) + [SILENCE] * 40)
        assert await _run_until(lambda: nova.heard == ["4 7 2 9 1 0"])
        assert turn.calls == [] and seg.fed <= fed + 2
        assert "472910" not in caplog.text and "4 7 2 9 1 0" not in caplog.text
    finally:
        await pipe.stop()


async def test_frames_spoken_over_nova_do_not_start_a_conversation_turn():
    mic = FakeMic([SILENCE] * 10)
    nova = ContNova(mic)
    stt = SpyStt(["echo"])
    pipe, seg, turn = _cont(mic, nova, stt=stt, echo_tail_ms=300)
    await pipe.start()
    try:
        pipe.listen()
        assert await _run_until(lambda: pipe.conversation_active)
        nova.voice.mode = "speaking"
        mic.feed(_voiced(30) + [SILENCE] * 10)
        await asyncio.sleep(0.2)
        nova.voice.mode = "idle"
        mic.feed(_voiced(10) + [SILENCE] * 10)                             # inside the echo tail: ignored
        await asyncio.sleep(0.15)
        assert stt.lengths == [] and nova.heard == []
    finally:
        await pipe.stop()
