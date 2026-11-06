# Third-party notices

DriveOS itself is proprietary (see [LICENSE](LICENSE)). It uses the components below under their own licences. "Use"
says how each is included: **linked** (compiled or imported at run time), **bundled** (a copy ships in this repository
or the AppImage), or **downloaded** (fetched by the user or by `driveos fetch-models`, not shipped here). Licence
identifiers are taken from the upstream projects and package metadata at the versions in `uv.lock`.

## Authentication

| Component | Version | Licence | URL | Use |
|---|---|---|---|---|
| DriveAuth Edge (`driveauth-edge`) | 1.0.0 | Apache-2.0 | See `packaging/vendor/README.txt` | Linked, used unmodified as a library. A built wheel is bundled in `packaging/vendor/`. Full licence text: [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt). Copyright 2026 DriveAuth Edge contributors. |

## Speech

| Component | Version | Licence | URL | Use |
|---|---|---|---|---|
| parakeet.cpp (mudler) | v0.6.1, commit `5c9b0fbbd8e611d88e47d7e1ebae9bca9b447e8f` | MIT | https://github.com/mudler/parakeet.cpp | Built from source by `scripts/build_parakeet.sh`; its shared libraries are loaded through ctypes and bundled in the AppImage. Includes ggml (MIT). |
| NVIDIA parakeet-tdt_ctc-110m model, GGUF q8_0 | Hub revision `07a08a3a405d20c5434b9a1442f6e6cf571f57ad` of `mudler/parakeet-cpp-gguf` | CC-BY-4.0; the conversion repository applies the source model's licence | https://huggingface.co/nvidia/parakeet-tdt_ctc-110m | Downloaded. **Attribution:** speech recognition model parakeet-tdt_ctc-110m by NVIDIA, licensed CC-BY-4.0, converted to GGUF by the parakeet.cpp project. |
| faster-whisper | 1.2.1 | MIT | https://github.com/SYSTRAN/faster-whisper | Linked; fallback STT. Whisper `base.en` / `tiny.en` weights (MIT, OpenAI) are downloaded. |
| CTranslate2 | 4.8.5 | MIT | https://github.com/OpenNMT/CTranslate2 | Linked through faster-whisper. |
| Silero VAD | v5 | MIT | https://github.com/snakers4/silero-vad | Bundled: `models/silero_vad.onnx`. Copyright (c) Silero Team. |
| Smart Turn v3.2 (CPU) | v3.2 | BSD-2-Clause | https://github.com/pipecat-ai/smart-turn | Bundled: `models/smart-turn-v3.2-cpu.onnx`. Copyright (c) Pipecat / Daily. |
| Whisper feature extraction (from pipecat-ai 1.14.0) | 1.14.0 | BSD-2-Clause, with portions Apache-2.0 | https://github.com/pipecat-ai/pipecat | Bundled source adapted as `src/driveos/voice/whisper_features.py` (Copyright (c) 2024-2026 Daily; portions derived from Hugging Face Transformers, Apache-2.0, Copyright The HuggingFace Inc. team, and the librosa and torchaudio authors). |
| Phonon text-to-speech: `ptts` package | 0.5.0 | MIT OR Apache-2.0 | https://github.com/gradium-ai/xn-ptts | Linked. |
| Phonon model weights (`gradium/phonon-31m-en`) | Hub revision at download time | Apache-2.0 (per the model card) | https://huggingface.co/gradium/phonon-31m-en | Downloaded by `driveos fetch-models`. |
| Kokoro-ONNX (optional) | 0.6.1 | MIT; Kokoro model Apache-2.0 | https://github.com/thewh1teagle/kokoro-onnx | Linked; model files downloaded by the user. |
| pyttsx3 (fallback voice) | 2.99 | MPL-2.0 | https://github.com/nateshmbhat/pyttsx3 | Linked. |
| openWakeWord (optional trained wake word) | 0.6.x | Code Apache-2.0; its pre-trained models carry their own, more restrictive terms (licence to be confirmed before use) | https://github.com/dscripka/openWakeWord | Optional, installed by `scripts/setup.sh`. Not used by default (the default wake engine is parakeet). |
| ONNX Runtime | 1.31.1 | MIT | https://onnxruntime.ai | Linked. |

## Voice biometrics

| Component | Version | Licence | URL | Use |
|---|---|---|---|---|
| WeSpeaker ResNet34 (ONNX conversion `soniqo/WeSpeaker-ResNet34-LM-ONNX`) | Hub revision at download time | CC-BY-4.0 (attribution required); WeSpeaker toolkit code is Apache-2.0 | https://huggingface.co/soniqo/WeSpeaker-ResNet34-LM-ONNX | Downloaded by `driveos fetch-models`. Credit the WeSpeaker project and the ONNX conversion author. |
| ReDimNet2-B6 (ONNX, `soniqo/ReDimNet2-B6-ONNX-FP32`) | Hub revision at download time | MIT | https://huggingface.co/soniqo/ReDimNet2-B6-ONNX-FP32 | Downloaded by `driveos fetch-models`. |
| AASIST-L anti-spoofing | weights exported from clovaai/aasist | MIT, Copyright (c) 2021-present NAVER Corp. | https://github.com/clovaai/aasist | Bundled: `src/driveos/biometrics/assets/aasist-l.onnx`; licence in `AASIST-LICENSE.txt` beside it. Trained on ASVspoof2019 LA. |
| Impostor cohort embeddings (`cohort.npz`) | n/a | Derived from LibriSpeech dev-clean, CC-BY-4.0 (embeddings only, no audio) | https://www.openslr.org/12 | Bundled. **Attribution:** LibriSpeech by Panayotov et al., CC-BY-4.0. |

## User interface and maps

| Component | Version | Licence | URL | Use |
|---|---|---|---|---|
| LVGL | 9.6.0 | MIT | https://lvgl.io | Fetched at configure time and linked into the HMI (and thus the AppImage). |
| MapLibre Native (C bindings) | 0.202610.0, commit `dd97d20f8fab6990cd87c13f091ce00e662707bd` | BSD-2-Clause | https://github.com/maplibre/maplibre-native | The checksum-pinned native package is fetched at build time and linked into the HMI; its shared library and licence ship in the AppImage. |
| Mapbox (maps, directions, search; Mapbox GL JS 3.33.0 in the legacy desktop view) | service; GL JS bundled in `src/driveos/ui/web/vendor/` | Mapbox Terms of Service; attribution and the Mapbox logo are required wherever Mapbox data is shown | https://www.mapbox.com/legal/tos | **A Mapbox access token is required** for the live map. The user supplies their own token and accepts Mapbox's terms. Data from OpenStreetMap (ODbL) is shown with the "© Mapbox © OpenStreetMap" attribution. |
| Manrope | n/a | SIL OFL 1.1 | https://github.com/sharanda/manrope | Bundled fonts in `src/driveos/ui/assets/fonts/` and bitmap fonts generated from them in `hmi_lvgl/fonts/`. Licence: `src/driveos/ui/assets/fonts/OFL.txt`. |
| Lucide icons | n/a | ISC (portions MIT, Feather) | https://lucide.dev | Bundled SVGs and generated icon data; licence in `hmi_lvgl/assets/icons/LICENSE-lucide.txt`. |
| PySide6 / Qt (legacy desktop interface, `driveos run`) | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | https://www.qt.io | Linked in source installs only; not part of the AppImage. |
| Intel RealSense ID SDK (`rsid_py`) | 3.6.1 | Apache-2.0 | https://github.com/realsenseai/RealSenseID | Not distributed here; built by the user for the F455 camera. |

## Generated images

The food, drink and place images in `hmi_lvgl/assets/food/` are synthetic, generated with
Tongyi-MAI/Z-Image-Turbo (Apache-2.0, https://huggingface.co/Tongyi-MAI/Z-Image-Turbo). They contain no real products,
text or logos, are labelled "Sample Only" in the interface, and are documented in
`hmi_lvgl/assets/food/LICENSE.md`.

## Cloud services (optional, terms apply)

| Service | Use |
|---|---|
| OpenRouter (https://openrouter.ai) | Language-model routing for the optional agent; OpenRouter's and each model provider's terms apply. |
| Exa (https://exa.ai) | Optional web search. |
| Mapbox (https://www.mapbox.com) | Maps, routing and search, see above. |
| Google Places API (New) | Optional place photos, ratings and price levels. |
| Twilio, Stripe, Square | Optional one-time-code SMS, card payments and ordering. |
| Hugging Face Hub | Model downloads. |

## Python dependencies

Installed from PyPI at the versions pinned in `uv.lock`; none are modified.

| Package | Licence |
|---|---|
| httpx, websockets, scikit-learn, numpy, soundfile | BSD-3-Clause |
| qasync | BSD-2-Clause |
| pydantic, pydantic-settings, rapidfuzz, mcp, sounddevice, webrtcvad-wheels | MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| huggingface-hub, pyarrow, requests | Apache-2.0 |
| Pillow | MIT-CMU |
| scipy | BSD-3-Clause |
| tqdm | MPL-2.0 AND MIT |

## Compiled build tools in the AppImage

CPython (python-build-standalone, PSF-2.0), linuxdeploy and appimagetool (MIT) are used at build time; the Python
runtime is bundled in the AppImage under the PSF licence.
