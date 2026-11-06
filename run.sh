#!/usr/bin/env bash
# Start DriveOS without activating the virtualenv. Any arguments go to the `driveos` command:
#   ./run.sh                 HMI in demo mode        ./run.sh demo          automated walkthrough
#   ./run.sh --gps           follow live GPS         ./run.sh gps           print raw GPS fixes
#   ./run.sh doctor          check the install
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$DIR/.venv/bin/driveos" ]]; then
  echo "DriveOS isn't installed in $DIR/.venv yet — running scripts/setup.sh first…"
  bash "$DIR/scripts/setup.sh"
fi
cd "$DIR"
# the XVF3800 boots with its second playback stage at -20 dB, below the tray slider's reach
if [[ $(uname -s) == Linux && -f "$DIR/scripts/set_xvf3800_levels.sh" ]]; then
  bash "$DIR/scripts/set_xvf3800_levels.sh" || true
fi
exec "$DIR/.venv/bin/driveos" "$@"
