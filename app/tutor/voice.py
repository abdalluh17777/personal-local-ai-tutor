"""Speech: Whisper (speech -> text) on the GPU, and Kokoro (text -> speech) for the tutor's voice.

Both models load on first use and then stay loaded. Both are GPU-only on purpose: if CUDA is not
available they raise a clear error instead of silently running on the CPU and system RAM.
"""

import importlib.util
import io
import os
import re
import sys
import threading
import time
import wave
from pathlib import Path

import httpx
import numpy as np

from . import config


class VoiceError(RuntimeError):
    pass


_AR = "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
_ARABIC = re.compile(rf"[{_AR}]+")
# A run of Arabic words, with the spaces and punctuation between them (never Latin letters)
_ARABIC_RUN = re.compile(rf"[{_AR}](?:[{_AR}\s.,:;!?\"'«»()\-]*[{_AR}])?[.,!?]*")
_AR_LETTER = "ء-يٮ-ۓۺ-ۼݐ-ݿﭐ-﷿ﹰ-﻿"   # letters, not punctuation
VOICE_LABELS = {"af": "American, female", "am": "American, male", "bf": "British, female", "bm": "British, male"}
JOIN_PAUSE_S = 0.08   # between an English and an Arabic part of the same sentence


_cuda_dirs_added = False


def _add_cuda_dll_dirs() -> None:
    """Make the CUDA 12 DLLs from the nvidia-* wheels findable, for both CTranslate2 and ONNX Runtime.

    PATH matters, not only add_dll_directory: cuDNN 9 loads its sub-libraries (cudnn_engines_*.dll)
    itself with a plain PATH search, and fails without it.
    """
    global _cuda_dirs_added
    if _cuda_dirs_added or sys.platform != "win32":
        return
    spec = importlib.util.find_spec("nvidia")
    for root in (spec.submodule_search_locations if spec else []):
        for bin_dir in sorted(Path(root).glob("*/bin")):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    _cuda_dirs_added = True


def clean_markup(text: str) -> str:
    """Text as it should be read aloud: no markdown or citation labels (Arabic is kept)."""
    text = re.sub(r"\[S\d+\]", "", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_`#>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def clean_for_speech(text: str) -> str:
    """Text the English voice should read: no markdown, citation labels or Arabic script."""
    text = _ARABIC.sub("", clean_markup(text))
    text = re.sub(r"\(\s*[,;:]*\s*\)", "", text)        # brackets left empty after removing Arabic
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def split_languages(text: str) -> list[tuple[str, str]]:
    """Split a sentence into ("en", ...) and ("ar", ...) parts, in order, for the matching voice."""
    parts, pos = [], 0
    for m in _ARABIC_RUN.finditer(text):
        if m.start() > pos:
            parts.append(("en", text[pos:m.start()]))
        parts.append(("ar", m.group()))
        pos = m.end()
    if pos < len(text):
        parts.append(("en", text[pos:]))
    # Brackets and quotes that only framed the other language ("Extinction (الانطفاء) happens")
    trimmed = [(lang, re.sub(r"[\s(\[{\"'«:\-–]+$", "", re.sub(r"^[\s)\]}\"'»:;,\-–]+", "", part)))
               for lang, part in parts]
    # Keep only parts with real words: a lone "؟" or bracket is nothing to say
    return [(lang, part) for lang, part in trimmed
            if re.search(r"[A-Za-z]" if lang == "en" else f"[{_AR_LETTER}]", part)]


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class SpeechToText:
    def __init__(self, model_name: str = config.WHISPER_MODEL, compute_type: str = "float16"):
        self.model_name = model_name
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        with self._lock:
            if self._model is None:
                _add_cuda_dll_dirs()
                from faster_whisper import WhisperModel
                try:
                    self._model = WhisperModel(self.model_name, device="cuda", compute_type=self.compute_type,
                                               download_root=str(config.MODELS_DIR / "whisper"))
                except Exception as e:  # CUDA or cuDNN problems surface here
                    raise VoiceError(f"Whisper could not start on the GPU: {e}") from e
        return self._model

    def transcribe(self, audio: bytes, hint: str = "") -> dict:
        """Transcribe a recorded file (any format PyAV reads: webm/opus, wav, ...)."""
        from faster_whisper import decode_audio

        try:
            samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)
        except Exception as e:   # a broken upload is the request's fault, not the voice's
            raise ValueError(f"Could not read the recording: {e}") from e
        return self.transcribe_array(samples, hint)

    def transcribe_array(self, samples: np.ndarray, hint: str = "") -> dict:
        """Transcribe 16 kHz mono float32 samples (used directly by live conversation)."""
        model = self.load()
        duration = len(samples) / 16000
        if duration < 0.3:
            return {"text": "", "language": None, "duration": round(duration, 1)}

        with self._run_lock:   # one transcription at a time keeps GPU memory use flat
            # The learner speaks English or Arabic. Accented English can be mis-detected as another
            # language, so the choice is limited to these two. Detection runs on the GPU too.
            language = "en"
            try:
                _, _, probs = model.detect_language(samples)
                scores = dict(probs)
                language = "ar" if scores.get("ar", 0) > scores.get("en", 0) else "en"
            except Exception:
                pass

            segments, _info = model.transcribe(
                samples, language=language, beam_size=5,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
                condition_on_previous_text=False,   # stops repetition loops on long recordings
                initial_prompt=hint or None)        # course terms, so they are spelled correctly
            # Whisper sometimes "hears" phrases in silence; drop segments it thinks are not speech
            kept = [s.text.strip() for s in segments
                    if not (s.no_speech_prob > 0.6 and s.avg_logprob < -0.8)]
        return {"text": " ".join(kept).strip(), "language": language, "duration": round(duration, 1)}


class ArabicSpeech:
    """Client for the Arabic voice worker (workers/arabic_tts): SILMA TTS in its own process and environment,
    because it needs PyTorch and numpy 1.x, which would clash with this app's libraries."""

    def __init__(self, url: str = config.ARABIC_TTS_URL):
        self.client = httpx.Client(base_url=url, timeout=httpx.Timeout(120, connect=1))
        self._available: bool | None = None
        self._checked = 0.0

    def available(self) -> bool:
        if self._available is None or time.monotonic() - self._checked > 10:
            try:
                self._available = self.client.get("/health").status_code == 200
            except httpx.HTTPError:
                self._available = False
            self._checked = time.monotonic()
        return self._available

    def mark_unavailable(self) -> None:
        """After a failure: skip the Arabic voice until the next health check (in 10 s)."""
        self._available = False
        self._checked = time.monotonic()

    def speak(self, text: str, speed: float) -> tuple[np.ndarray, int]:
        try:
            r = self.client.post("/speak", json={"text": text, "speed": speed})
        except httpx.HTTPError as e:
            self.mark_unavailable()
            raise VoiceError(f"Arabic voice is not reachable: {e}") from e
        if r.status_code != 200:
            self.mark_unavailable()
            raise VoiceError(f"Arabic voice failed: {r.text[:200]}")
        with wave.open(io.BytesIO(r.content)) as w:
            rate = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return pcm.astype(np.float32) / 32768.0, rate

    def warmup(self) -> None:
        if self.available():
            self.client.post("/warmup", timeout=300)


class TextToSpeech:
    def __init__(self, model_path: Path = config.KOKORO_MODEL, voices_path: Path = config.KOKORO_VOICES,
                 arabic: "ArabicSpeech | None" = None):
        self.model_path = model_path
        self.voices_path = voices_path
        self.arabic = arabic or ArabicSpeech()
        self._session = None
        self._kokoro = None
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._kokoro is not None

    @property
    def device(self) -> str | None:
        """Where Kokoro runs, read from ONNX Runtime each time (None until it is loaded)."""
        if self._session is None:
            return None
        return "gpu" if "CUDAExecutionProvider" in self._session.get_providers() else "cpu"

    def load(self):
        with self._lock:
            if self._kokoro is None:
                if not (self.model_path.exists() and self.voices_path.exists()):
                    raise VoiceError(f"Kokoro model files are missing in {self.model_path.parent}")
                _add_cuda_dll_dirs()
                import onnxruntime as ort
                from kokoro_onnx import Kokoro

                ort.preload_dlls()   # CUDA 12 and cuDNN DLLs from the nvidia-* wheels
                ort.set_default_logger_severity(3)
                options = ort.SessionOptions()
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                options.log_severity_level = 3   # errors only; ORT's performance hints are noise here
                # Weights go straight to the GPU and no CPU memory arena is kept (measured: same speed,
                # ~430 MB less committed memory). Most of the remaining ~0.9 GB of RAM is the CUDA,
                # cuBLAS and cuDNN libraries themselves, which every CUDA program pays once.
                options.enable_cpu_mem_arena = False
                options.add_session_config_entry("session.use_device_allocator_for_initializers", "1")
                cuda = ("CUDAExecutionProvider", {
                    # Every sentence has a different length; an exhaustive cuDNN search would rerun per length
                    "cudnn_conv_algo_search": "HEURISTIC",
                    "cudnn_conv_use_max_workspace": "1",
                })
                # GPU only, as for Whisper: ONNX Runtime would otherwise build a CPU session when a CUDA
                # library fails to load, or switch to the CPU during a later run
                try:
                    session = ort.InferenceSession(str(self.model_path), sess_options=options, providers=[cuda])
                    session.disable_fallback()
                except Exception as e:
                    raise VoiceError(f"Kokoro could not start on the GPU: {e}") from e
                if "CUDAExecutionProvider" not in session.get_providers():
                    raise VoiceError("Kokoro could not start on the GPU (CUDA is not available)")
                kokoro = Kokoro.from_session(session, str(self.voices_path))
                # A real synthesis: a CUDA library that fails to load shows up only in the first run, which
                # now raises instead of switching to the CPU
                try:
                    kokoro.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")
                except Exception as e:
                    raise VoiceError(f"Kokoro could not start on the GPU: {e}") from e
                self._session = session
                self._kokoro = kokoro
        return self._kokoro

    def voices(self) -> list[dict]:
        names = sorted(v for v in self.load().get_voices() if v[:2] in VOICE_LABELS)
        return [{"id": v, "label": f"{v.split('_', 1)[1].title()} ({VOICE_LABELS[v[:2]]})"} for v in names]

    def synthesize(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        """Speech for one sentence as float32 samples and their sample rate.

        English parts are spoken by Kokoro and Arabic parts by the Arabic voice worker, in sentence order.
        """
        parts = split_languages(clean_markup(text))
        has_arabic = any(lang == "ar" for lang, _ in parts)
        arabic_ok = has_arabic and self.arabic.available()
        arabic_error = "the Arabic voice is not running"
        pieces = []
        for lang, part in parts:
            if lang == "ar":
                if arabic_ok:
                    try:
                        pieces.append(self.arabic.speak(part, speed))
                    except VoiceError as e:   # speak() marks the worker unavailable; the English parts are still spoken
                        arabic_ok, arabic_error = False, str(e)
            else:
                pieces.append(self._english(part, voice, speed))
        if not pieces:
            raise VoiceError(f"Arabic speech is not available: {arabic_error}"
                             if has_arabic else "Nothing to read aloud")
        return self._join(pieces)

    def _english(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        kokoro = self.load()
        if voice not in kokoro.get_voices() or voice[:2] not in VOICE_LABELS:
            voice = "af_heart"
        lang = "en-gb" if voice.startswith("b") else "en-us"
        with self._run_lock:   # sentences are synthesised one at a time, in order
            return kokoro.create(clean_for_speech(text)[:1500], voice=voice, speed=min(max(speed, 0.6), 1.4),
                                 lang=lang)

    @staticmethod
    def _join(pieces: list[tuple[np.ndarray, int]]) -> tuple[np.ndarray, int]:
        rate = pieces[0][1]
        out = []
        for i, (samples, sr) in enumerate(pieces):
            if sr != rate:   # both voices produce 24 kHz today; resample only if that ever changes
                samples = np.interp(np.linspace(0, len(samples), int(len(samples) * rate / sr), endpoint=False),
                                    np.arange(len(samples)), samples).astype(np.float32)
            if i:
                out.append(np.zeros(int(rate * JOIN_PAUSE_S), dtype=np.float32))
            out.append(samples.astype(np.float32))
        return np.concatenate(out), rate

    def speak(self, text: str, voice: str, speed: float) -> bytes:
        return wav_bytes(*self.synthesize(text, voice, speed))


class Voice:
    """Both speech engines behind one object, so the server can swap in a fake for tests."""

    def __init__(self):
        from .assessment import PronunciationChecker
        self.stt = SpeechToText()
        self.tts = TextToSpeech()
        self.pron = PronunciationChecker()   # its model loads on the first check, and unloads when idle

    def status(self) -> dict:
        return {"stt_loaded": self.stt.loaded, "tts_loaded": self.tts.loaded, "tts_device": self.tts.device,
                "stt_model": self.stt.model_name, "kokoro_files": self.tts.model_path.exists(),
                "arabic_voice": self.tts.arabic.available(), "pronunciation": self.pron.status()}

    def warmup(self) -> dict:
        # A real round trip, not just loading: the first GPU run sets up kernels (measured: ~0.5 s
        # with a warm CUDA cache, ~11 s the very first time on this PC), so the learner never waits for it
        if not (self.tts.loaded and self.stt.loaded):
            clip = self.tts.speak("Ready.", "af_heart", 1.0)
            self.stt.transcribe(clip)
        try:
            self.tts.arabic.warmup()
        except httpx.HTTPError:
            pass   # the Arabic voice is optional; status() reports whether it is running
        return self.status()
