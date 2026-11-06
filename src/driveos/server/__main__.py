from __future__ import annotations

import argparse
import asyncio
import sys

from ..config import load_settings
from ..core.logging_setup import setup_logging
from ..core.private_fs import tighten_umask
from .runner import serve_forever


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m driveos.server", description="DriveOS headless UI back-end")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-voice", action="store_true", help="no microphone and no speech output")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--time-scale", type=float)
    a = ap.parse_args(argv)
    s = load_settings(**({"mode": "live"} if a.live else {}))
    if a.time_scale:
        s.vehicle.simulator_time_scale = a.time_scale
    tighten_umask()
    setup_logging(s.log_level, s.log_json, s.logging.transcripts)
    return asyncio.run(serve_forever(s, voice=not a.no_voice, host=a.host, port=a.port))


if __name__ == "__main__":
    sys.exit(main())
