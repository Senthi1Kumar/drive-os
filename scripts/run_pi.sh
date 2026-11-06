#!/usr/bin/env bash
# One-command DriveOS demo launcher for the Raspberry Pi: back-end (voice on) + LVGL HMI.
#
# Usage: scripts/run_pi.sh [options]
#   --no-voice         start the back-end without microphone or speech output
#   --no-audio-check   skip scripts/pi_audio_setup.sh --check
#   --windowed         do not pass --fullscreen to the HMI
#   --live             pass --live to the back-end (real services instead of the simulator)
#   --duration SEC     quit the HMI after SEC seconds (smoke runs)
#   --smoke            after the back-end is up, drive it over the UI socket (show_tab, find coffee)
#   --build            rebuild the HMI before starting
#   --timeout SEC      seconds to wait for the back-end hello (default 60)
#   -h, --help         this text
#
# Env: DRIVEOS_VENV (default <repo>/.venv), DRIVEOS_PORT (default 8790), DRIVEOS_LOG_DIR,
#      DRIVEOS_MAPLIBRE_PREFIX (MapLibre Native C package root; default hmi_lvgl/.deps/maplibre/<preset>/...).
# The HMI gets DRIVEOS_MAPBOX__TOKEN from <repo>/.env (that one line, HMI process only, never printed).
# The back-end and the HMI each run in their own process group; both groups are stopped on exit.
# shellcheck disable=SC2317  # cleanup and stop_group run from traps
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${DRIVEOS_VENV:-$ROOT/.venv}"
PORT="${DRIVEOS_PORT:-8790}"
LOG_DIR="${DRIVEOS_LOG_DIR:-$HOME/.local/state/drive-os/logs}"
HMI_DIR="$ROOT/hmi_lvgl"
HMI_BIN="$HMI_DIR/build/driveos_hmi"
URL="ws://127.0.0.1:$PORT/v1/ui"

VOICE=1 AUDIO_CHECK=1 FULLSCREEN=1 LIVE=0 DURATION=0 SMOKE=0 FORCE_BUILD=0 WAIT_S=60

say()  { printf '[run_pi] %s\n' "$*"; }
warn() { printf '[run_pi] WARN: %s\n' "$*" >&2; }
die()  { printf '[run_pi] ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --no-voice) VOICE=0 ;;
    --no-audio-check) AUDIO_CHECK=0 ;;
    --windowed) FULLSCREEN=0 ;;
    --live) LIVE=1 ;;
    --duration) shift; DURATION="${1:?--duration needs seconds}" ;;
    --smoke) SMOKE=1 ;;
    --build) FORCE_BUILD=1 ;;
    --timeout) shift; WAIT_S="${1:?--timeout needs seconds}" ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

SERVER_PID="" HMI_PID=""

stop_group() {
  local pid="$1" i
  [[ -n $pid ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for ((i = 0; i < 100; i++)); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

cleanup() {
  trap '' INT TERM HUP
  stop_group "$HMI_PID"
  stop_group "$SERVER_PID"
  [[ -n $SERVER_PID ]] && say "stopped (back-end log: ${LOG:-n/a})"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

# ---- Wayland session: reuse the environment if set, else the compositor socket under the user runtime dir.
detect_wayland() {
  local rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" s
  [[ -d $rt ]] || return 1
  if [[ -z ${WAYLAND_DISPLAY:-} ]]; then
    for s in "$rt"/wayland-*; do
      [[ -S $s ]] || continue
      WAYLAND_DISPLAY="$(basename "$s")"
      break
    done
  fi
  [[ -n ${WAYLAND_DISPLAY:-} ]] || return 1
  export XDG_RUNTIME_DIR="$rt" WAYLAND_DISPLAY
}

if detect_wayland; then
  say "display: WAYLAND_DISPLAY=$WAYLAND_DISPLAY XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
else
  warn "no Wayland socket found (is the desktop session logged in?); the HMI will fall back to X11/DRM"
fi

# ---- audio routing
[[ -f $ROOT/scripts/set_xvf3800_levels.sh ]] && bash "$ROOT/scripts/set_xvf3800_levels.sh"
if ((AUDIO_CHECK)) && [[ -f $ROOT/scripts/pi_audio_setup.sh ]]; then
  say "audio check"
  if aud="$(bash "$ROOT/scripts/pi_audio_setup.sh" --check 2>&1)"; then :; fi
  printf '%s\n' "$aud"
  if grep -q '✗' <<<"$aud"; then
    warn "audio check reported problems (see lines marked above); continuing anyway"
  fi
fi

# ---- thermals
thermal() {
  local t th
  command -v vcgencmd >/dev/null 2>&1 || { say "vcgencmd not available; skipping thermal log"; return 0; }
  t="$(vcgencmd measure_temp 2>/dev/null || true)"
  th="$(vcgencmd get_throttled 2>/dev/null || true)"
  say "thermal ($1): ${t:-temp n/a} ${th:-throttled n/a} arm_clock=$(vcgencmd measure_clock arm 2>/dev/null | cut -d= -f2)"
  local deg="${t#temp=}"; deg="${deg%%.*}"; deg="${deg%%\'*}"
  if [[ $deg =~ ^[0-9]+$ ]] && ((deg > 80)); then warn "SoC at ${deg}C (>80): cool it down before the demo"; fi
  local hex="${th#throttled=}"
  if [[ $hex =~ ^0x[0-9a-fA-F]+$ ]] && ((hex != 0)); then
    warn "get_throttled=$hex (bit0 undervoltage, bit1 freq-capped, bit2 throttled, bit3 soft temp limit; bits 16+ = occurred since boot)"
  fi
}
thermal start

# ---- python + HMI binary
PY="$VENV/bin/python"
[[ -x $PY ]] || die "python not found at $PY (set DRIVEOS_VENV or run scripts/setup.sh)"

if ((FORCE_BUILD)) || [[ ! -x $HMI_BIN ]]; then
  say "building HMI"
  # configure every time: it is cheap, and it is what notices a MapLibre package that appeared since the last build
  CMAKE_ARGS=(-DCMAKE_BUILD_TYPE=Release)
  [[ -n ${DRIVEOS_MAPLIBRE_PREFIX:-} ]] && CMAKE_ARGS+=("-DDOS_MAPLIBRE_PREFIX=$DRIVEOS_MAPLIBRE_PREFIX")
  cmake -S "$HMI_DIR" -B "$HMI_DIR/build" "${CMAKE_ARGS[@]}" || die "cmake configure failed"
  nice -n 10 cmake --build "$HMI_DIR/build" -j2 || die "HMI build failed"
fi
[[ -x $HMI_BIN ]] || die "HMI binary missing: $HMI_BIN"

# ---- back-end
umask 077
mkdir -p "$LOG_DIR" && chmod 700 "$LOG_DIR" && chmod 600 "$LOG_DIR"/*.log 2>/dev/null || true
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/server-$TS.log"
SRV_ARGS=()
((VOICE)) || SRV_ARGS+=(--no-voice)
((LIVE)) && SRV_ARGS+=(--live)

# The demo runs the OpenRouter agent for turns the on-device rules can't parse ("find some cafes nearby?").
# Without a key it stays on the rules parser, which still handles the scripted commands offline.
if [[ -z ${DRIVEOS_NLU__ENGINE:-} ]]; then
  if [[ -n ${OPENROUTER_API_KEY:-} ]] || grep -qs '^OPENROUTER_API_KEY=.' "$ROOT/.env"; then
    export DRIVEOS_NLU__ENGINE=harness
  else
    export DRIVEOS_NLU__ENGINE=rules
  fi
fi
say "language understanding: $DRIVEOS_NLU__ENGINE"

if "$PY" - "$PORT" <<'PYEOF' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PYEOF
then
  die "port $PORT is already in use (another back-end running?)"
fi

say "starting back-end (voice=$([[ $VOICE == 1 ]] && echo on || echo off)) -> $LOG"
cd "$ROOT" || die "cannot cd to $ROOT"
setsid "$PY" -m driveos.server "${SRV_ARGS[@]}" >"$LOG" 2>&1 &
SERVER_PID=$!

hello_probe() {
  "$PY" - "$URL" <<'PYEOF'
import asyncio, json, sys
import websockets

async def main(url):
    async with websockets.connect(url, open_timeout=3) as ws:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 3))
        sys.exit(0 if msg.get("type") == "hello" else 1)

asyncio.run(main(sys.argv[1]))
PYEOF
}

say "waiting up to ${WAIT_S}s for $URL (models load)"
up=0
for ((i = 0; i < WAIT_S; i++)); do
  kill -0 "$SERVER_PID" 2>/dev/null || { tail -n 25 "$LOG" >&2; die "back-end exited during startup (log: $LOG)"; }
  if hello_probe 2>/dev/null; then up=1; break; fi
  sleep 1
done
((up)) || { tail -n 25 "$LOG" >&2; die "no hello from $URL within ${WAIT_S}s (log: $LOG)"; }
say "back-end up: hello received after ~${i}s"

# ---- smoke driver (optional)
smoke_drive() {
  "$PY" - "$URL" <<'PYEOF'
import asyncio, json, sys
import websockets

async def main(url):
    async with websockets.connect(url, open_timeout=5) as ws:
        hello = json.loads(await ws.recv())
        print("smoke: hello", hello.get("app"), hello.get("version"))
        for i, (name, args) in enumerate([("show_tab", {"tab": "shop"}),
                                          ("find", {"category": "food", "query": "coffee"}),
                                          ("show_tab", {"tab": "home"})]):
            await ws.send(json.dumps({"v": hello.get("v"), "type": "cmd", "id": i + 1, "name": name, "args": args}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))
                if m.get("type") == "ack" and m.get("id") == i + 1:
                    print("smoke: %s ok=%s %s" % (name, m.get("ok"), m.get("error") or ""))
                    break
            await asyncio.sleep(2)

asyncio.run(main(sys.argv[1]))
PYEOF
}

# ---- HMI (own process group; waited on so Ctrl-C reaches the trap)
HMI_ARGS=(--url "$URL")
((FULLSCREEN)) && HMI_ARGS+=(--fullscreen)
((DURATION > 0)) && HMI_ARGS+=(--quit-after "$DURATION")
say "starting HMI: ${HMI_BIN##*/} ${HMI_ARGS[*]}"
# The Mapbox token reaches the HMI through its environment only: one line of .env, quotes stripped, never echoed.
MAPBOX_TOKEN=""
if [[ -f $ROOT/.env ]]; then
  MAPBOX_TOKEN="$(grep -m1 '^DRIVEOS_MAPBOX__TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d "\"'\r " || true)"
fi
[[ -n ${DRIVEOS_MAPBOX__TOKEN:-} ]] && MAPBOX_TOKEN="$DRIVEOS_MAPBOX__TOKEN"
if [[ -n $MAPBOX_TOKEN ]]; then say "map: Mapbox token present"; else say "map: no Mapbox token, token-free map style"; fi
DRIVEOS_MAPBOX__TOKEN="$MAPBOX_TOKEN" setsid "$HMI_BIN" "${HMI_ARGS[@]}" &
HMI_PID=$!

if ((SMOKE)); then
  sleep 4
  smoke_drive || warn "smoke driver failed"
  thermal "smoke"
fi

wait "$HMI_PID"
rc=$?
HMI_PID=""
thermal end
say "HMI exited (rc=$rc)"
exit "$rc"
