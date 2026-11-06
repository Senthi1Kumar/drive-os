"""Latency of the voice-first pipeline on a recorded clip: embedding, anti-spoof, ASR, and the full
clip-end -> decision path (ASR | embedding | anti-spoof in parallel, then DriveAuth).

    python scripts/bench_biometrics.py [clip.wav] [--runs 5]

Without a clip, the first LibriSpeech cohort utterance is used. Prints one JSON line.
"""

import argparse
import asyncio
import io
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

SECONDS = 4.0


def pi_state() -> dict:
    out = {}
    for k, cmd in (("temp", ["vcgencmd", "measure_temp"]), ("throttled", ["vcgencmd", "get_throttled"]),
                   ("arm_clock", ["vcgencmd", "measure_clock", "arm"])):
        try:
            out[k] = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:
            pass
    return out


def load_clip(path: str | None) -> np.ndarray:
    import soundfile as sf

    if path:
        x, sr = sf.read(path, dtype="float32")
    else:
        import pyarrow.parquet as pq
        from huggingface_hub import hf_hub_download

        from driveos.biometrics.cohort import FILE, REPO

        row = pq.read_table(hf_hub_download(REPO, FILE, repo_type="dataset")).to_pylist()[1]
        x, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
    if x.ndim > 1:
        x = x[:, 0]
    assert sr == 16000, f"need 16 kHz, got {sr}"
    return x[: int(16000 * SECONDS)]


def ms(f, n):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        f()
        ts.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(ts), 1)


async def main(a) -> None:
    from driveos.biometrics import driveauth_adapter as da
    from driveos.biometrics.calibrate import Calibrator
    from driveos.biometrics.cm import build_cm
    from driveos.biometrics.sv import build_embedder
    from driveos.config import BiometricsConfig, VoiceConfig
    from driveos.voice.stt import build_stt

    global SECONDS
    SECONDS = a.seconds
    cfg = BiometricsConfig(sv_backend=a.sv)
    clip = load_clip(a.clip)
    emb, cm, stt = build_embedder(cfg), build_cm(cfg), build_stt(VoiceConfig(whisper_device="cpu", whisper_model=a.whisper))
    pcm = (clip * 32767).astype(np.int16).tobytes()
    e0 = emb.embed(clip)
    cm.check(clip)
    await stt.transcribe(pcm)                      # warm-up
    res = {"clip_s": round(len(clip) / 16000, 2), "sv": emb.name, "cm": cm.name, "pi_before": pi_state()}
    res["embed_ms"] = ms(lambda: emb.embed(clip), a.runs)
    res["cm_ms"] = ms(lambda: cm.check(clip), a.runs)
    t = time.perf_counter()
    for _ in range(a.runs):
        await stt.transcribe(pcm)
    res["asr_ms"] = round((time.perf_counter() - t) * 1000 / a.runs, 1)

    seam = da.DriveAuthSeam(Path(tempfile.mkdtemp()), "bench")
    seam.auth._profile.seed_mature()
    cal = Calibrator()
    totals = []
    for _ in range(a.runs):
        t = time.perf_counter()
        _, v, c = await asyncio.gather(stt.transcribe(pcm), asyncio.to_thread(emb.embed, clip),
                                       asyncio.to_thread(cm.check, clip))
        p = cal(float(np.dot(e0, v)))
        seam.begin(da.confident_score(p))
        await asyncio.to_thread(seam.authenticate, clip, amount=8.0, beneficiary="b", beneficiary_known=True)
        totals.append((time.perf_counter() - t) * 1000)
    res["clip_end_to_decision_ms"] = [round(x) for x in totals]
    res["clip_end_to_decision_median_ms"] = round(statistics.median(totals))
    res["pi_after"] = pi_state()
    print(json.dumps(res))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", nargs="?")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--sv", default="wespeaker", choices=["redimnet2", "wespeaker"])
    ap.add_argument("--whisper", default="tiny.en")
    ap.add_argument("--seconds", type=float, default=2.5, help="clip length")
    asyncio.run(main(ap.parse_args()))
