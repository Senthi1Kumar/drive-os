# Changelog

## 1.0.0 — initial release

First release of DriveOS as a standalone product.

### Voice
- Wake phrase ("Hey Nova") and speech-to-text on the device with parakeet.cpp (NVIDIA parakeet-tdt_ctc-110m, CPU),
  with faster-whisper as an automatic fallback.
- Silero VAD and Smart Turn v3 decide when you have finished speaking, so short pauses do not cut a sentence in two.
  One wake phrase opens a continuous conversation; follow-up turns need no wake phrase.
- Natural spoken replies with the Phonon text-to-speech model, with Kokoro and system voices as alternatives.
- Voice-button barge-in, spoken one-time codes and spoken navigation cues that wait while you are talking.

### Ordering and places
- Find coffee, food, fuel, chargers, pharmacies and stores by voice or touch; open a menu, choose options and quantities,
  edit the cart and track the order to pickup.
- Real nearby places through Mapbox search, along the route or around the car, with directions-only handling for places
  that cannot take orders; an offline sample catalogue for demos.
- Optional web search through Exa for opening hours, reviews and charger status, with queries scrubbed on the device.
- Optional OpenRouter tool agent for open-ended requests; on-device rules still handle every approval.

### Payment and biometrics
- Voice-first approval ladder powered by DriveAuth Edge: spoken confirmation for small amounts, voice-ID challenge with
  random digits and advisory anti-spoof measurement, Face ID, then a one-time code. The exact total is re-priced and shown before approval.
- Intel RealSense ID F455 Face ID with a live camera preview in the approval sheet.
- One-time codes by SMS or on-screen phone notification, spoken or typed, with attempt limits and masking.
- Few-shot voice enrollment with a quality gate; `driveos voice-check` calibrates thresholds for your microphone.
- Signed, hash-chained transaction ledger; `driveos ledger verify` detects tampering.

### Navigation
- MapLibre Native map on the head unit with Mapbox styles, traffic-aware routing, congestion-coloured routes, spoken
  turn-by-turn guidance, lane guidance, automatic re-routing and a follow-the-car view.
- Live GPS from serial NMEA receivers, gpsd, TCP/UDP streams or a recorded drive; simulated driving for demos.

### Platform
- Native LVGL front-end (Wayland, X11 or DRM/KMS) talking to the Python back-end over a local WebSocket.
- Raspberry Pi 5 (arm64) and x86_64 Linux; a single aarch64 AppImage with a signed
  checksum, built on Ubuntu 22.04 (glibc 2.35).
- `driveos doctor`, `scripts/pi_audio_setup.sh` and `scripts/set_xvf3800_levels.sh` for installation and audio checks.

### Privacy
- Voice prints and face templates stay on the device; raw audio is held in memory only.
- Personal files are created owner-only, logs carry no transcripts unless explicitly enabled, conversation history is
  purged on a schedule, and the language-model provider is asked for zero data retention.
