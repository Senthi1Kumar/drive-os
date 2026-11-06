"""``driveos enroll``: prompts phrases on the terminal, records from the mic, reports quality and template stats."""

from __future__ import annotations

import asyncio

import numpy as np

from ..config import Settings
from .capture import capture_utterance
from .cohort import load_cohort
from .enroll import MIN_UTTERANCES, PHRASES, TARGET_UTTERANCES, EnrollmentSession, NotEnoughSpeech
from .store import TemplateStore
from .sv import build_embedder


def list_mics() -> int:
    try:
        import sounddevice as sd
    except Exception as e:
        print(f"sounddevice unavailable: {e}")
        return 1
    devs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    if not devs:
        print("no input devices found")
        return 1
    default = sd.default.device[0]
    for i, d in devs:
        mark = "*" if i == default else " "
        print(f"{mark} [{i}] {d['name']}  ({d['max_input_channels']} ch, {int(d['default_samplerate'])} Hz)")
    return 0


async def _record(mic, prompt: str) -> np.ndarray:
    print(f"\n  Next phrase: \"{prompt}\"")
    await asyncio.to_thread(input, "  Read it once to yourself, then press Enter and say it out loud... ")
    print("  listening (stops after you finish speaking)")
    cap = await capture_utterance(mic, start_timeout_s=10.0, max_s=10.0, end_silence_ms=900, min_speech_s=1.5)
    if not cap.voiced:
        print("  didn't hear a full phrase; let's try that one again")
    return cap.ch0


async def _run(settings: Settings, name: str, driver_id: str, n_target: int) -> int:
    from ..voice.audio import Microphone

    cfg = settings.biometrics
    embedder = build_embedder(cfg)
    if embedder is None:
        print("Speaker model missing. Run: driveos fetch-models")
        return 1
    print("DriveOS voice enrollment")
    print("  Your voice will be recorded and turned into a numeric voiceprint stored encrypted on this device.")
    print("  Recordings are not kept. You can delete the voiceprint at any time with: driveos enroll --delete")
    answer = await asyncio.to_thread(input, "  Do you consent to enroll your voice for payment verification? [yes/no] ")
    if answer.strip().lower() not in ("y", "yes"):
        print("Consent not given. Nothing was recorded.")
        return 2
    cohort = load_cohort(cfg.models_dir, embedder.name)
    if cohort is None:
        print("  no calibration cohort (run: driveos fetch-models); using the prior calibration")
    sess = EnrollmentSession(embedder, TemplateStore(cfg.home), driver_id=driver_id, name=name, consent=True,
                             cohort=cohort)
    mic = Microphone(settings.voice.input_device, settings.voice.sample_rate)
    await mic.start()
    print(f"  microphone: {mic.channels} channel(s)")
    try:
        attempts = 0
        while len(sess.accepted) < n_target and attempts < n_target + 4:
            phrase = PHRASES[attempts % len(PHRASES)].format(name=name)
            audio = await _record(mic, phrase)
            r = sess.add(audio)
            attempts += 1
            q = r.quality
            print(f"  utterance {attempts}: {'OK' if r.accepted else 'REJECTED'}  speech {q.voiced_s:.1f}s  "
                  f"SNR {q.snr_db:.0f} dB  clipping {q.clip_frac * 100:.1f}%")
            for why in q.reasons:
                print(f"    - {why}; let's repeat that one")
        res = sess.finalize()
    except NotEnoughSpeech as e:
        print(f"Enrollment incomplete: {e}")
        return 1
    finally:
        await mic.stop()
    t = res.template
    print("\nEnrolled.")
    print(f"  driver {t.driver_id} ({t.name}), model {t.model}, {res.kept} utterances kept")
    if res.dropped_outliers:
        print(f"  dropped as outliers: utterance(s) {[i + 1 for i in res.dropped_outliers]}")
    print(f"  pairwise cosine: mean {res.pairwise_mean:.3f}, min {res.pairwise_min:.3f}")
    print(f"  calibration: {res.calibration.describe()}")
    print("  thresholds are demo-calibrated on this enrollment, not certified")
    return 0


def enroll_face(a, settings: Settings, client=None, ask=input) -> int:
    from .f455 import F455Client, FaceUserMap, new_user_id

    cfg = settings.biometrics
    driver_id = a.driver_id or cfg.driver_id
    client = client or F455Client(cfg.f455_lib)
    users = FaceUserMap(cfg.home)
    if not client.device_present():
        print("No F455 face camera found (see docs/f455.md).")
        return 1
    if a.delete:
        uid = users.get(driver_id)
        if uid and users.is_linked(uid):
            users.drop(driver_id)
            print(f"face for {driver_id} unlinked (the camera entry {uid} belongs to another app and was kept)")
            return 0
        if uid:
            client.remove_user(uid)
            users.drop(driver_id)
        print(f"face for {driver_id} deleted")
        return 0
    if not a.name:
        print("--name <driver> is required")
        return 2
    print("DriveOS face enrollment")
    print("  Your face template is created and stored encrypted inside the F455 camera; DriveOS keeps only an id.")
    if ask("  Do you consent to enroll your face for payment verification? [yes/no] ").strip().lower() not in ("y", "yes"):
        print("Consent not given. Nothing was captured.")
        return 2
    uid = new_user_id(a.name)
    print("  Look at the camera...")
    res = client.enroll(uid, 30.0)
    if res.status == "DuplicateFaceprints":
        return _link_existing_face(client, users, driver_id, a.name, ask)
    if not res.matched:
        print(f"Enrollment failed: {res.status}")
        return 1
    old = users.get(driver_id)
    users.set(driver_id, uid)
    users.set_name(driver_id, a.name)
    if old and old != uid and not users.is_linked(old):
        client.remove_user(old)
    print(f"Enrolled {a.name} as {uid} for driver {driver_id}.")
    return 0


def _link_existing_face(client, users, driver_id: str, name: str, ask) -> int:
    """The camera already stores this face (e.g. from the AI Box). Identify which entry it is and link it."""
    print("  This face is already stored in the F455 (for example from the AI Box).")
    print("  Keep looking at the camera to identify which stored entry is yours...")
    res = client.authenticate(10.0)
    if not res.matched or not res.user_id:
        print(f"Could not identify the stored face: {res.status}")
        return 1
    q = f"  The camera recognised you as '{res.user_id}'. Use it for {name} ({driver_id})? [yes/no] "
    if ask(q).strip().lower() not in ("y", "yes"):
        print("Not linked. Nothing was changed.")
        return 1
    users.link(driver_id, res.user_id)
    users.set_name(driver_id, name)
    print(f"Linked {name} to the existing camera entry {res.user_id} for driver {driver_id} (the entry is kept).")
    return 0


def cmd_enroll(a, settings: Settings) -> int:
    if a.list_mics:
        return list_mics()
    if getattr(a, "face", False):
        return enroll_face(a, settings)
    cfg = settings.biometrics
    driver_id = a.driver_id or cfg.driver_id
    store = TemplateStore(cfg.home)
    if a.delete:
        store.delete(driver_id)
        print(f"voiceprint for {driver_id} deleted")
        return 0
    if not a.name:
        print("--name <driver> is required")
        return 2
    try:
        return asyncio.run(_run(settings, a.name, driver_id, max(MIN_UTTERANCES, min(a.utterances, 6))))
    except KeyboardInterrupt:
        print("\nEnrollment cancelled. Nothing was saved.")
        return 130


__all__ = ["cmd_enroll", "list_mics", "TARGET_UTTERANCES"]
