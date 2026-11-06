"""Smart Turn v3 end-of-turn detector: direct ONNX inference, RAM only.

Port of the Smart Turn v3 inference path from pipecat-ai 1.14.0 (BSD-2-Clause, Copyright (c) 2024-2026,
Daily). No Pipecat runtime, no debug audio writer: audio and features never leave memory.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from .whisper_features import compute_whisper_log_mel_features

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None

RATE = 16000
MAX_SAMPLES = 8 * RATE   # the model sees the most recent 8 s
DEFAULT_ONNX = Path(__file__).resolve().parents[3] / "models" / "smart-turn-v3.2-cpu.onnx"


class SmartTurnAnalyzer:
    def __init__(self, model_path: str | Path | None = None, *, session=None):
        if session is None:
            path = Path(model_path) if model_path else DEFAULT_ONNX
            if not path.exists():
                raise FileNotFoundError(f"Smart Turn model missing: {path} (models are never downloaded at startup)")
            if ort is None:
                raise RuntimeError("onnxruntime not installed")
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            session = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
        self._session = session
        self._lock = threading.Lock()
        self.last_probability = 0.0

    @staticmethod
    def prepare(audio: np.ndarray) -> np.ndarray:
        """Keep the last 8 s, or left-pad with zeros to 8 s."""
        if len(audio) > MAX_SAMPLES:
            return audio[-MAX_SAMPLES:]
        if len(audio) < MAX_SAMPLES:
            return np.pad(audio, (MAX_SAMPLES - len(audio), 0), mode="constant", constant_values=0)
        return audio

    def probability(self, audio: np.ndarray) -> float:
        x = self.prepare(np.asarray(audio, dtype=np.float32))
        feats = compute_whisper_log_mel_features(x, do_normalize=True)[None, :, :]
        with self._lock:
            out = self._session.run(None, {"input_features": feats})
        p = float(np.asarray(out[0]).reshape(-1)[0])
        self.last_probability = p
        return p

    def complete(self, pcm16: bytes) -> bool:
        """True when the speaker's turn is finished (probability > 0.5). Synchronous and serialized."""
        if not pcm16:
            return False
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        return self.probability(audio) > 0.5
