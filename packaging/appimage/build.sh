#!/usr/bin/env bash
# Build DriveOS-<ver>-aarch64.AppImage. MUST run on aarch64 Ubuntu 22.04 (glibc 2.35) so the image runs on
# Raspberry Pi OS Bookworm (2.36) and Trixie (2.41).
#
#   packaging/appimage/build.sh                       # on an aarch64 Ubuntu 22.04 host or inside the container below
#   packaging/appimage/build.sh --in-container        # re-exec inside docker.io ubuntu:22.04 (needs an arm64 docker: native or qemu binfmt)
#   VERSION=1.2.3 packaging/appimage/build.sh         # else: git tag vX.Y.Z (exact), else pyproject version + .dev+sha
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${OUT:-$ROOT/dist}"
MAX_MB=250

PBS_TAG=20261013
PBS_FILE="cpython-3.13.16+${PBS_TAG}-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"
PBS_SHA256=69a30ed26198178732c37b8de0b6b5191479fbacfe526954da776befe38f18e7
LINUXDEPLOY_URL=https://github.com/linuxdeploy/linuxdeploy/releases/download/1-alpha-20250213-2/linuxdeploy-aarch64.AppImage
LINUXDEPLOY_SHA256=06706ac8189797dccd36bd384105892cb5e6e71f784f4df526cc958adc223cd6
APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-aarch64.AppImage
APPIMAGETOOL_SHA256=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158
MLN_TAG=core/v0.202610.0
MLN_PRESET=linux-gnu-arm64-egl
MLN_SHA256=f4e502b34f1f020a5a229d51cbd49ace075fee884ac0016a87116a55decfe9ad
MLN_URL="https://github.com/maplibre/maplibre-native-ffi/releases/download/${MLN_TAG/\//%2F}/maplibre-native-c-${MLN_PRESET}.tar.gz"
DRIVEAUTH_WHEEL="$ROOT/packaging/vendor/driveauth_edge-1.0.0-py3-none-any.whl"   # Drive_auth_edge@76f33c8, no voice/torch extras

if [ "${1:-}" = "--in-container" ]; then
  docker run --rm --platform linux/arm64 -v "$ROOT":/src -w /src -e VERSION="${VERSION:-}" -e OUT=/src/dist ubuntu:22.04 \
    bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo >/dev/null && packaging/appimage/build.sh'
  exit $?
fi

[ "$(uname -m)" = aarch64 ] || { echo "error: needs aarch64 (got $(uname -m)); use --in-container or the CI workflow" >&2; exit 1; }
REQUESTED_VERSION="${VERSION:-}"
. /etc/os-release
[ "${VERSION_ID:-}" = "22.04" ] || echo "warning: not Ubuntu 22.04 (${PRETTY_NAME}); the image will need a glibc >= this host's" >&2

# --- version -----------------------------------------------------------------
if [ -n "${REQUESTED_VERSION:-}" ]; then
  VERSION="$REQUESTED_VERSION"
elif tag=$(git -C "$ROOT" describe --tags --exact-match --match 'v[0-9]*' 2>/dev/null); then
  VERSION="${tag#v}"
else
  base=$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)
  VERSION="${base}.dev+$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo local)"
fi
echo "==> DriveOS $VERSION"

# --- system packages ---------------------------------------------------------
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO=sudo
if ! dpkg -s cmake clang libsdl2-dev libdrm-dev libjpeg-dev libegl1-mesa-dev >/dev/null 2>&1; then
  $SUDO apt-get update -qq
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq --no-install-recommends \
    build-essential clang cmake pkg-config git curl ca-certificates file patchelf desktop-file-utils squashfs-tools \
    libsdl2-dev libdrm-dev libwayland-dev libxkbcommon-dev libinput-dev libjpeg-dev libegl1-mesa-dev \
    libportaudio2 libsndfile1
fi

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
APPDIR="$W/DriveOS.AppDir"; mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/share/drive-os"

fetch() { # fetch URL SHA OUT
  local cache="${CACHE_DIR:-$HOME/.cache/drive-os-build}"; mkdir -p "$cache"
  local f="$cache/$(basename "$3")"
  [ -f "$f" ] && echo "$2  $f" | sha256sum -c --quiet 2>/dev/null || { curl -fsSL "$1" -o "$f"; echo "$2  $f" | sha256sum -c --quiet; }
  cp "$f" "$3"
}

# --- staged source with the version injected (the tree itself is never modified) --
SRC="$W/src"; mkdir -p "$SRC"
( cd "$ROOT" && tar --exclude=.git --exclude=dist --exclude=./hmi_lvgl/build --exclude=build --exclude=__pycache__ --exclude=.venv --exclude=.env \
    --exclude='*.gguf' --exclude=src/driveos/data/catalog/dataset.json \
    -cf - pyproject.toml README.md src hmi_lvgl ) | tar -xf - -C "$SRC"
PYV="${VERSION%%+*}"; PYV="${PYV/.dev/.dev0}"
sed -i "s/^version *= *\".*\"/version = \"$PYV\"/" "$SRC/pyproject.toml"
printf '__version__ = "%s"\n' "$VERSION" > "$SRC/src/driveos/_version.py"
sed -i "s/^__version__ = .*/from ._version import __version__  # noqa: F401/" "$SRC/src/driveos/__init__.py"

# --- relocatable CPython -----------------------------------------------------
echo "==> CPython ($PBS_FILE)"
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_FILE//+/%2B}" "$PBS_SHA256" "$W/python.tgz"
tar -xzf "$W/python.tgz" -C "$APPDIR/usr"   # -> usr/python
PY="$APPDIR/usr/python/bin/python3"

# --- python packages (NO PySide6/qasync: the headless server does not use Qt; NO torch/speechbrain) ----
echo "==> pip"
"$PY" -m pip install -q --no-cache-dir --upgrade pip
"$PY" - "$SRC/pyproject.toml" > "$W/req.txt" <<'PYEOF'
import sys, tomllib
p = tomllib.load(open(sys.argv[1], "rb"))["project"]
skip = ("pyside6", "qasync", "pytest", "ruff", "respx")
reqs = list(p["dependencies"])
for extra in ("voice", "biometrics", "gps"):
    reqs += p["optional-dependencies"][extra]
for r in dict.fromkeys(reqs):
    if not r.lower().startswith(skip):
        print(r)
PYEOF
"$PY" -m pip install -q --no-cache-dir -r "$W/req.txt"
"$PY" -m pip install -q --no-cache-dir --no-deps "$DRIVEAUTH_WHEEL" "$SRC"
"$PY" -m pip install -q --no-cache-dir PyYAML
# host hook: DRIVEOS_SYSTEM_SITE=<host site-packages> is appended AFTER the bundled packages
SP="$("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
cat > "$SP/zz_driveos_host.pth" <<'PTH'
import os,sys; p=os.environ.get("DRIVEOS_SYSTEM_SITE"); p and os.path.isdir(p) and p not in sys.path and sys.path.append(p)
PTH
"$PY" -m pip uninstall -y -q pip setuptools wheel 2>/dev/null || true
# trim
find "$APPDIR/usr/python" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$APPDIR/usr/python" -type d \( -name tests -o -name test -o -name testing \) -path '*site-packages*' -not -path '*/numpy/*' -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$APPDIR/usr/python/lib/python3.13/"{idlelib,tkinter,turtledemo,test,ensurepip} "$APPDIR/usr/python/share" "$APPDIR/usr/python/lib/"tcl* "$APPDIR/usr/python/lib/"tk* 2>/dev/null || true
PYTHONDONTWRITEBYTECODE=1 "$PY" -c 'import driveos,driveauth,sys; assert "PySide6" not in sys.modules; print("driveos", driveos.__version__)'
"$PY" - "$APPDIR/usr/python/bin" "$SP" <<'PYEOF'
import pathlib
import sys

bin_dir, site_packages = map(pathlib.Path, sys.argv[1:])
for path in bin_dir.iterdir():
    if not path.is_file() or path.is_symlink():
        continue
    with path.open("rb") as handle:
        first = handle.readline()
        rest = handle.read()
    if first.startswith(b"#!") and str(bin_dir).encode() in first:
        path.write_bytes(b"#!/usr/bin/env python3\n" + rest)
for path in site_packages.glob("*.dist-info/direct_url.json"):
    path.unlink()
PYEOF

# --- native speech and map libraries ------------------------------------------
echo "==> parakeet.cpp"
PARAKEET_BUILD_DIR="$W/parakeet" CFLAGS="${CFLAGS:-} -ffile-prefix-map=$W=." \
  CXXFLAGS="${CXXFLAGS:-} -ffile-prefix-map=$W=." \
  "$ROOT/scripts/build_parakeet.sh" "$APPDIR/usr/lib/driveos/parakeet"

echo "==> MapLibre Native"
fetch "$MLN_URL" "$MLN_SHA256" "$W/maplibre.tgz"
mkdir -p "$W/maplibre"
tar -xzf "$W/maplibre.tgz" -C "$W/maplibre"
MLN_PREFIX="$W/maplibre/maplibre-native-c-${MLN_PRESET}"
[ -f "$MLN_PREFIX/include/maplibre_native_c.h" ] || { echo "error: MapLibre package has no C header" >&2; exit 1; }
install -m755 "$MLN_PREFIX/lib/libmaplibre-native-c.so" "$APPDIR/usr/lib/"
install -D -m644 "$MLN_PREFIX/share/maplibre-native-c/LICENSE" "$APPDIR/usr/share/licenses/maplibre-native-c/LICENSE"
cp -a "$MLN_PREFIX/share/maplibre-native-c/licenses" "$APPDIR/usr/share/licenses/maplibre-native-c/"

# --- LVGL HMI ----------------------------------------------------------------
echo "==> HMI"
BUILD_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)"
CC=clang CFLAGS="${CFLAGS:-} -ffile-prefix-map=$W=." \
  cmake -S "$SRC/hmi_lvgl" -B "$W/hmi" -DCMAKE_BUILD_TYPE=Release -DDRIVEOS_VERSION="$PYV" \
  -DDOS_MAPLIBRE_PREFIX="$MLN_PREFIX" >/dev/null
cmake --build "$W/hmi" -j"$(nproc)" >/dev/null
install -m755 "$W/hmi/driveos_hmi" "$APPDIR/usr/bin/driveos-hmi"
mkdir -p "$APPDIR/usr/share/drive-os/fixtures" && cp "$SRC"/hmi_lvgl/fixtures/*.json "$APPDIR/usr/share/drive-os/fixtures/"

# --- runtime models, generated images and licences -----------------------------
mkdir -p "$APPDIR/usr/share/drive-os/models"
cp -a "$ROOT/models/." "$APPDIR/usr/share/drive-os/models/"
cp -a "$ROOT/hmi_lvgl/assets/food" "$APPDIR/usr/share/drive-os/food"
install -D -m755 "$ROOT/scripts/set_xvf3800_levels.sh" "$APPDIR/usr/share/drive-os/scripts/set_xvf3800_levels.sh"
for doc in LICENSE THIRD_PARTY_NOTICES.md PRIVACY.md; do
  [ ! -f "$ROOT/$doc" ] || install -D -m644 "$ROOT/$doc" "$APPDIR/usr/share/doc/drive-os/$doc"
done

# --- desktop, icons, AppRun --------------------------------------------------
ICON="$ROOT/packaging/icon/drive-os-256.png"
install -D -m644 "$ICON" "$APPDIR/usr/share/icons/hicolor/256x256/apps/drive-os.png"
install -D -m644 "$ROOT/packaging/icon/drive-os-512.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/drive-os.png"
cp "$ICON" "$APPDIR/drive-os.png"; cp "$ICON" "$APPDIR/.DirIcon"
install -m644 "$ROOT/packaging/appimage/drive-os.desktop" "$APPDIR/drive-os.desktop"
install -m755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"

# --- shared libs of the HMI via linuxdeploy ----------------------------------
echo "==> linuxdeploy"
fetch "$LINUXDEPLOY_URL" "$LINUXDEPLOY_SHA256" "$W/linuxdeploy"; chmod +x "$W/linuxdeploy"
# --appimage-extract-and-run: no FUSE inside containers. Wayland/DRM/GL/ALSA/PipeWire come from the host (excludelist).
APPIMAGE_EXTRACT_AND_RUN=1 "$W/linuxdeploy" --appdir "$APPDIR" --executable "$APPDIR/usr/bin/driveos-hmi" >/dev/null
patchelf --set-rpath '$ORIGIN/../lib' "$APPDIR/usr/bin/driveos-hmi"
# libportaudio (sounddevice) and libsndfile (soundfile) are not on a stock Pi OS: bundle them
for l in libportaudio.so.2 libsndfile.so.1; do
  p=$(/sbin/ldconfig -p | awk -v l="$l" '$1==l && /aarch64|AArch64/ {print $NF; exit}'); [ -n "$p" ] && cp -L "$p" "$APPDIR/usr/lib/"
done

# --- appimage ----------------------------------------------------------------
find "$APPDIR/usr/python" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "==> appimagetool"
fetch "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$W/appimagetool"; chmod +x "$W/appimagetool"
mkdir -p "$OUT"; IMG="$OUT/DriveOS-${VERSION}-aarch64.AppImage"
ARCH=aarch64 APPIMAGE_EXTRACT_AND_RUN=1 "$W/appimagetool" --comp zstd --mksquashfs-opt -Xcompression-level --mksquashfs-opt 19 \
  "$APPDIR" "$IMG" >/dev/null
sha256sum "$IMG" | sed "s|$OUT/||" > "$IMG.sha256"

MB=$(( $(stat -c %s "$IMG") / 1048576 ))
echo "==> $IMG  ${MB} MB  (limit ${MAX_MB} MB)"
[ "$MB" -le "$MAX_MB" ] || { echo "error: image exceeds ${MAX_MB} MB" >&2; exit 2; }
echo "==> selftest"
APPIMAGE_EXTRACT_AND_RUN=1 SDL_VIDEODRIVER=dummy "$IMG" --selftest
