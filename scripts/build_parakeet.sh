#!/usr/bin/env bash
# Build libparakeet.so (mudler/parakeet.cpp, CPU only) for this machine (aarch64 or x86_64).
#   scripts/build_parakeet.sh [OUT_DIR]
# OUT_DIR defaults to ~/.local/share/drive-os/models/parakeet (the path `driveos` looks in).
# It receives libparakeet.so and the ggml shared libraries it links against (RUNPATH is $ORIGIN).
set -euo pipefail

TAG="${PARAKEET_TAG:-v0.6.1}"
COMMIT="${PARAKEET_COMMIT:-5c9b0fbbd8e611d88e47d7e1ebae9bca9b447e8f}"
REPO="https://github.com/mudler/parakeet.cpp.git"
OUT="${1:-${XDG_DATA_HOME:-$HOME/.local/share}/drive-os/models/parakeet}"
WORK="${PARAKEET_BUILD_DIR:-$HOME/.cache/drive-os-build/parakeet.cpp-$TAG}"
JOBS="${JOBS:-$(nproc)}"

for tool in git cmake patchelf readelf; do command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }; done
command -v g++ >/dev/null || command -v c++ >/dev/null || { echo "missing a C++ compiler" >&2; exit 1; }

if [ ! -d "$WORK/.git" ]; then
  git clone --filter=blob:none "$REPO" "$WORK"
fi
git -C "$WORK" fetch --tags --quiet
git -C "$WORK" checkout --quiet "$TAG"
HEAD="$(git -C "$WORK" rev-parse HEAD)"
if [ "$HEAD" != "$COMMIT" ]; then
  echo "tag $TAG is $HEAD, expected $COMMIT; refusing to build" >&2
  exit 1
fi
git -C "$WORK" submodule update --init --recursive --quiet 2>/dev/null || true

cmake -S "$WORK" -B "$WORK/build-shared" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
  -DPARAKEET_SHARED=ON -DPARAKEET_BUILD_CLI=OFF -DPARAKEET_BUILD_SERVER=OFF -DPARAKEET_BUILD_TESTS=OFF \
  -DPARAKEET_GGML_CUDA=OFF -DPARAKEET_GGML_HIP=OFF -DPARAKEET_GGML_METAL=OFF -DPARAKEET_GGML_VULKAN=OFF \
  -DGGML_NATIVE=OFF
cmake --build "$WORK/build-shared" -j "$JOBS" --target parakeet
patchelf --set-rpath '$ORIGIN' "$WORK/build-shared/libparakeet.so"
readelf -d "$WORK/build-shared/libparakeet.so" | grep -q 'ORIGIN' || {
  echo "libparakeet.so has no \$ORIGIN runtime path; refusing to package an unrelocatable library" >&2
  exit 1
}
while IFS= read -r -d '' lib; do
  patchelf --set-rpath '$ORIGIN' "$lib"
  readelf -d "$lib" | grep -q 'Library runpath: \[\$ORIGIN\]' || {
    echo "$lib has no \$ORIGIN runtime path; refusing to package an unrelocatable library" >&2
    exit 1
  }
done < <(find "$WORK/build-shared" -name 'libggml*.so*' -type f -print0)

mkdir -p "$OUT"
cp -a "$WORK/build-shared/libparakeet.so" "$OUT/"
find "$WORK/build-shared" -name 'libggml*.so*' -exec cp -a {} "$OUT/" \;
install -D -m644 "$WORK/LICENSE" "$OUT/licenses/parakeet.LICENSE"
install -D -m644 "$WORK/third_party/ggml/LICENSE" "$OUT/licenses/ggml.LICENSE"
echo "built parakeet.cpp $TAG ($COMMIT) for $(uname -m) into $OUT"
