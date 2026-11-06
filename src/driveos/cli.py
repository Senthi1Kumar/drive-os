"""driveos — command line entry point.

  driveos run [--live] [--fullscreen] [--no-voice] [--gps [--gps-source SPEC]] [--script "a|b|..."]
  driveos demo [--loop] [--gps] [--headless]   automated real-time walkthrough
  driveos gps [--source SPEC]                  print live fixes (check a receiver or phone app)
  driveos doctor            check configuration, credentials, audio and models
  driveos ledger verify     verify the signed transaction ledger
  driveos ledger list       recent transactions
  driveos screenshots --out DIR
  driveos mcp-merchant      run the bundled merchant MCP server (stdio)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .config import load_settings
from .core.logging_setup import setup_logging
from .core.private_fs import tighten_umask


def _settings(a):
    overrides = {}
    if getattr(a, "live", False):
        overrides["mode"] = "live"
    s = load_settings(**overrides)
    if getattr(a, "fullscreen", False):
        s.display.fullscreen = True
    if getattr(a, "size", None):
        w, h = a.size.lower().split("x")
        s.display.width, s.display.height = int(w), int(h)
    if getattr(a, "time_scale", None):
        s.vehicle.simulator_time_scale = a.time_scale
    if getattr(a, "gps_source", None):
        s.vehicle.gps_source = a.gps_source
    if getattr(a, "gps", False) or (getattr(a, "gps_source", None) and getattr(a, "cmd", "") == "run"):
        s.vehicle.source = "gps"
    return s


def cmd_run(a) -> int:
    s = _settings(a)
    tighten_umask()
    setup_logging(s.log_level, s.log_json, s.logging.transcripts)
    from .app import run

    script = [x.strip() for x in a.script.split("|")] if a.script else None
    return run(s, script=script, voice=not a.no_voice)


def cmd_demo(a) -> int:
    s = _settings(a)
    s.vehicle.source = "simulator"            # the demo drives itself; --gps only picks the start point
    setup_logging(s.log_level, s.log_json, s.logging.transcripts)
    if a.headless:
        from .container import build_services
        from .demo import run_headless

        return asyncio.run(run_headless(build_services(s), loop=a.loop, gps=a.gps, pace=a.pace))
    from .app import run

    return run(s, voice=not a.no_voice, demo={"loop": a.loop, "gps": a.gps, "pace": a.pace, "exit": a.exit})


def cmd_gps(a) -> int:
    """Print live fixes from a GPS source — the quickest way to check a receiver or phone app."""
    import qasync
    from PySide6.QtCore import QCoreApplication

    from .nav.gps import GpsVehicle

    setup_logging("WARNING")
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def main() -> int:
        g = GpsVehicle(a.source)
        g.status_listeners.append(lambda st: print(f"  status: {st}{' — ' + g.error if g.error else ''}"))

        seen = [0]

        def show(v):
            if g.fixes == seen[0]:
                return
            seen[0] = g.fixes
            acc = f"±{g.accuracy_m:.0f} m" if g.accuracy_m else "±? m"
            sats = f"{g.satellites} sats" if g.satellites else ""
            print(f"  {v.position.lat:10.6f} {v.position.lon:11.6f}  {acc:>9}  {v.speed_mps * 2.23694:5.1f} mph  "
                  f"hdg {v.heading:5.1f}  gear {v.gear}  {sats}  [{g.label}]")

        g.subscribe(show)
        print(f"GPS source: {a.source} — Ctrl+C to stop")
        await g.start()
        try:
            if a.seconds:
                await asyncio.sleep(a.seconds)
            else:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await g.stop()
        print(f"{g.fixes} fixes")
        return 0 if g.fixes else 1

    with loop:
        try:
            return loop.run_until_complete(main())
        except KeyboardInterrupt:
            return 0


def cmd_screenshots(a) -> int:
    s = _settings(a)
    s.voice.enabled = False
    s.data_dir = Path(a.data or (Path(a.out) / ".data"))
    s.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(s.log_level, s.log_json, s.logging.transcripts)
    from .app import run

    return run(s, screenshot_dir=Path(a.out), voice=False)


def cmd_ledger(a) -> int:
    s = load_settings()
    from .core.keys import FileKeyStore
    from .wallet.ledger import Ledger

    led = Ledger(s.data_dir / "ledger.sqlite3", FileKeyStore(s.data_dir / "device_key.pem"))
    if a.action == "verify":
        n = led.verify()
        print(f"ledger OK — {n} entries, chain and signatures valid")
    else:
        for r in led.recent_transactions(a.limit):
            snap = r["snapshot"]
            print(f"{r['created_at'][:19]}  {r['state']:<10} {r['total_minor'] / 100:>8.2f} {r['currency']}  "
                  f"{snap['quote']['merchant_name']}  ({r['tx_id']})")
    return 0


def _assistant_label(s) -> str:
    if s.nlu.agent_enabled and s.nlu.openrouter.api_key:
        return "OpenRouter agent + on-device rules"
    return "on-device rules"


def cmd_doctor(a) -> int:
    s = load_settings()
    setup_logging("WARNING")
    return asyncio.run(_doctor(s))


async def _doctor(s) -> int:
    from .core.http import Http

    ok = True
    http = Http(timeout=6.0)

    def line(name: str, status: str, detail: str = "") -> None:
        mark = {"ok": "✓", "warn": "!", "fail": "✗", "off": "–"}[status]
        print(f"  {mark} {name:<28} {detail}")

    print(f"DriveOS doctor — mode={s.mode}, data={s.data_dir}")
    try:
        from .core.keys import FileKeyStore
        from .wallet.ledger import Ledger

        n = Ledger(s.data_dir / "ledger.sqlite3", FileKeyStore(s.data_dir / "device_key.pem")).verify()
        line("ledger", "ok", f"{n} entries verified")
    except Exception as e:
        ok = False
        line("ledger", "fail", str(e))

    try:
        from .merchants.local_catalog import LocalCatalog
        from .merchants.registry import local_ordering

        cat = LocalCatalog(s.data_dir, include_dataset=s.merchants.include_dataset)
        near = [m for m in cat.near(s.vehicle.start, s.merchants.search_radius_m)]
        line("catalogue", "ok", f"{len(cat.merchants)} restaurants with menus · {len(near)} near "
                                f"{s.vehicle.start_label.split(',')[0]} · ordering: {local_ordering(s)}")
    except Exception as e:
        ok = False
        line("catalogue", "fail", str(e))
    line("face id", "ok" if s.auth.face == "command" else "warn",
         {"command": f"command: {' '.join(s.auth.face_command)}", "simulated": "simulated (demo)",
          "none": "off"}[s.auth.face])
    line("one-time codes", "ok" if s.auth.otp_channel == "sms" and s.auth.twilio_account_sid else "warn",
         "SMS via Twilio" if s.auth.otp_channel == "sms" else "paired-phone banner (demo)")
    try:
        from .nav.gps import _gpsd_reachable

        gpsd = await _gpsd_reachable("127.0.0.1", 2947)
        start = "starts on live GPS" if s.vehicle.source in ("gps", "gpsd") else "starts simulated, Live GPS on the Nav screen"
        line("gps", "ok", f"{start} · source {s.vehicle.gps_source}" + (" · gpsd running" if gpsd else "")
             + " — check with: driveos gps")
    except Exception as e:
        line("gps", "warn", str(e))
    line("assistant", "ok", _assistant_label(s))

    if s.mapbox.token:
        try:
            from .nav.mapbox import MapboxClient

            r = await MapboxClient(s.mapbox, http).directions([s.vehicle.start, s.places[0].location])
            line("mapbox directions", "ok", f"{r.distance_m / 1000:.1f} km route")
        except Exception as e:
            ok = False
            line("mapbox directions", "fail", str(e))
    else:
        line("mapbox", "off", "no token — offline map, estimated routes, no live gas/bar/store search")

    if s.google_places.api_key:
        try:
            from .enrichment.google_places import GooglePlaces

            g = GooglePlaces(s.google_places.api_key.get_secret_value(), http)
            hit = await g.find("coffee", s.vehicle.start, 1500)
            line("google places", "ok", hit["name"] if hit else "no results nearby")
        except Exception as e:
            ok = False
            line("google places", "fail", str(e))
    else:
        line("google places", "off", "no key — no real-world photos/ratings")

    if "square" in s.merchants.sources:
        try:
            from .merchants.square import SquareMerchantProvider

            sq = SquareMerchantProvider(s.square, http)
            for lid in s.square.location_ids:
                loc = await sq._location(lid)
                line(f"square {lid[:8]}", "ok", loc.get("name", ""))
        except Exception as e:
            ok = False
            line("square", "fail", str(e))

    if s.payments.provider == "stripe":
        try:
            r = await http.request("GET", "https://api.stripe.com/v1/balance", service="stripe",
                                   headers={"Authorization": f"Bearer {s.stripe.secret_key.get_secret_value()}"})
            line("stripe", "ok" if r.status_code == 200 else "fail", f"HTTP {r.status_code}")
            ok = ok and r.status_code == 200
        except Exception as e:
            ok = False
            line("stripe", "fail", str(e))
    else:
        line("payments", "ok" if s.payments.provider != "simulated" else "warn", s.payments.provider)

    if "mcp" in s.merchants.sources:
        from .merchants.mcp_provider import McpMerchantProvider

        for cfg in s.merchants.mcp_servers:
            p = McpMerchantProvider(cfg)
            try:
                res = await p.search(near=s.vehicle.start, route=None, category="coffee", query=None, limit=3)
                line(f"mcp {cfg.name}", "ok", f"{len(res)} merchants nearby")
            except Exception as e:
                ok = False
                line(f"mcp {cfg.name}", "fail", str(e))
            finally:
                await p.aclose()

    wake_mod = {"phrase": ("faster_whisper", f'wake phrase "{s.voice.wake_phrase}"'),
                "openwakeword": ("openwakeword", f"wake word {s.voice.wake_model}")}.get(s.voice.wake)
    checks = [("sounddevice", "audio i/o"), ("faster_whisper", "speech-to-text"), ("webrtcvad", "vad")]
    if wake_mod:
        checks.append(wake_mod)
    else:
        line("wake", "off", "push-to-talk only")
    checks += [("kokoro_onnx", "kokoro tts"), ("pyttsx3", "pyttsx3 tts")]
    for mod, label in checks:
        try:
            __import__(mod)
            line(label, "ok", mod)
        except OSError as e:
            hint = "install PortAudio: sudo apt install libportaudio2" if mod == "sounddevice" else str(e)
            line(label, "warn", f"not available ({hint})")
        except Exception as e:
            line(label, "warn", f"not available ({type(e).__name__}) — run scripts/setup to install voice support")
    try:
        from PySide6.QtWebEngineQuick import QtWebEngineQuick  # noqa: F401

        line("qt webengine", "ok", "Mapbox GL map available")
    except Exception as e:
        line("qt webengine", "warn", f"{type(e).__name__} — offline map only")
    await http.aclose()
    print("All checks passed." if ok else "Some checks failed.")
    return 0 if ok else 1


def cmd_fetch_models(a) -> int:
    from .voice.tts import PHONON_MODEL, fetch_phonon_model

    rc = 0
    print(f"Fetching {PHONON_MODEL} ...")
    try:
        cache = fetch_phonon_model()
        print(f"Phonon model cached for offline use under {cache}")
    except Exception as e:
        print(f"failed: {e}")
        rc = 1
    s = _settings(a)
    rc |= _fetch_parakeet(s)
    rc |= _fetch_biometric_models(s)
    return rc


def _fetch_parakeet(s) -> int:
    from .voice import parakeet_stt as pk

    if s.voice.stt != "parakeet":
        return 0
    print(f"Fetching parakeet {pk.MODEL_NAME} from {pk.MODEL_REPO} ...")
    try:
        print(f"  parakeet model: ok {pk.fetch_model(s.biometrics.models_dir)}")
    except Exception as e:
        print(f"  parakeet model: FAILED {e}")
        return 1
    lib, _ = pk.resolve_paths(s.voice, s.biometrics.models_dir)
    if lib is None:
        print("  libparakeet.so is a native build: run scripts/build_parakeet.sh (until then speech uses faster-whisper)")
    return 0


def _fetch_biometric_models(s) -> int:
    import shutil
    from importlib import resources

    from .biometrics import models as bm

    cfg = s.biometrics
    rc = 0
    print(f"Fetching voice biometric models into {cfg.models_dir} ...")
    for name, ok, detail in bm.fetch_all(cfg.models_dir):
        print(f"  {name}: {'ok' if ok else 'FAILED'} {detail}")
        rc |= 0 if ok else 1
    dest = bm.aasist_path(cfg.models_dir)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with resources.as_file(resources.files("driveos.biometrics").joinpath("assets/aasist-l.onnx")) as src:
            shutil.copyfile(src, dest)
        print(f"  aasist-l: ok {dest}")
    except Exception as e:
        print(f"  aasist-l: FAILED {e}")
        rc = 1
    try:
        from .biometrics.models import cohort_path

        cp = cohort_path(cfg.models_dir)
        cp.parent.mkdir(parents=True, exist_ok=True)
        with resources.as_file(resources.files("driveos.biometrics").joinpath("assets/cohort.npz")) as src:
            shutil.copyfile(src, cp)
        print(f"  calibration cohort: ok {cp} (80 LibriSpeech dev-clean clips, 40 speakers, embeddings only)")
    except Exception as e:
        print(f"  calibration cohort: FAILED {e}; enrollment will use the prior calibration")
    return rc


def cmd_enroll(a) -> int:
    from .biometrics.enroll_cli import cmd_enroll as run

    return run(a, _settings(a))


def cmd_voice_check(a) -> int:
    from .biometrics.voice_check import cmd_voice_check as run

    return run(a, _settings(a))


def cmd_mcp(a) -> int:
    from .mcp_server.server import main

    sys.argv = [sys.argv[0], *(["--http", str(a.http)] if a.http else [])]
    main()
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import __version__

    ap = argparse.ArgumentParser(prog="driveos", description="DriveOS — voice-first in-vehicle ordering")
    ap.add_argument("--version", action="version", version=f"DriveOS {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="start the HMI (default)")
    r.add_argument("--live", action="store_true", help="live mode (real merchants/payments as configured)")
    r.add_argument("--fullscreen", action="store_true")
    r.add_argument("--size", help="window size, e.g. 1920x720")
    r.add_argument("--no-voice", action="store_true", help="disable microphone and speech output")
    r.add_argument("--script", help="utterances separated by |, played after start-up")
    r.add_argument("--time-scale", type=float, help="simulator speed-up for demos")
    r.add_argument("--gps", action="store_true", help="follow live GPS instead of simulating the drive")
    r.add_argument("--gps-source", help="auto | qt | gpsd[:host:port] | serial:/dev/ttyACM0 | tcp:host:port | "
                                        "udp:port | replay[:file.nmea|.gpx][@speed] | ip")
    r.set_defaults(fn=cmd_run)

    dm = sub.add_parser("demo", help="automated real-time walkthrough: find, order, approve, drive, pick up")
    dm.add_argument("--loop", action="store_true", help="repeat forever (showroom)")
    dm.add_argument("--gps", action="store_true", help="start the demo at your GPS / IP position")
    dm.add_argument("--gps-source", help="GPS source for --gps (default: auto)")
    dm.add_argument("--pace", type=float, default=1.0, help="pause multiplier (2 = slower, 0.5 = faster)")
    dm.add_argument("--time-scale", type=float, default=2.0, help="driving speed-up (default 2x)")
    dm.add_argument("--headless", action="store_true", help="no window — print the conversation")
    dm.add_argument("--exit", action="store_true", help="close the window when the walkthrough ends")
    dm.add_argument("--no-voice", action="store_true", help="don't speak Nova's replies")
    dm.add_argument("--fullscreen", action="store_true")
    dm.add_argument("--size", help="window size, e.g. 1920x720")
    dm.set_defaults(fn=cmd_demo, live=False)

    gp = sub.add_parser("gps", help="show live fixes from a GPS source (receiver / phone / replay)")
    gp.add_argument("--source", default="auto", help="same values as run --gps-source")
    gp.add_argument("--seconds", type=float, default=0, help="stop after N seconds (default: until Ctrl+C)")
    gp.set_defaults(fn=cmd_gps)

    d = sub.add_parser("doctor", help="check configuration and connectivity")
    d.set_defaults(fn=cmd_doctor)

    fm = sub.add_parser("fetch-models", help="download the speech and voice-biometric models for offline use")
    fm.set_defaults(fn=cmd_fetch_models)

    en = sub.add_parser("enroll", help="enroll a driver's voice for payment verification")
    en.add_argument("--name", help="the driver's name")
    en.add_argument("--driver-id", help="template id (default: biometrics.driver_id)")
    en.add_argument("--utterances", type=int, default=5, help="phrases to record (3-6)")
    en.add_argument("--list-mics", action="store_true", help="list input devices and exit")
    en.add_argument("--delete", action="store_true", help="delete the enrolled voiceprint (with --face: the face)")
    en.add_argument("--face", action="store_true", help="enroll the driver's face on the F455 camera instead")
    en.set_defaults(fn=cmd_enroll)

    vc = sub.add_parser("voice-check", help="measure speaker, anti-spoof and digit checks on this mic; suggest a threshold")
    vc.add_argument("--takes", type=int, default=6, help="genuine takes to record (default 6, min 3)")
    vc.add_argument("--replay", action="store_true", help="also record a phone replay of your voice")
    vc.add_argument("--apply", action="store_true", help="store the suggested per-channel thresholds (0600)")
    vc.add_argument("--asr-model", help="digit ASR model to try, e.g. base.en (default: biometrics.challenge_whisper_model)")
    vc.add_argument("--selftest", action="store_true", help="no mic or enrollment: load models, one synthetic take, print timings")
    vc.set_defaults(fn=cmd_voice_check)

    lg = sub.add_parser("ledger", help="transaction ledger tools")
    lg.add_argument("action", choices=["verify", "list"])
    lg.add_argument("--limit", type=int, default=20)
    lg.set_defaults(fn=cmd_ledger)

    sc = sub.add_parser("screenshots", help="render every screen to PNG")
    sc.add_argument("--out", default="screenshots")
    sc.add_argument("--data", help="data dir for the run (defaults to OUT/.data)")
    sc.add_argument("--size", default="1280x800")
    sc.set_defaults(fn=cmd_screenshots, live=False, fullscreen=False, time_scale=None)

    m = sub.add_parser("mcp-merchant", help="run the bundled merchant MCP server")
    m.add_argument("--http", type=int, help="serve streamable HTTP on this port")
    m.set_defaults(fn=cmd_mcp)

    a = ap.parse_args(argv)
    if not a.cmd:
        a = ap.parse_args(["run", *(argv or sys.argv[1:])])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "json", "os"]
