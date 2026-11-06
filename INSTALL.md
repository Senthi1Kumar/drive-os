# Installing DriveOS

DriveOS runs on two targets:

| Target | OS | Notes |
|---|---|---|
| Raspberry Pi 5 | Raspberry Pi OS Bookworm or Trixie, arm64 | Reference platform. Use active cooling. |
| x86_64 Linux | Ubuntu 22.04 or later, Debian 12 or later | Development, demos, CI. |

Demo mode needs no accounts or keys: a bundled sample catalogue, a simulated car and simulated payments. Add keys only for
the live map, the language-model agent, web search, SMS codes or real payments ([Configuration](#6-configuration)).

Two ways to install:

1. [AppImage](#1-appimage-aarch64) for Raspberry Pi 5. One file, no build.
2. [From source](#2-from-source) for any target, and the only option on x86_64.

---

## 1. AppImage (aarch64)

Download `DriveOS-<version>-aarch64.AppImage`, `SHA256SUMS`, `SHA256SUMS.minisig` and `driveos-minisign.pub` from the
repository's Releases page.

```bash
sha256sum -c SHA256SUMS
minisign -V -p driveos-minisign.pub -m SHA256SUMS
chmod +x DriveOS-*-aarch64.AppImage
./DriveOS-*-aarch64.AppImage                 # back-end + HMI, supervised
```

For a normal Pi application-menu and Desktop installation, keep the AppImage in `~/Downloads` and run the standalone
installer from the same release:

```bash
curl -fsSL https://github.com/Senthi1Kumar/drive-os/releases/download/v1.0.2/driveos-installer.sh \
  -o ~/Downloads/driveos-installer.sh
chmod +x ~/Downloads/driveos-installer.sh
~/Downloads/driveos-installer.sh             # add --launch to open DriveOS after installing
```

The installer verifies the AppImage's checksum and signature, moves it into `~/Applications`, and creates
`~/.local/share/applications/drive-os.desktop` and `~/Desktop/DriveOS.desktop`. It needs no sudo and does not download
models until you launch DriveOS.

Other modes:

```bash
./DriveOS-*.AppImage --server-only            # headless back-end on ws://127.0.0.1:8790/v1/ui
./DriveOS-*.AppImage --hmi-only               # front-end only
./DriveOS-*.AppImage --selftest               # import check, WebSocket hello, headless screenshots; exit code 0 = healthy
./DriveOS-*.AppImage --appimage-extract-and-run   # when FUSE is not available
```

The image contains the HMI, a relocatable Python back-end, the Parakeet/MapLibre native libraries, generated food assets,
and the small Silero and Smart Turn ONNX models. It does not contain keys, user configuration or large downloadable models.
On first run it creates a desktop entry and, if the models folder is empty, runs `driveos fetch-models` (about 200 MB;
network needed once).

| Path | Content |
|---|---|
| `~/.config/drive-os/config.toml` | Settings ([`config.example.toml`](config.example.toml)) |
| `~/.config/drive-os/.env` | API keys (`chmod 600`) |
| `~/.local/share/drive-os/` | `models/`, `enroll/`, `ledger/`, `driveauth-store/` |
| `~/.local/state/drive-os/logs/` | Back-end logs |

The speech-to-text library (`libparakeet.so`) and its ggml dependencies are included in the AppImage. Only the pinned
Parakeet GGUF model is downloaded by `fetch-models`. If the model cannot be loaded, the back-end logs one warning and
falls back to faster-whisper `base.en`.

Then go to [Audio](#7-audio) and, for Face ID, [the F455 camera](#8-f455-face-id-camera).

---

## 2. From source

### 2.1 System packages

**Raspberry Pi OS (Bookworm/Trixie) and Debian/Ubuntu**

```bash
sudo apt update
sudo apt install -y build-essential cmake pkg-config git curl ca-certificates patchelf \
    libsdl2-dev libdrm-dev libwayland-dev libxkbcommon-dev libinput-dev libjpeg-dev libegl1-mesa-dev \
    libportaudio2 libsndfile1 espeak-ng alsa-utils
```

`libsdl2-dev` is the HMI window backend (Wayland, X11 or DRM/KMS), `libjpeg-dev` decodes the item photos,
`libegl1-mesa-dev` is needed when the live map is built, `libportaudio2` and `libsndfile1` serve the microphone and audio
files, and `espeak-ng` is the offline fallback voice.

### 2.2 Python

DriveOS needs Python 3.11, 3.12 or 3.13. Raspberry Pi OS Bookworm ships 3.11 and Trixie ships 3.13. Ubuntu
22.04 ships 3.10, which is too old; install a managed interpreter with [uv](https://docs.astral.sh/uv/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.13
```

### 2.3 Install the back-end

Either with uv (recommended):

```bash
git clone https://github.com/Senthi1Kumar/drive-os.git && cd drive-os
uv sync --extra voice --extra biometrics --extra gps --extra dev
uv pip install packaging/vendor/driveauth_edge-1.0.0-py3-none-any.whl     # DriveAuth Edge, pinned
```

or with the setup script (creates `.venv`, installs the `voice`, `gps` and `dev` extras, creates `.env`, runs a health
check):

```bash
bash scripts/setup.sh
.venv/bin/pip install -e ".[biometrics]" packaging/vendor/driveauth_edge-1.0.0-py3-none-any.whl
```

The `biometrics` extra and DriveAuth are what enable voice-ID and the approval ladder. Add `--no-voice` to
`scripts/setup.sh` for a smaller install without the speech stack.

Check the result:

```bash
.venv/bin/driveos doctor
```

`doctor` reports configuration, keys, audio devices, models, the map engine and the ledger.

### 2.4 Models

```bash
.venv/bin/driveos fetch-models
```

Downloads the speech-to-text model (parakeet-tdt_ctc-110m, q8_0 GGUF, with its sha256 checked), the voice-biometric models
and the Phonon text-to-speech weights into `~/.local/share/drive-os/models` and the Hugging Face cache. After this
the stack runs offline. The two small ONNX models for voice-activity and turn detection ship in `models/`.

### 2.5 Speech-to-text library (parakeet.cpp)

```bash
scripts/build_parakeet.sh                    # CPU build; output: ~/.local/share/drive-os/models/parakeet/
scripts/build_parakeet.sh /some/dir          # or a directory of your choice
```

Needs `git`, `cmake` and a C++ compiler. It builds the pinned parakeet.cpp release (v0.6.1) and refuses to build if the
tag does not match the expected commit. If the library or model cannot be loaded the back-end falls back to
faster-whisper.

### 2.6 HMI (LVGL)

```bash
cmake -S hmi_lvgl -B hmi_lvgl/build -DCMAKE_BUILD_TYPE=Release
cmake --build hmi_lvgl/build -j2             # -j2 keeps a Pi 5 responsive while it compiles
ctest --test-dir hmi_lvgl/build              # optional unit tests
```

LVGL is fetched at configure time. The live map needs the MapLibre Native C package at
`hmi_lvgl/.deps/maplibre/<preset>/maplibre-native-c-<preset>/` (`<preset>` is `linux-gnu-arm64-egl` on aarch64 and
`linux-gnu-x64-egl` on x86_64) or at the path given with `-DDOS_MAPLIBRE_PREFIX=...`. Without the package the build
succeeds and the Nav screen shows a schematic view instead of the map.

---

## 3. Run

```bash
scripts/run_pi.sh                            # back-end (voice on) + HMI, fullscreen
scripts/run_pi.sh --help                     # --no-voice --windowed --live --duration SEC --smoke --build
```

`run_pi.sh` sets the XVF3800 levels, checks audio routing, logs temperature and throttling, starts the back-end, waits for
its WebSocket hello and then starts the HMI. Ctrl-C stops both. Logs go to `~/.local/state/drive-os/logs/`.

To run the pieces yourself:

```bash
.venv/bin/python -m driveos.server           # back-end on ws://127.0.0.1:8790/v1/ui (add --no-voice, --live)
hmi_lvgl/build/driveos_hmi --url ws://127.0.0.1:8790/v1/ui --fullscreen
```

Say "Hey Nova" or press the voice button, then, for example: "find me a coffee", "the first one", "add a flat white",
"check out", and follow the approval prompt.

## 4. Enroll the driver

Voice ID and Face ID are optional but are what the approval ladder uses for larger amounts.

```bash
.venv/bin/driveos enroll --name Alex          # voice: 3 to 6 short phrases, quiet room
.venv/bin/driveos enroll --face --name Alex   # F455 camera
.venv/bin/driveos voice-check                 # measures speaker and anti-spoof scores on this microphone
```

Enrollment needs the driver's explicit consent; templates stay on the device (see [PRIVACY.md](PRIVACY.md)). Without an
enrollment, DriveOS falls back to simulated approvals in demo mode.

## 5. GPS

By default the car on screen is simulated. To follow a real position:

```bash
.venv/bin/driveos gps --source auto           # print fixes first, to check the receiver
.venv/bin/driveos run --gps --gps-source serial:/dev/ttyACM0
```

| `--gps-source` | Source | Setup |
|---|---|---|
| `serial:/dev/ttyACM0[@9600]` | USB NMEA receiver | `sudo usermod -aG dialout $USER`, then log out and in |
| `gpsd` or `gpsd:host:2947` | gpsd | `sudo apt install gpsd`, point it at the receiver |
| `tcp:host:port`, `udp:10110` | NMEA over the network (phone apps, other devices) | Same network, allow the port |
| `replay[:file.nmea\|file.gpx][@speed]` | A recorded drive; a short sample drive is bundled | None |
| `ip` | City-level, does not move | Last resort |

Make it permanent with `DRIVEOS_VEHICLE__SOURCE=gps` and `DRIVEOS_VEHICLE__GPS_SOURCE=serial:/dev/ttyACM0` in `.env`.

## 6. Configuration

```bash
mkdir -p ~/.config/driveos
cp config.example.toml ~/.config/driveos/config.toml      # AppImage: ~/.config/drive-os/config.toml
cp .env.example .env && chmod 600 .env                    # AppImage: ~/.config/drive-os/.env
```

Copy `.env.example`, then fill in values only for the services you use. Blank values leave those integrations disabled:

| Variable | Enables |
|---|---|
| `DRIVEOS_MAPBOX__TOKEN` | Live Mapbox map, traffic-aware routing, place search. Create a token at account.mapbox.com. |
| `DRIVEOS_NLU__ENGINE=harness` and `OPENROUTER_API_KEY` | The OpenRouter tool agent for open-ended requests. On-device rules still decide approvals. |
| `EXA_API_KEY` | The web-search tool (scrubbed queries). |
| `DRIVEOS_AUTH__OTP_CHANNEL=sms` and the Twilio variables | One-time codes by SMS to the occupant's phone. |
| `DRIVEOS_PAYMENTS__PROVIDER=stripe` and `DRIVEOS_STRIPE__SECRET_KEY` | Real card holds and captures (start with a test key). |
| `DRIVEOS_MODE=live` | Real services instead of the simulator. |

Never commit `.env`. Every setting in `config.toml` can also be set as `DRIVEOS_<SECTION>__<KEY>`.

## 7. Audio

A reSpeaker XVF3800 USB array is the reference microphone and speaker. Other ALSA/PipeWire devices work.

```bash
bash scripts/pi_audio_setup.sh --check       # report routing and run a smoke test; changes nothing
bash scripts/pi_audio_setup.sh               # fix a stale ALSA default and make the XVF3800 the PipeWire default
bash scripts/pi_audio_setup.sh --user        # same, with a per-user ~/.asoundrc instead of sudo
bash scripts/pi_audio_setup.sh --undo        # restore the previous configuration
bash scripts/set_xvf3800_levels.sh           # set the XVF3800 mixer to 0 dB (run_pi.sh does this at every start)
.venv/bin/python -m sounddevice              # list input devices; pin one with DRIVEOS_VOICE__INPUT_DEVICE=<n>
```

A stale `/etc/asound.conf` left by another sound-card installer is the usual cause of "Cannot get card index" errors when
Nova speaks; `pi_audio_setup.sh` detects and fixes it.

## 8. F455 face-ID camera

DriveOS talks to the Intel RealSense ID F455 through the SDK's Python binding `rsid_py` (SDK 3.6.1, firmware
9.16.1.1). Build it once and DriveOS finds it automatically:

```bash
git clone --depth 1 --branch v3.6.1 https://github.com/realsenseai/RealSenseID.git ~/repos/RealSenseID
cmake -S ~/repos/RealSenseID -B ~/repos/RealSenseID/build-device -DCMAKE_BUILD_TYPE=Release \
      -DRSID_PY=ON -DRSID_SAMPLES=ON -DRSID_PIPELINE=OFF
cmake --build ~/repos/RealSenseID/build-device -j2

# live camera preview in the approval sheet (optional)
cmake -S ~/repos/RealSenseID -B ~/repos/RealSenseID/build-preview -DCMAKE_BUILD_TYPE=Release \
      -DRSID_PY=ON -DRSID_PREVIEW=ON -DRSID_PIPELINE=OFF -DRSID_SAMPLES=OFF -DRSID_TOOLS=OFF
cmake --build ~/repos/RealSenseID/build-preview -j2
sudo usermod -aG dialout $USER               # the camera is /dev/ttyACM*
```

Build with the same Python ABI as the DriveOS environment. Use `biometrics.f455_lib` or `DRIVEOS_F455_LIB` to point to a
different build directory. Details, configuration keys and how the camera result maps to DriveAuth evidence are in
[docs/f455.md](docs/f455.md).

## Raspberry Pi notes

- A Pi 5 without a fan throttles at about 70 to 75 degrees C, which slows speech and map rendering. `run_pi.sh` prints
  `vcgencmd` temperature and throttling flags at start and end; fix cooling if it warns.
- Use the official 27 W power supply; check `vcgencmd get_throttled` for undervoltage.
- Run the HMI from a logged-in Wayland desktop session, or with `--backend drm` on a bare console.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `driveos doctor` reports voice off | `sudo apt install libportaudio2`; check the input device with `python -m sounddevice`. |
| Nova shows text but is silent | `bash scripts/pi_audio_setup.sh --check`; `sudo apt install espeak-ng`; check the output device. |
| `Cannot get card index for ...` | Stale `/etc/asound.conf`; run `bash scripts/pi_audio_setup.sh`. |
| Quiet speaker on the XVF3800 | `bash scripts/set_xvf3800_levels.sh`. |
| Log says parakeet unavailable, using whisper | AppImage: check the network and relaunch it; first-run model download resumes automatically. Source install: run `driveos fetch-models` and, if needed, `scripts/build_parakeet.sh`. |
| Speech model cannot be loaded | The first `fetch-models` needs internet; afterwards it is cached and works offline. |
| Wake phrase is missed | Pause briefly before speaking, or use the voice button; check the microphone level with `driveos voice-check`. |
| HMI: "port already in use" from `run_pi.sh` | Another back-end is running: `pkill -f driveos.server`. |
| HMI window does not open | Check `WAYLAND_DISPLAY`/`DISPLAY`, or use `--backend drm` on a console; install `libsdl2-dev` when building. |
| Nav shows no streets | No Mapbox token, a rejected token, or the HMI was built without the MapLibre package. See 2.6 and `driveos doctor`. |
| Face ID unavailable | `rsid_py` not found or the camera is not on `/dev/ttyACM*`; see section 8 and `docs/f455.md`. Approvals step up to a one-time code. |
| GPS chip says "searching" | `driveos gps --source auto` shows what is received; check the group membership and source string. |
| AppImage will not start | `--appimage-extract-and-run` when FUSE is missing; the image needs glibc 2.35 or later. |
| `uv sync` cannot find Python | `uv python install 3.13`. |

Still stuck? Run `driveos doctor` and include its output, without keys, in your report. Running on a head unit as a
kiosk service (systemd, gpsd, cabin camera) is described in [deploy/README.md](deploy/README.md).
