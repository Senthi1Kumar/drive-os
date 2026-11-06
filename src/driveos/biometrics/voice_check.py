"""``driveos voice-check``: measures, on the real mic, how the speaker model, the anti-spoof model and the digit
check behave for the genuine driver (and optionally for a replay), then suggests a per-channel anti-spoof threshold.

Everything is held in RAM: no audio, transcript or digit string is written to disk. The only file `--apply` writes
is the 0600 threshold file (numbers only). Thresholds are demo calibration, not certification.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import numpy as np

from ..config import Settings, VoiceConfig
from .calibrate import Calibrator
from .capture import Capture, capture_utterance
from .cm import AASIST_SAMPLES, Countermeasure, build_cm, cm_input
from .cm_calibration import CmCalibration
from .liveness import DIGIT_PROMPT, challenge_matches, generate_challenge
from .quality import assess
from .store import TemplateStore, VoiceTemplate
from .sv import SpeakerEmbedder, build_embedder, cosine

CHANNELS = ("ch0", "ch1", "mix")
PHRASE = "my voice is my passport"
THRESHOLD_MARGIN = 0.05
THRESHOLD_PCT = 5.0
DEFAULT_THRESHOLD = 0.5
Asr = Callable[[bytes], Awaitable[str]]


@dataclass
class Take:
    kind: str                                   # "genuine" | "replay"
    cosine: float
    p: float
    bona: dict[str, float | None]               # per channel; None when the channel is not available
    digits_ok: bool | None                      # None: not a digit take
    snr_db: float
    seconds: float
    timings_ms: dict[str, float] = field(default_factory=dict)


def suggest_threshold(values: list[float], margin: float = THRESHOLD_MARGIN, pct: float = THRESHOLD_PCT,
                      cap: float = DEFAULT_THRESHOLD) -> float | None:
    """5th-percentile genuine bona fide probability minus a margin, never above the default cut-off and never
    negative. With a handful of takes the 5th percentile is close to the minimum, which is the point."""
    if not values:
        return None
    return float(min(cap, max(0.0, float(np.percentile(values, pct)) - margin)))


@dataclass
class ChannelSummary:
    channel: str
    n: int
    minimum: float
    median: float
    p5: float
    suggested: float
    replay_max: float | None
    separated: bool | None                      # replay all below the suggested threshold


def summarize(takes: list[Take]) -> dict[str, ChannelSummary]:
    out: dict[str, ChannelSummary] = {}
    for ch in CHANNELS:
        g = [t.bona[ch] for t in takes if t.kind == "genuine" and t.bona.get(ch) is not None]
        r = [t.bona[ch] for t in takes if t.kind == "replay" and t.bona.get(ch) is not None]
        sug = suggest_threshold(g)
        if sug is None:
            continue
        rmax = max(r) if r else None
        out[ch] = ChannelSummary(ch, len(g), min(g), float(np.median(g)), float(np.percentile(g, THRESHOLD_PCT)),
                                 sug, rmax, (rmax < sug) if rmax is not None else None)
    return out


def recommend_channel(summary: dict[str, ChannelSummary]) -> str | None:
    """The channel with the widest gap between genuine speech and the replay (or, without a replay, the highest
    5th-percentile genuine probability)."""
    if not summary:
        return None
    return max(summary.values(), key=lambda s: (s.p5 - (s.replay_max if s.replay_max is not None else 0.0))).channel


def synthetic_utterance(seconds: float = 4.0, seed: int = 1) -> Capture:
    """Voiced-looking audio for `--selftest`: proves models load and shows timings. Not a speech sample."""
    r = np.random.default_rng(seed)
    t = np.arange(int(16000 * seconds)) / 16000
    env = (np.sin(2 * np.pi * 3 * t) > -0.2).astype(np.float32)
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / 16000
    a = sum(np.sin(k * phase) / k for k in range(1, 9)).astype(np.float32)
    a = (0.15 * a * env + 0.002 * r.standard_normal(len(t))).astype(np.float32)
    b = (0.3 * a + 0.001 * r.standard_normal(len(t))).astype(np.float32)
    return Capture(a, b, True, seconds)


async def measure_take(cap: Capture, *, kind: str, digits: str | None, tpl: VoiceTemplate, calibrator: Calibrator,
                       embedder: SpeakerEmbedder, cm: Countermeasure, asr: Asr | None,
                       ch1_gain: float) -> Take:
    timings: dict[str, float] = {}

    async def timed(name: str, fn, *args):
        t = time.perf_counter()
        try:
            return await asyncio.to_thread(fn, *args)
        finally:
            timings[name] = (time.perf_counter() - t) * 1000

    async def run_asr() -> str:
        if asr is None or digits is None:
            return ""
        t = time.perf_counter()
        try:
            return await asr(cap.pcm16)
        finally:
            timings["asr"] = (time.perf_counter() - t) * 1000

    async def run_cm() -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for ch in CHANNELS:
            if ch != "ch0" and cap.ch1 is None:
                out[ch] = None
                continue
            audio, _ = cm_input(cap.ch0, cap.ch1, ch, ch1_gain)
            r = await timed(f"cm_{ch}", cm.check, audio)
            out[ch] = r.bonafide if r.status == "ok" else None
        return out

    emb, bona, text = await asyncio.gather(timed("embed", embedder.embed, cap.ch0), run_cm(), run_asr())
    cos = cosine(tpl.template, emb)
    ok = challenge_matches(text, digits) if digits is not None else None
    return Take(kind, cos, calibrator(cos), bona, ok, assess(cap.ch0).snr_db, cap.seconds, timings)


def _f(v: float | None, spec: str = ".2f") -> str:
    return "  n/a" if v is None else format(v, spec)


def format_take(i: int, t: Take) -> str:
    digits = {None: "n/a", True: "yes", False: "NO"}[t.digits_ok]
    ms = t.timings_ms
    cm_ms = max((v for k, v in ms.items() if k.startswith("cm_")), default=0.0)
    return (f"  {t.kind[:3]} {i}: cos {t.cosine:.3f}  P {t.p:.2f}  cm ch0 {_f(t.bona['ch0'])} ch1 {_f(t.bona['ch1'])} "
            f"mix {_f(t.bona['mix'])}  digits {digits}  SNR {t.snr_db:.0f} dB  "
            f"[embed {ms.get('embed', 0):.0f} ms, cm {cm_ms:.0f} ms/ch, asr {ms.get('asr', 0):.0f} ms]")


def format_report(takes: list[Take], current_threshold: float, current_channel: str) -> str:
    g = [t for t in takes if t.kind == "genuine"]
    lines = ["", "Summary (anti-spoof bona fide probability per channel)", ""]
    lines.append(f"  {'channel':<8}{'n':>3}{'min':>7}{'median':>8}{'p5':>7}{'replay max':>12}{'suggested':>11}")
    s = summarize(takes)
    for ch in CHANNELS:
        if ch not in s:
            lines.append(f"  {ch:<8}   (no data)")
            continue
        c = s[ch]
        lines.append(f"  {ch:<8}{c.n:>3}{c.minimum:>7.2f}{c.median:>8.2f}{c.p5:>7.2f}"
                     f"{_f(c.replay_max):>12}{c.suggested:>11.2f}")
    if g:
        cos = [t.cosine for t in g]
        pv = [t.p for t in g]
        dg = [t.digits_ok for t in g if t.digits_ok is not None]
        lines += ["", f"  speaker model: cosine min {min(cos):.3f} mean {np.mean(cos):.3f}; "
                      f"calibrated P min {min(pv):.2f} mean {np.mean(pv):.2f}"]
        if dg:
            lines.append(f"  digit check: {sum(dg)}/{len(dg)} matched")
    rec = recommend_channel(s)
    lines.append("")
    lines.append(f"  today: cm_channel = {current_channel}, threshold = {current_threshold:.2f}")
    if rec:
        c = s[rec]
        lines.append(f"  suggested: cm_channel = {rec}, threshold = {c.suggested:.2f} "
                     f"(5th percentile {c.p5:.2f} minus {THRESHOLD_MARGIN:.2f})")
        if c.separated is False:
            lines.append("  WARNING: the replay scored above the suggested threshold on this channel, so the "
                         "anti-spoof model cannot separate replay from live here. Keep the digit challenge and the "
                         "DriveAuth step-up carrying the anti-replay load.")
        elif c.separated is None:
            lines.append("  no replay was measured (run again with --replay) so this says nothing about spoof detection.")
        if c.suggested < 0.1:
            lines.append("  note: a threshold this low makes the anti-spoof model nearly inert on this channel.")
    lines += ["", "  This is demo calibration from a few takes of one speaker, not certification.",
              "  Apply the thresholds with --apply; set the channel with [biometrics] cm_channel in config.toml."]
    return "\n".join(lines)


async def run_check(settings: Settings, *, n: int, replay: bool, apply: bool, source, embedder: SpeakerEmbedder,
                    cm: Countermeasure, asr: Asr | None, tpl: VoiceTemplate,
                    ask: Callable[[str], Awaitable[str]], out: Callable[[str], None] = print,
                    capture=capture_utterance) -> int:
    cfg = settings.biometrics
    cal = Calibrator.from_dict(tpl.calibration)
    takes: list[Take] = []

    async def one(kind: str, digits: str | None, prompt: str, min_speech_s: float) -> Take | None:
        out("\n" + prompt)
        await ask("  Press Enter, then speak... ")
        out("  listening")
        cap = await capture(source, start_timeout_s=10.0, max_s=8.0, end_silence_ms=700, min_speech_s=min_speech_s)
        if not cap.voiced:
            out("  nothing heard; skipping this one")
            return None
        t = await measure_take(cap, kind=kind, digits=digits, tpl=tpl, calibrator=cal, embedder=embedder, cm=cm,
                               asr=asr, ch1_gain=cfg.cm_ch1_gain)
        takes.append(t)
        out(format_take(len([x for x in takes if x.kind == kind]), t))
        return t

    out(f"voice-check: {n} genuine takes, nothing is saved. Say the digits, then the phrase.")
    for _ in range(n):
        digits = generate_challenge(cfg.challenge_digits)
        await one("genuine", digits, f"  Say: {' '.join(digits)}   then: \"{PHRASE}\"", 1.0)
    if replay:
        out("\nReplay: play a recording of yourself from a phone, close to the microphone.")
        for _ in range(max(2, n // 2)):
            await one("replay", None, "  Play the recording once.", 1.0)
    if not any(t.kind == "genuine" for t in takes):
        out("No usable genuine takes were captured.")
        return 1
    threshold = _active_threshold(settings, cfg.driver_id)
    out(format_report(takes, threshold, cfg.cm_channel))
    if apply:
        s = summarize(takes)
        existing = CmCalibration.load(cfg.home, cfg.driver_id)
        th = dict(existing.thresholds)
        th.update({ch: c.suggested for ch, c in s.items()})
        p = CmCalibration(th, sum(1 for t in takes if t.kind == "genuine")).save(cfg.home, cfg.driver_id)
        out(f"\nSaved thresholds to {p} (mode 0600).")
    else:
        out("\nNothing was changed. Re-run with --apply to store these thresholds.")
    return 0


def _active_threshold(settings: Settings, driver_id: str) -> float:
    cfg = settings.biometrics
    v = CmCalibration.load(cfg.home, driver_id).threshold_for(cfg.cm_channel)
    return v if v is not None else cfg.cm_spoof_threshold


def _digit_asr(settings: Settings, model: str | None) -> tuple[Asr | None, str]:
    from ..voice.stt import WhisperSTT

    name = model or settings.biometrics.challenge_whisper_model
    try:
        stt = WhisperSTT(VoiceConfig(whisper_model=name, whisper_device="cpu"),
                         beam_size=settings.biometrics.challenge_beam_size)
    except Exception as e:
        return None, f"digit ASR {name} unavailable: {e}"

    async def asr(pcm: bytes) -> str:
        return await stt.transcribe(pcm, vocabulary=DIGIT_PROMPT)

    return asr, f"digit ASR {name}, beam {settings.biometrics.challenge_beam_size}"


async def selftest(settings: Settings, asr_model: str | None = None, out: Callable[[str], None] = print) -> int:
    """No mic and no enrollment: loads every model and runs one synthetic take so the timings print."""
    cfg = settings.biometrics
    t0 = time.perf_counter()
    embedder = build_embedder(cfg)
    if embedder is None:
        out("Speaker model missing. Run: driveos fetch-models")
        return 1
    t1 = time.perf_counter()
    cm = build_cm(cfg)
    t2 = time.perf_counter()
    asr, asr_note = _digit_asr(settings, asr_model)
    t3 = time.perf_counter()
    out(f"loaded: speaker {embedder.name} {1000 * (t1 - t0):.0f} ms, anti-spoof {cm.name} {1000 * (t2 - t1):.0f} ms, "
        f"{asr_note} {1000 * (t3 - t2):.0f} ms")
    cap = synthetic_utterance()
    emb = await asyncio.to_thread(embedder.embed, cap.ch0)
    tpl = VoiceTemplate("selftest", "selftest", embedder.name, emb, emb[None, :], True)
    digits = "4729"
    for i in range(2):          # the first run pays one-time session warm-up
        t = await measure_take(cap, kind="genuine", digits=digits, tpl=tpl, calibrator=Calibrator(), embedder=embedder,
                               cm=cm, asr=asr, ch1_gain=cfg.cm_ch1_gain)
        out(("warm-up" if i == 0 else "steady ") + format_take(1, t))
    out(f"anti-spoof window {AASIST_SAMPLES / 16000:.2f} s; selftest audio is synthetic, scores are not meaningful")
    return 0 if t.bona["ch0"] is not None else 1


def cmd_voice_check(a, settings: Settings) -> int:
    cfg = settings.biometrics
    if a.selftest:
        return asyncio.run(selftest(settings, a.asr_model))
    store = TemplateStore(cfg.home)
    tpl = store.load(cfg.driver_id)
    if tpl is None:
        print(f"No voice enrollment for {cfg.driver_id}. Run: driveos enroll --name <driver>")
        return 1
    embedder = build_embedder(cfg)
    if embedder is None:
        print("Speaker model missing. Run: driveos fetch-models")
        return 1
    cm = build_cm(cfg)
    if cm.name == "none":
        print(f"Anti-spoof model unavailable: {getattr(cm, 'reason', '')}")
        return 1

    async def main() -> int:
        from ..voice.audio import Microphone

        asr, note = _digit_asr(settings, a.asr_model)
        print(note)
        mic = Microphone(settings.voice.input_device, settings.voice.sample_rate)
        await mic.start()
        print(f"microphone: {mic.device_name or 'default'}, {mic.channels} channel(s)")
        if mic.channels < 2:
            print("  single-channel device: ch1 and mix are not measured")

        async def ask(prompt: str) -> str:
            return await asyncio.to_thread(input, prompt)

        try:
            return await run_check(settings, n=max(3, a.takes), replay=a.replay, apply=a.apply, source=mic,
                                   embedder=embedder, cm=cm, asr=asr, tpl=tpl, ask=ask)
        finally:
            await mic.stop()

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was saved.")
        return 130
