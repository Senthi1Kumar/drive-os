"""The headless back-end must run without PySide6 / qasync — checked in a clean interpreter."""

from __future__ import annotations

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import asyncio, json, os, sys, tempfile
    os.environ["DRIVEOS_ENV_FILE"] = os.devnull
    os.environ["DRIVEOS_CONFIG"] = os.path.join(tempfile.mkdtemp(), "none.toml")
    from pathlib import Path
    import driveos.server
    from driveos.config import load_settings
    from websockets.asyncio.client import connect

    async def main():
        s = load_settings(data_dir=Path(tempfile.mkdtemp()))
        s.merchants.sources = ["local"]
        b = driveos.server.build_backend(s, voice=False, port=0)
        await b.start()
        try:
            async with connect(f"ws://127.0.0.1:{b.server.port}/v1/ui") as ws:
                assert json.loads(await ws.recv())["type"] == "hello"
                assert json.loads(await ws.recv())["type"] == "state"
        finally:
            await b.stop()

    asyncio.run(main())
    bad = sorted(m for m in sys.modules if m.split(".")[0] in ("PySide6", "shiboken6", "qasync"))
    assert not bad, bad
    print("OK")
    """
)


def test_server_runs_without_qt():
    r = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr
