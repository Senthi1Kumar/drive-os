"""Kaldi-compatible log-mel filterbank in numpy (80 bins, 25 ms / 10 ms, povey window, dither 0).

Matches ``torchaudio.compliance.kaldi.fbank`` defaults used by WeSpeaker: waveform scaled by 2**15,
DC removal, 0.97 pre-emphasis, 512-point FFT, 20 Hz low edge, log floor at float32 eps.
"""

from __future__ import annotations

import numpy as np

_EPS = float(np.finfo(np.float32).eps)


def _mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def _mel_banks(n_mels: int, n_fft: int, rate: int, low: float = 20.0, high: float | None = None) -> np.ndarray:
    high = rate / 2.0 if high is None else high
    nyq = rate / 2.0
    fft_bins = n_fft // 2
    bin_hz = nyq / fft_bins
    mlo, mhi = _mel(low), _mel(high)
    delta = (mhi - mlo) / (n_mels + 1)
    banks = np.zeros((n_mels, fft_bins + 1), dtype=np.float64)
    freqs = np.arange(fft_bins) * bin_hz
    mel = _mel(freqs)
    for m in range(n_mels):
        left, center, right = mlo + m * delta, mlo + (m + 1) * delta, mlo + (m + 2) * delta
        up = (mel - left) / (center - left)
        down = (right - mel) / (right - center)
        banks[m, :fft_bins] = np.maximum(0.0, np.minimum(up, down))
    return banks


_BANKS: dict[tuple[int, int], np.ndarray] = {}


def fbank(wave: np.ndarray, rate: int = 16000, n_mels: int = 80) -> np.ndarray:
    """wave: float32 in [-1, 1]. Returns (frames, n_mels) float32 log-mel energies."""
    win, shift, n_fft = 400, 160, 512
    x = np.asarray(wave, dtype=np.float64) * 32768.0
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    n = 1 + (len(x) - win) // shift
    idx = np.arange(win)[None, :] + shift * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    prev = np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    frames = frames - 0.97 * prev
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / (win - 1))) ** 0.85
    frames = frames * window
    spec = np.abs(np.fft.rfft(frames, n=n_fft, axis=1)) ** 2
    key = (n_mels, rate)
    if key not in _BANKS:
        _BANKS[key] = _mel_banks(n_mels, n_fft, rate)
    mel = spec @ _BANKS[key].T
    return np.log(np.maximum(mel, _EPS)).astype(np.float32)
