#!/usr/bin/env bash
# One-time setup for macOS and Linux: creates .venv, installs DriveOS with voice support, checks it.
#   ./scripts/setup.sh            # full install (voice included)
#   ./scripts/setup.sh --no-voice # app only, keyboard/console control
set -euo pipefail
cd "$(dirname "$0")/.."

ORIG_VENV="${VIRTUAL_ENV:-}"   # whatever the calling shell has active (this script can't change that)
VOICE=1
[[ "${1:-}" == "--no-voice" ]] && VOICE=0

# 1. find Python 3.11-3.13
PY=""
for c in python3.12 python3.13 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)'; then
    PY="$c"; break
  fi
done
if [[ -z "$PY" ]]; then
  echo "Python 3.11, 3.12 or 3.13 is required. Install it first (see INSTALL.md, step 1)." >&2
  exit 1
fi
echo "→ using $($PY --version) at $(command -v $PY)"

# 2. system audio library on Linux (macOS wheels bundle their own)
if [[ "$(uname)" == "Linux" && $VOICE == 1 ]]; then
  if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    echo "! PortAudio is missing. Install it, then re-run:  sudo apt install libportaudio2 espeak-ng"
  fi
fi

# 3. virtual environment + packages
if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel >/dev/null
if [[ $VOICE == 1 ]]; then
  pip install -e ".[voice,gps,dev]"
  pip install --no-deps "openwakeword>=0.6,<0.7"   # wake word ("Hey Jarvis"); ONNX backend
else
  pip install -e ".[gps,dev]"
fi

ROOT="$(pwd)"
if [[ ! -x "$ROOT/.venv/bin/driveos" ]]; then
  echo "✗ install did not create $ROOT/.venv/bin/driveos — see the pip output above" >&2
  exit 1
fi
chmod +x "$ROOT/run.sh" 2>/dev/null || true

# 4. a local .env for your keys (all lines start commented out, so demo mode keeps working)
[[ -f .env ]] || cp .env.example .env

# 5. check
echo
"$ROOT/.venv/bin/driveos" doctor || true
echo
echo "Done. Start DriveOS from $ROOT with any of:"
echo "  ./run.sh                       # no activation needed"
echo "  ./run.sh demo                  # automated real-time walkthrough (find → order → pay → drive → pickup)"
echo "  ./run.sh --gps                 # follow your live GPS"
echo "  ./run.sh --gps --gps-source replay   # live-GPS pipeline on the recorded Los Gatos drive"
echo
echo "Or activate the environment first (this script can't activate it for your shell):"
echo "  cd \"$ROOT\" && source .venv/bin/activate && driveos"
if [[ -n "$ORIG_VENV" && "$ORIG_VENV" != "$ROOT/.venv" ]]; then
  echo
  echo "! Your shell has a different virtualenv active ($ORIG_VENV)."
  echo "  Run 'deactivate' first, or just use ./run.sh."
fi
