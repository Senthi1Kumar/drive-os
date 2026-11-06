#!/usr/bin/env bash
# Route DriveOS audio to the reSpeaker XVF3800 on a Raspberry Pi.
#
# Why: a leftover /etc/asound.conf (e.g. from a WM8960 HAT installer) can point ALSA's `default`
# at a sound card that is no longer attached. PortAudio (the microphone) skips a broken default,
# but pyttsx3/espeak plays through a bare `aplay`, which only uses `default` -> every spoken reply
# fails with "Cannot get card index for <card>".
#
# What it does (idempotent, undoable):
#   1. If ALSA `default` references a missing card: moves the stale /etc/asound.conf aside and enables
#      Debian's /etc/alsa/conf.d/99-pulseaudio-default.conf (default -> PipeWire). Needs sudo once and
#      survives reboots; a WM8960 HAT service re-creates /etc/asound.conf itself when the HAT is back.
#      Without sudo (or with --user) it writes ~/.asoundrc instead.
#   2. Makes the XVF3800 PipeWire's default source and sink (matched by name, not card number).
#   3. Pins DriveOS's microphone in ~/.config/driveos/config.toml (only if it doesn't exist).
#
# Usage:  scripts/pi_audio_setup.sh            apply (system-wide ALSA fix, asks for sudo)
#         scripts/pi_audio_setup.sh --user     apply with a per-user ~/.asoundrc instead of sudo
#         scripts/pi_audio_setup.sh --check    report routing + smoke test, change nothing
#         scripts/pi_audio_setup.sh --undo     restore the previous ALSA config and default source
set -euo pipefail

MATCH="${DRIVEOS_AUDIO_MATCH:-XVF3800}"
ASOUNDRC="$HOME/.asoundrc"
MARKER="# managed by driveos scripts/pi_audio_setup.sh"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/driveos-audio"
DRIVEOS_CFG="${DRIVEOS_CONFIG:-$HOME/.config/driveos/config.toml}"
ETC_CONF=/etc/asound.conf
ETC_BAK=/etc/asound.conf.driveos-bak
PULSE_DEFAULT=/etc/alsa/conf.d/99-pulseaudio-default.conf

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

# Card ids referenced as hw:/plughw:/CARD= in the ALSA config files that define `default`.
referenced_cards() {
  local f
  for f in /etc/asound.conf "$ASOUNDRC"; do
    [[ -f $f ]] || continue
    grep -v '^[[:space:]]*#' "$f" | grep -oE '(plug)?hw:[A-Za-z][A-Za-z0-9_]*|CARD=[A-Za-z][A-Za-z0-9_]*' \
      | sed -E 's/^(plug)?hw://; s/^CARD=//' || true
  done | sort -u
}

managed_asoundrc() { [[ -f $ASOUNDRC ]] && grep -qF "$MARKER" "$ASOUNDRC"; }

# Prints the first referenced card id that is not attached; empty when everything resolves
# (or when our managed ~/.asoundrc already overrides `default`).
missing_card() {
  managed_asoundrc && return 0
  local c
  while read -r c; do
    [[ -n $c && ! -e /proc/asound/$c ]] && { echo "$c"; return; }
  done < <(referenced_cards)
  return 0
}

pw_node() {  # pw_node sinks|sources -> first non-monitor node whose name matches $MATCH
  pactl list short "$1" 2>/dev/null | awk -v m="$MATCH" 'tolower($2) ~ tolower(m) && $2 !~ /\.monitor$/ {print $2; exit}'
}

has_pulse_pcm() { aplay -L 2>/dev/null | grep -qx pulse; }

check() {
  echo "ALSA"
  local miss
  miss=$(missing_card)
  if [[ -n $miss ]]; then err "default references missing card '$miss' (see /etc/asound.conf)"; else ok "every card referenced by default is attached"; fi
  if [[ -e $ETC_BAK || -L $ETC_BAK ]]; then ok "stale config parked at $ETC_BAK"; fi
  if [[ -f $PULSE_DEFAULT ]]; then ok "$PULSE_DEFAULT routes default -> PipeWire"; fi
  if [[ -f $ASOUNDRC ]]; then
    if grep -qF "$MARKER" "$ASOUNDRC"; then ok "$ASOUNDRC routes default -> PipeWire"; else warn "$ASOUNDRC exists (not managed by this script)"; fi
  fi
  if has_pulse_pcm; then ok "pulse PCM available"; else warn "no 'pulse' ALSA PCM (sudo apt install libasound2-plugins)"; fi
  echo "PipeWire"
  local src sink
  src=$(pw_node sources); sink=$(pw_node sinks)
  if [[ -n $src ]]; then ok "$MATCH source: $src"; else err "no $MATCH source found"; fi
  if [[ -n $sink ]]; then ok "$MATCH sink:   $sink"; else err "no $MATCH sink found"; fi
  echo "  default source: $(pactl get-default-source 2>/dev/null || echo '?')"
  echo "  default sink:   $(pactl get-default-sink 2>/dev/null || echo '?')"
  echo "DriveOS"
  if [[ -f $DRIVEOS_CFG ]]; then
    ok "config: $DRIVEOS_CFG"
    grep -E '^[[:space:]]*(input|output)_device' "$DRIVEOS_CFG" | sed 's/^/    /' || warn "no input/output_device pinned"
  else
    warn "no $DRIVEOS_CFG (PortAudio will pick its own default input)"
  fi
  echo "Smoke test (1 s of silence through ALSA default, as pyttsx3 plays it)"
  local out
  if out=$(timeout 5 aplay -q -D default -d 1 -f S16_LE -r 16000 -c 1 /dev/zero 2>&1); then
    ok "aplay -D default opens"
  else
    err "aplay -D default failed: ${out//$'\n'/ }"
  fi
}

# System-wide: park the stale /etc/asound.conf and enable Debian's "default -> pulse" conf.d snippet.
fix_system() {
  sudo mv "$ETC_CONF" "$ETC_BAK"   # mv keeps a symlink a symlink
  ok "moved stale $ETC_CONF (card '$1') -> $ETC_BAK"
  if [[ ! -e $PULSE_DEFAULT ]]; then
    if [[ -f $PULSE_DEFAULT.example ]]; then
      sudo cp "$PULSE_DEFAULT.example" "$PULSE_DEFAULT"
    else
      printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' | sudo tee "$PULSE_DEFAULT" >/dev/null
    fi
    touch "$STATE_DIR/created_pulse_default"
    ok "enabled $PULSE_DEFAULT (ALSA default -> PipeWire)"
  fi
  if managed_asoundrc; then rm -f "$ASOUNDRC"; ok "removed per-user $ASOUNDRC (no longer needed)"; fi
}

# Per-user fallback: ~/.asoundrc overrides `default` (it is parsed after /etc/asound.conf).
fix_user() {
  local ts
  ts=$(date +%Y%m%d-%H%M%S)
  if [[ -f $ASOUNDRC ]] && ! managed_asoundrc; then
    cp -p "$ASOUNDRC" "$STATE_DIR/asoundrc.bak.$ts"
    echo "$STATE_DIR/asoundrc.bak.$ts" > "$STATE_DIR/asoundrc.last_backup"
    warn "backed up existing $ASOUNDRC -> $STATE_DIR/asoundrc.bak.$ts"
  fi
  cat > "$ASOUNDRC" <<EOF
$MARKER
# /etc/asound.conf points 'default' at card '$1', which is not attached; route it through PipeWire.
# Remove this file (or run pi_audio_setup.sh --undo) if that card is attached again.
pcm.!default { type pulse }
ctl.!default { type pulse }
EOF
  ok "ALSA default -> PipeWire via $ASOUNDRC (was card '$1')"
}

apply() {
  local mode=${1:-system} miss
  mkdir -p "$STATE_DIR"
  miss=$(missing_card)
  if [[ -n $miss ]]; then
    has_pulse_pcm || { err "ALSA default is broken (missing '$miss') and there is no pulse PCM — sudo apt install libasound2-plugins"; exit 1; }
    if [[ $mode != user ]] && grep -qs "$miss" "$ETC_CONF" && sudo -v; then
      fix_system "$miss"
    else
      fix_user "$miss"
    fi
  else
    ok "ALSA default resolves; ALSA config left as is"
  fi

  local src sink cur
  src=$(pw_node sources); sink=$(pw_node sinks)
  if [[ -n $src ]]; then
    cur=$(pactl get-default-source 2>/dev/null || true)
    if [[ $cur != "$src" ]]; then
      [[ -n $cur ]] && echo "$cur" > "$STATE_DIR/prev_default_source"
      pactl set-default-source "$src"
      ok "default source -> $src (was ${cur:-unset})"
    else
      ok "default source already $MATCH"
    fi
  else
    warn "no $MATCH source in PipeWire — is it plugged in?"
  fi
  if [[ -n $sink ]]; then
    cur=$(pactl get-default-sink 2>/dev/null || true)
    if [[ $cur != "$sink" ]]; then
      [[ -n $cur ]] && echo "$cur" > "$STATE_DIR/prev_default_sink"
      pactl set-default-sink "$sink"
      ok "default sink -> $sink (was ${cur:-unset})"
    else
      ok "default sink already $MATCH"
    fi
  else
    warn "no $MATCH sink in PipeWire — replies will go to the current default sink"
  fi

  if [[ -f $DRIVEOS_CFG ]]; then
    if grep -qE '^[[:space:]]*input_device' "$DRIVEOS_CFG"; then
      ok "DriveOS config already pins input_device ($DRIVEOS_CFG)"
    else
      warn "$DRIVEOS_CFG exists without input_device; add under [voice]:  input_device = \"$MATCH\""
    fi
  else
    mkdir -p "$(dirname "$DRIVEOS_CFG")"
    cat > "$DRIVEOS_CFG" <<EOF
$MARKER
[voice]
input_device = "$MATCH"    # sounddevice substring match; keeps the mic off the webcam
# output_device stays unset on purpose: PipeWire owns the $MATCH playback side, so PortAudio lists
# its hw device as 0-out. The default output (ALSA default -> PipeWire default sink) reaches it.
EOF
    ok "wrote $DRIVEOS_CFG"
  fi
}

undo() {
  if [[ -e $ETC_BAK || -L $ETC_BAK ]] && [[ ! -e $ETC_CONF && ! -L $ETC_CONF ]]; then
    sudo mv "$ETC_BAK" "$ETC_CONF" && ok "restored $ETC_CONF"
  fi
  if [[ -f $STATE_DIR/created_pulse_default ]]; then
    sudo rm -f "$PULSE_DEFAULT" && rm -f "$STATE_DIR/created_pulse_default" && ok "removed $PULSE_DEFAULT"
  fi
  if [[ -f $ASOUNDRC ]] && grep -qF "$MARKER" "$ASOUNDRC"; then
    rm -f "$ASOUNDRC"
    ok "removed managed ~/.asoundrc"
    if [[ -f $STATE_DIR/asoundrc.last_backup ]]; then
      cp -p "$(<"$STATE_DIR/asoundrc.last_backup")" "$ASOUNDRC"
      ok "restored previous ~/.asoundrc"
    fi
  fi
  local f
  for f in source sink; do
    if [[ -f $STATE_DIR/prev_default_$f ]]; then
      pactl "set-default-$f" "$(<"$STATE_DIR/prev_default_$f")" && ok "default $f restored"
      rm -f "$STATE_DIR/prev_default_$f"
    fi
  done
  if [[ -f $DRIVEOS_CFG ]] && grep -qF "$MARKER" "$DRIVEOS_CFG"; then
    rm -f "$DRIVEOS_CFG"
    ok "removed managed $DRIVEOS_CFG"
  fi
}

command -v pactl >/dev/null || { err "pactl not found (sudo apt install pulseaudio-utils)"; exit 1; }
case "${1:-}" in
  --check) check ;;
  --undo)  undo ;;
  "")      apply system; echo; check ;;
  --user)  apply user; echo; check ;;
  -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) err "unknown option: $1"; exit 2 ;;
esac
