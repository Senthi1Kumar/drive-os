"""Text-to-speech engines. All implement `say()` (awaits until playback ends) and `stop()` for barge-in."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import queue
import re
import shutil
import threading
from collections.abc import Iterable
from pathlib import Path

from ..config import VoiceConfig

log = logging.getLogger("driveos.tts")


class SilentSpeaker:
    """No audio: holds each line for a natural reading time so the HMI paces like spoken output."""

    def __init__(self, words_per_s: float = 3.2):
        self.wps = words_per_s
        self._cancel = asyncio.Event()

    async def say(self, text: str) -> None:
        self._cancel.clear()
        try:
            await asyncio.wait_for(self._cancel.wait(), min(6.0, 0.4 + len(text.split()) / self.wps))
        except TimeoutError:
            pass

    def stop(self) -> None:
        self._cancel.set()


class KokoroSpeaker:
    def __init__(self, cfg: VoiceConfig):
        from kokoro_onnx import Kokoro  # optional dependency: driveos[voice]

        if not cfg.kokoro_model or not cfg.kokoro_voices:
            raise ValueError("kokoro_model and kokoro_voices paths are required")
        self.k = Kokoro(str(cfg.kokoro_model), str(cfg.kokoro_voices))
        self.voice = cfg.kokoro_voice
        self.device = cfg.output_device

    def _play(self, text: str) -> None:
        import sounddevice as sd

        samples, rate = self.k.create(text, voice=self.voice, speed=1.05, lang="en-us")
        sd.play(samples, rate, device=self.device)
        sd.wait()

    async def say(self, text: str) -> None:
        await asyncio.to_thread(self._play, text)

    def stop(self) -> None:
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass


PHONON_MODEL = "gradium/phonon-31m-en"
_MAX_CLAUSE_WORDS = 8


def split_clauses(text: str, max_words: int = _MAX_CLAUSE_WORDS) -> list[str]:
    """Split at clause punctuation, or after `max_words` words without any, so playback can start early."""
    rest = text.strip()
    out: list[str] = []
    while rest:
        clause = re.search(r"[.!?;,]\s+", rest)
        words = list(re.finditer(r"\S+\s+", rest))
        end = clause.end() if clause else None
        if len(words) >= max_words and (end is None or words[max_words - 1].end() < end):
            end = words[max_words - 1].end()
        if end is None:
            break
        piece, rest = rest[:end].strip(), rest[end:]
        if re.search(r"\w", piece):
            out.append(piece)
    if re.search(r"\w", rest):
        out.append(rest.strip())
    return out


def phonon_cache_dir() -> Path:
    """Where ptts keeps the Phonon weights: the Hugging Face hub cache (override with HF_HUB_CACHE / HF_HOME)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def load_phonon(duration_delta: float = -0.25):
    import ptts  # optional dependency: driveos[voice]

    conditions = {"duration_delta": duration_delta} if duration_delta else {}
    return ptts.TTS(lang="en", conditions=conditions)


def fetch_phonon_model() -> Path:
    """Download (if missing) and initialise the Phonon model so later runs work offline."""
    load_phonon()
    return phonon_cache_dir()


class _SdSink:
    """Blocking float32 mono output on the default PortAudio device, resampled when the device needs it."""

    def __init__(self, rate: int, device=None):
        import numpy as np
        import sounddevice as sd

        self._np = np
        self.src_rate = rate
        self.rate = rate
        try:
            sd.check_output_settings(device=device, samplerate=rate, channels=1)
        except Exception:
            info = sd.query_devices(device, "output")
            self.rate = int(info["default_samplerate"])
        self._stream = sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32", device=device)
        self._stream.start()

    def write(self, samples) -> None:
        if self.rate != self.src_rate:
            from scipy.signal import resample_poly

            g = math.gcd(self.rate, self.src_rate)
            samples = resample_poly(samples, self.rate // g, self.src_rate // g).astype(self._np.float32)
        self._stream.write(self._np.ascontiguousarray(samples, dtype=self._np.float32).reshape(-1, 1))

    def abort(self) -> None:
        try:
            self._stream.abort()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass


class PhononSpeaker:
    """Phonon 31M English on CPU: clause-chunked synthesis, playback starts on the first generated chunk."""

    def __init__(self, cfg: VoiceConfig, model=None, sink_factory=None):
        self.voice = cfg.phonon_voice
        self.device = cfg.output_device
        self._model = model if model is not None else load_phonon(cfg.phonon_duration_delta)
        self._sink_factory = sink_factory or (lambda rate: _SdSink(rate, self.device))
        self._model_lock = threading.Lock()
        self._state = threading.Lock()
        self._cancel = threading.Event()
        self._sink = None
        self._warm_up()

    def _warm_up(self) -> None:
        for _ in self._chunks("Ready.", threading.Event()):
            pass

    def _chunks(self, text: str, cancel: threading.Event):
        """Yield (float32 samples, rate); stops early when `cancel` is set."""
        import numpy as np

        with self._model_lock, self._model.stream(text, voice=self.voice) as audio:
            rate = int(audio.sample_rate)
            for chunk in audio:
                if cancel.is_set():
                    return
                yield np.asarray(chunk, dtype=np.float32).reshape(-1), rate

    def _produce(self, clauses: Iterable[str], out: queue.Queue, cancel: threading.Event) -> None:
        def put(item) -> bool:
            while not cancel.is_set():
                try:
                    out.put(item, timeout=0.05)
                    return True
                except queue.Full:
                    pass
            return False

        try:
            for clause in clauses:
                for item in self._chunks(clause, cancel):
                    if not put(item):
                        return
        except Exception as e:
            log.warning("Phonon synthesis failed: %s", e)
        finally:
            put(None)

    def _speak(self, text: str, cancel: threading.Event) -> None:
        clauses = split_clauses(text)
        if not clauses:
            return
        q: queue.Queue = queue.Queue(maxsize=16)
        producer = threading.Thread(target=self._produce, args=(clauses, q, cancel), daemon=True)
        producer.start()
        sink = None
        try:
            while not cancel.is_set():
                try:
                    item = q.get(timeout=0.05)
                except queue.Empty:
                    continue
                if item is None:
                    break
                samples, rate = item
                if sink is None:
                    sink = self._sink_factory(rate)
                    with self._state:
                        self._sink = sink
                    if cancel.is_set():
                        break
                sink.write(samples)
        except Exception as e:
            if not cancel.is_set():
                log.warning("Phonon playback failed: %s", e)
        finally:
            cancel.set()
            producer.join(timeout=2.0)
            with self._state:
                self._sink = None
            if sink is not None:
                sink.close()

    async def say(self, text: str) -> None:
        cancel = threading.Event()
        with self._state:
            self._cancel = cancel
        await asyncio.to_thread(self._speak, text, cancel)

    def stop(self) -> None:
        with self._state:
            self._cancel.set()
            sink = self._sink
        if sink is not None:
            sink.abort()


class Pyttsx3Speaker:
    def __init__(self, cfg: VoiceConfig):
        import pyttsx3  # optional dependency: driveos[voice]

        self._engine = pyttsx3.init()
        self._engine.setProperty("rate", 182)
        self._lock = threading.Lock()

    def _speak(self, text: str) -> None:
        with self._lock:
            self._engine.say(text)
            self._engine.runAndWait()

    async def say(self, text: str) -> None:
        await asyncio.to_thread(self._speak, text)

    def stop(self) -> None:
        try:
            self._engine.stop()
        except Exception:
            pass


class MacSaySpeaker:
    def __init__(self, cfg: VoiceConfig):
        if not shutil.which("say"):
            raise RuntimeError("macOS `say` not available")
        self._proc: asyncio.subprocess.Process | None = None

    async def say(self, text: str) -> None:
        self._proc = await asyncio.create_subprocess_exec("say", "-r", "190", text)
        await self._proc.wait()

    def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()


def build_speaker(cfg: VoiceConfig):
    if not cfg.enabled or cfg.tts == "none":
        return SilentSpeaker()
    import sys

    engines = {"phonon": PhononSpeaker, "kokoro": KokoroSpeaker, "pyttsx3": Pyttsx3Speaker, "say": MacSaySpeaker}
    fallbacks = (["say"] if sys.platform == "darwin" else []) + ["phonon", "pyttsx3"]
    order = [cfg.tts] + [e for e in fallbacks if e != cfg.tts]
    for name in order:
        try:
            sp = engines[name](cfg)
            if name != cfg.tts:
                log.warning("TTS %s unavailable — speaking with %s instead", cfg.tts, name)
            return sp
        except Exception as e:
            log.info("TTS %s unavailable (%s)", name, e)
    log.error("no text-to-speech engine available — Nova's replies are shown on screen only")
    return SilentSpeaker()
