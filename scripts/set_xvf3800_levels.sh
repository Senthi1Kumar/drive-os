#!/usr/bin/env bash
# Sets the reSpeaker XVF3800's own mixer to 0 dB (playback and capture).
#
# The card exposes two playback stages ('PCM Playback Volume' index 0 and 1). PipeWire's volume slider only
# drives index 0; index 1 comes up at 40/60 (-20 dB) after boot, so the JST speaker stays quiet even with the
# tray at 100%. Nothing restores it at boot on the demo Pi (its saved ALSA state is for the old WM8960 HAT),
# so the launchers run this every start. Non-fatal: exits 0 when no XVF3800 is plugged in.
#
#   scripts/set_xvf3800_levels.sh            playback 100%, capture 100%
#   scripts/set_xvf3800_levels.sh 80         playback 80%
#   XVF3800_CAPTURE_PCT=90 scripts/set_xvf3800_levels.sh
set -uo pipefail

PLAY_PCT="${1:-${XVF3800_PLAYBACK_PCT:-100}}"
CAP_PCT="${XVF3800_CAPTURE_PCT:-100}"

CARD="${XVF3800_CARD:-}"
if [[ -z $CARD ]] && grep -qi "XVF3800" /proc/asound/cards 2>/dev/null; then
  CARD=$(awk '/XVF3800/ { print $1; exit }' /proc/asound/cards)
fi
if [[ -z $CARD ]]; then
  echo "[xvf3800] not plugged in; leaving audio levels untouched" >&2
  exit 0
fi
command -v amixer >/dev/null || { echo "[xvf3800] amixer missing (apt install alsa-utils)" >&2; exit 0; }

set_ctl() {  # name index value...
  local name=$1 idx=$2
  shift 2
  amixer -q -c "$CARD" cset "name='$name',index=$idx" "$@" >/dev/null 2>&1
}

for idx in 0 1; do
  set_ctl 'PCM Playback Switch' "$idx" on
  set_ctl 'PCM Playback Volume' "$idx" "${PLAY_PCT}%"
  set_ctl 'Headset Capture Switch' "$idx" on
  set_ctl 'Headset Capture Volume' "$idx" "${CAP_PCT}%"
done

levels=$(amixer -c "$CARD" scontents 2>/dev/null | grep -oE '\[-?[0-9.]+dB\]' | sort -u | tr '\n' ' ')
echo "[xvf3800] card $CARD playback ${PLAY_PCT}% capture ${CAP_PCT}% (stages now: ${levels:-unknown})"
