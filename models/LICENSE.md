# Bundled model provenance and licences

The two small ONNX models below ship with the source tree and are loaded from disk at start-up.

## silero_vad.onnx

- Source: Silero VAD v5 (`snakers4/silero-vad`, `src/silero_vad/data/silero_vad.onnx`), taken unmodified from the upstream
  repository.
- SHA-256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`
- Licence: MIT, Copyright (c) Silero Team.

## smart-turn-v3.2-cpu.onnx

- Source: Smart Turn v3.2 (CPU build) from the `pipecat-ai/smart-turn-v3` model, as bundled in pipecat-ai 1.14.0
  (`pipecat/audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx`), used unmodified.
- SHA-256: `2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f`
- Licence: BSD-2-Clause (Pipecat / Daily).

## src/driveos/voice/whisper_features.py (code)

- Vendored from pipecat-ai 1.14.0 `pipecat/audio/turn/smart_turn/_whisper_features.py`: BSD-2-Clause,
  Copyright (c) 2024-2026, Daily.
- Portions derived from Hugging Face Transformers (`feature_extraction_whisper.py`, `audio_utils.py`):
  Apache-2.0, Copyright 2022 The HuggingFace Inc. team; Copyright 2023 The HuggingFace Inc. team and the
  librosa & torchaudio authors.

## src/driveos/voice/smart_turn.py and silero_vad.py (code)

- Inference paths ported from pipecat-ai 1.14.0 `local_smart_turn_v3.py` (BSD-2-Clause, Daily) and the
  reference Silero VAD ONNX inference (Silero Team, MIT). The Pipecat debug audio writer is intentionally not ported.
