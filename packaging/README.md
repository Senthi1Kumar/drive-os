# DriveOS AppImage packaging

One AppImage: LVGL HMI (`usr/bin/driveos-hmi`) + relocatable CPython 3.13 with the headless back-end (`usr/python`, `python -m driveos.server`). Includes Parakeet/ggml, MapLibre, the small VAD/turn models and generated food assets. No Qt/PySide6, torch, large downloaded models or `.env`.

## Build

The image must be built on **aarch64 Ubuntu 22.04 (glibc 2.35)**: it then runs on Raspberry Pi OS Bookworm (2.36) and Trixie (2.41).

- On an arm64 Ubuntu 22.04 host (or in an arm64 CI runner container): `scripts/build_appimage.sh`
- On an x86 laptop with arm64 docker (needs a running daemon and qemu-user binfmt, e.g. `docker run --privileged --rm tonistiigi/binfmt --install arm64`): `scripts/build_appimage.sh --in-container` (slow under emulation)
- GitHub Actions: `.github/workflows/release.yml` builds on an `ubuntu-22.04-arm` runner, or, when the repository variable `RELEASE_RUNNER` is `qemu`, on x86 through `docker buildx`/QEMU with `scripts/build_appimage.sh --in-container`. It uploads `dist/DriveOS-*.AppImage` and `.sha256`.

The script pins CPython (python-build-standalone 20261013, 3.13.16), linuxdeploy (1-alpha-20250213-2) and appimagetool (1.9.1) by URL + sha256, fails if the image exceeds 250 MB, then runs `--selftest`. Downloads are cached in `~/.cache/drive-os-build`.

DriveAuth comes from the vendored wheel `packaging/vendor/driveauth_edge-1.0.0-py3-none-any.whl` (Drive_auth_edge `76f33c8`, no `voice`/torch extras). The back-end is installed with `--no-deps` plus the dependency list from `pyproject.toml` minus PySide6/qasync, with the `voice`, `biometrics` and `gps` extras.

## Version

Single source is the git tag `vX.Y.Z`. `build.sh` uses an exact tag, else `VERSION=...`, else `pyproject` version + `.dev+<sha>`. It builds from a staged copy of the tree (the checkout is untouched): writes `src/driveos/_version.py`, makes `driveos.__version__` import it, rewrites the staged `pyproject.toml` version, and the HMI picks the same version through its CMake `DRIVEOS_VERSION` (read from the staged `pyproject.toml`). The file is `DriveOS-<ver>-aarch64.AppImage` plus `.sha256`.

## Run

```
chmod +x DriveOS-*.AppImage
./DriveOS-*.AppImage                  # back-end + HMI, supervised (back-end restarts with backoff)
./DriveOS-*.AppImage --server-only    # headless back-end on ws://127.0.0.1:8790/v1/ui
./DriveOS-*.AppImage --hmi-only
./DriveOS-*.AppImage --selftest       # import check, WS hello, 3 headless screenshots; exit code
./DriveOS-*.AppImage --appimage-extract-and-run   # no FUSE
```

First run installs a desktop entry and icon, and runs `driveos fetch-models` if `~/.local/share/drive-os/models` is empty.

## Data and secrets (never inside the image)

| Path | Content |
|---|---|
| `$XDG_CONFIG_HOME/drive-os/` (`~/.config/drive-os/`) | `config.toml` (fallback `~/.config/driveos/config.toml`), `.env` (OpenRouter key etc., chmod 600), `github_token` |
| `~/.local/share/drive-os/` | `models/ enroll/ ledger/ driveauth-store/` |
| `~/.local/state/drive-os/` | `logs/ update/` |

## Speech-to-text (parakeet.cpp)

Default STT is NVIDIA parakeet-tdt_ctc-110m (q8_0 GGUF, CC-BY-4.0) through mudler/parakeet.cpp (MIT) v0.6.1, commit `5c9b0fbbd8e611d88e47d7e1ebae9bca9b447e8f`, over ctypes (`voice/parakeet_stt.py`, C ABI >= 5). If the library or model is absent or fails to load the back-end logs one warning and uses faster-whisper `base.en`.

- The AppImage must ship `libparakeet.so` plus `libggml.so*`, `libggml-base.so*`, `libggml-cpu.so*` (libparakeet has RUNPATH `$ORIGIN`, so they must sit in the same directory). `scripts/build_parakeet.sh <outdir>` builds them (CPU only, `GGML_NATIVE=OFF`; cmake, a C++ compiler, git, patchelf and readelf), sets and verifies the `$ORIGIN` runtime path on the aarch64 Ubuntu 22.04 build host, so the glibc floor stays 2.35.
- The build stages them at `usr/lib/driveos/parakeet/` and `AppRun` points `DRIVEOS_PARAKEET_LIB` there. They are part of the AppImage; no separate native-library download is needed.
- The GGUF (about 120 MB) is a model, not bundled: `driveos fetch-models` downloads it to `~/.local/share/drive-os/models/parakeet/` (pinned HF revision, sha256 checked).
- Needs numpy at runtime (already in the `voice` extra).

## Map engine

The build downloads MapLibre Native C `0.202610.0` (`linux-gnu-arm64-egl`) from the pinned MapLibre Native FFI release and verifies its SHA-256. CMake builds against that package; `libmaplibre-native-c.so` and its licences are bundled, and the HMI uses a relative runtime path.

## Not in the image

Large downloadable models, `.env`, tests and caches are not included. The small VAD/turn ONNX models and generated food assets are bundled. Wayland/DRM/GL/EGL/ALSA/PipeWire come from the host; `libportaudio`/`libsndfile` are bundled.

## Known limitations

The HMI reports its build as `dev` when the staged tree has no `.git`. The over-the-air updater (symlink swap and rollback) is not part of this release.
