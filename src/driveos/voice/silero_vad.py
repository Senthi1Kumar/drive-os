"""Silero VAD v5 (ONNX + NumPy) utterance segmenter with pause/resume/reset.

Based on the reference Silero VAD v5 ONNX inference (MIT, Silero Team). Audio lives in RAM only.

    feed(pcm16) -> [(event, audio | None)]
        speech_started   speech onset (no audio yet)
        pause            min_silence_ms of quiet after real speech; audio is the whole utterance so far
        speech_stopped   safety ceiling (max_utterance_s) reached; audio is the capped utterance

A ``pause`` keeps the utterance in the segmenter. ``resume()`` means the turn detector judged it
incomplete, so the next speech continues the same utterance; ``reset()`` drops all audio and model state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None

WINDOW = 512  # 16 kHz Silero chunk
CONTEXT = 64
STATE_SHAPE = (2, 1, 128)
RATE = 16000

DEFAULT_ONNX = Path(__file__).resolve().parents[3] / "models" / "silero_vad.onnx"


@dataclass
class SileroVadConfig:
    threshold: float = 0.55
    min_silence_ms: int = 600
    min_speech_ms: int = 400
    speech_pad_ms: int = 400
    max_utterance_s: float = 60.0
    onnx_path: str | Path | None = None


class _SileroOnnx:
    """Stateful wrapper around the Silero session (16 kHz only)."""

    def __init__(self, session):
        self.session = session
        self.reset_states()

    @staticmethod
    def load(path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Silero VAD model missing: {path} (models are never downloaded at startup)")
        if ort is None:
            raise RuntimeError("onnxruntime not installed")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        return ort.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts)

    def reset_states(self) -> None:
        self._state = np.zeros(STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def __call__(self, x: np.ndarray) -> float:
        x = np.asarray(x, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != WINDOW:
            raise ValueError(f"expected {WINDOW} samples, got {x.shape[1]}")
        x_in = np.concatenate([self._context, x], axis=1)
        out, state = self.session.run(None, {"input": x_in, "state": self._state,
                                             "sr": np.array(RATE, dtype=np.int64)})
        self._state = state
        self._context = x_in[:, -CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


class SileroVadSegmenter:
    def __init__(self, cfg: SileroVadConfig | None = None, *, session=None):
        self.cfg = cfg or SileroVadConfig()
        if session is None:
            path = Path(self.cfg.onnx_path) if self.cfg.onnx_path else DEFAULT_ONNX
            session = _SileroOnnx.load(path)
        self._model = _SileroOnnx(session)
        self._pad = RATE * self.cfg.speech_pad_ms // 1000
        self._min_sil = RATE * self.cfg.min_silence_ms // 1000
        self._min_speech = RATE * self.cfg.min_speech_ms // 1000
        self._max_utt = int(self.cfg.max_utterance_s * RATE)
        self._neg = max(0.01, self.cfg.threshold - 0.15)
        self.last_prob = 0.0
        self._clear()

    def _clear(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._utterance: list[np.ndarray] = []
        self._utt_len = 0
        self._pre: list[np.ndarray] = []
        self._pre_samples = 0
        self._triggered = False
        self._temp_end = 0
        self._sample_i = 0
        self._active = 0
        self._last_voiced = 0
        self._stash: np.ndarray | None = None
        self._resume_pending = False

    def reset(self) -> None:
        """Drop all audio and model state."""
        self._model.reset_states()
        self.last_prob = 0.0
        self._clear()

    def resume(self) -> None:
        """The last pause was judged incomplete: keep its audio and continue the same utterance."""
        if self._stash is None:
            return
        if self._triggered:   # speech already restarted as a fresh utterance: merge it behind the stash
            self._utterance.insert(0, self._stash)
            self._utt_len += len(self._stash)
            self._last_voiced += len(self._stash)
            self._active = max(self._active, self._min_speech)  # the stash already passed the gate
            self._stash = None
        else:
            self._resume_pending = True

    def _remember_pre(self, chunk: np.ndarray) -> None:
        self._pre.append(chunk)
        self._pre_samples += len(chunk)
        while self._pre and self._pre_samples > self._pad:
            first = self._pre[0]
            if self._pre_samples - len(first) >= self._pad:
                self._pre.pop(0)
                self._pre_samples -= len(first)
            else:
                drop = self._pre_samples - self._pad
                self._pre[0] = first[drop:]
                self._pre_samples -= drop
                break

    def _end_utterance(self) -> np.ndarray | None:
        """Close the current utterance; return its audio, or None when it was a noise blip."""
        audio = np.concatenate(self._utterance) if self._utterance else np.zeros(0, dtype=np.float32)
        active, last_voiced = self._active, self._last_voiced
        self._utterance = []
        self._utt_len = 0
        self._pre = []
        self._pre_samples = 0
        self._triggered = False
        self._temp_end = 0
        self._active = 0
        self._last_voiced = 0
        self._resume_pending = False
        if active < self._min_speech or len(audio) < self._min_speech:
            self._stash = None
            return None
        if last_voiced > 0:
            audio = audio[: min(len(audio), last_voiced + self._pad)]
        return audio

    def feed(self, pcm16: bytes) -> list[tuple[str, np.ndarray | None]]:
        events: list[tuple[str, np.ndarray | None]] = []
        if not pcm16:
            return events
        incoming = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, incoming])
        while len(self._buf) >= WINDOW:
            chunk = self._buf[:WINDOW].copy()
            self._buf = self._buf[WINDOW:]
            self._sample_i += WINDOW
            prob = self._model(chunk)
            self.last_prob = prob
            voiced = prob >= self.cfg.threshold
            if voiced:
                self._temp_end = 0
            if voiced and not self._triggered:
                self._triggered = True
                head: list[np.ndarray] = []
                if self._resume_pending and self._stash is not None:
                    head = [self._stash]
                    self._active = max(self._active, self._min_speech)  # stash already passed the gate
                    resumed = True
                else:
                    resumed = False     # a stash left over from an undecided pause stays available to resume()
                self._resume_pending = False
                self._utterance = head + self._pre + [chunk]
                self._pre = []
                self._pre_samples = 0
                self._utt_len = sum(len(c) for c in self._utterance)
                self._active += WINDOW
                self._last_voiced = self._utt_len
                if not resumed:
                    events.append(("speech_started", None))
                continue
            if not self._triggered:
                self._remember_pre(chunk)
                continue
            self._utterance.append(chunk)
            self._utt_len += WINDOW
            if prob >= self._neg:
                self._active += WINDOW
                self._last_voiced = self._utt_len
            if self._utt_len >= self._max_utt:
                audio = self._end_utterance()
                self._stash = None
                if audio is not None:
                    events.append(("speech_stopped", audio))
                continue
            if prob < self._neg:
                if not self._temp_end:
                    self._temp_end = self._sample_i
                if self._sample_i - self._temp_end < self._min_sil:
                    continue
                audio = self._end_utterance()
                if audio is not None:
                    self._stash = audio
                    events.append(("pause", audio))
        return events
