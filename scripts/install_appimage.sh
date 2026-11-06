#!/usr/bin/env bash
set -euo pipefail

LAUNCH=0
if [ "${1:-}" = "--launch" ]; then
  LAUNCH=1
  shift
fi
[ $# -le 1 ] || { echo "usage: $0 [--launch] [DriveOS-<version>-aarch64.AppImage]" >&2; exit 2; }

DOWNLOAD_DIR="${DRIVEOS_DOWNLOAD_DIR:-$HOME/Downloads}"
if [ $# -eq 1 ]; then
  APP="$1"
else
  shopt -s nullglob
  candidates=("$DOWNLOAD_DIR"/DriveOS-*-aarch64.AppImage)
  ((${#candidates[@]})) || { echo "error: no DriveOS aarch64 AppImage found in $DOWNLOAD_DIR" >&2; exit 2; }
  APP=$(printf '%s\n' "${candidates[@]}" | sort -V | tail -n 1)
fi
[ -s "$APP" ] || { echo "error: AppImage not found or empty: $APP" >&2; exit 2; }
APP=$(realpath "$APP")
NAME=$(basename "$APP")
if [[ ! "$NAME" =~ ^DriveOS-([0-9A-Za-z.+_-]+)-aarch64\.AppImage$ ]]; then
  echo "error: unsupported AppImage filename: $NAME" >&2
  exit 2
fi
TAG="v${BASH_REMATCH[1]}"
BASE="https://github.com/Senthi1Kumar/drive-os/releases/download/$TAG"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/driveos-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

for file in SHA256SUMS SHA256SUMS.minisig "$NAME.minisig" driveos-minisign.pub; do
  curl -fsSL "$BASE/$file" -o "$WORK/$file"
done

if command -v minisign >/dev/null 2>&1; then
  MINISIGN=$(command -v minisign)
else
  case "$(uname -m)" in
    aarch64) ARCH=aarch64 ;;
    x86_64) ARCH=x86_64 ;;
    *) echo "error: unsupported architecture: $(uname -m)" >&2; exit 2 ;;
  esac
  curl -fsSL https://github.com/jedisct1/minisign/releases/download/0.12/minisign-0.12-linux.tar.gz -o "$WORK/minisign.tar.gz"
  printf '%s  %s\n' '9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73' "$WORK/minisign.tar.gz" | sha256sum -c -
  tar -xzf "$WORK/minisign.tar.gz" -C "$WORK"
  MINISIGN="$WORK/minisign-linux/$ARCH/minisign"
fi

"$MINISIGN" -V -p "$WORK/driveos-minisign.pub" -m "$WORK/SHA256SUMS"
EXPECTED=$(awk -v name="$NAME" '$2 == name {print $1}' "$WORK/SHA256SUMS")
[ -n "$EXPECTED" ] || { echo "error: $NAME is absent from $TAG SHA256SUMS" >&2; exit 1; }
ACTUAL=$(sha256sum "$APP" | awk '{print $1}')
[ "$ACTUAL" = "$EXPECTED" ] || { echo "error: AppImage checksum mismatch" >&2; exit 1; }
"$MINISIGN" -V -p "$WORK/driveos-minisign.pub" -x "$WORK/$NAME.minisig" -m "$APP"

ICON="$ROOT/packaging/icon/drive-os-256.png"
if [ ! -s "$ICON" ]; then
  ICON="$WORK/drive-os.png"
  curl -fsSL "$BASE/drive-os.png" -o "$ICON"
fi
[ -s "$ICON" ] || { echo "error: DriveOS icon is missing" >&2; exit 1; }

APP_DIR="$HOME/Applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
APP_MENU="$HOME/.local/share/applications"
DESKTOP="$HOME/Desktop"
DEST="$APP_DIR/$NAME"
mkdir -p "$APP_DIR" "$ICON_DIR" "$APP_MENU" "$DESKTOP"
if [ "$APP" != "$DEST" ]; then
  STAGE="$APP_DIR/.$NAME.installing.$$"
  mv "$APP" "$STAGE"
  chmod 755 "$STAGE"
  mv -f "$STAGE" "$DEST"
else
  chmod 755 "$DEST"
fi
install -m644 "$ICON" "$ICON_DIR/drive-os.png"

DESKTOP_CONTENT="[Desktop Entry]
Type=Application
Name=DriveOS
GenericName=In-vehicle voice assistant
Comment=Hands-free, voice-first ordering and payments
Exec=\"$DEST\"
Icon=drive-os
Categories=Utility;
Terminal=false
StartupNotify=false
X-AppImage-Name=DriveOS
X-AppImage-Version=$TAG"
printf '%s\n' "$DESKTOP_CONTENT" > "$APP_MENU/drive-os.desktop"
printf '%s\n' "$DESKTOP_CONTENT" > "$DESKTOP/DriveOS.desktop"
chmod 755 "$APP_MENU/drive-os.desktop" "$DESKTOP/DriveOS.desktop"
if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP/DriveOS.desktop" metadata::trusted true >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_MENU" >/dev/null 2>&1 || true
fi

printf 'DriveOS installed: %s\n' "$DEST"
printf 'Application menu: %s\n' "$APP_MENU/drive-os.desktop"
printf 'Desktop launcher: %s\n' "$DESKTOP/DriveOS.desktop"
if [ "$LAUNCH" -eq 1 ]; then
  "$DEST"
fi
