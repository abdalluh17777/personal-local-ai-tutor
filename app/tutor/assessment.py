"""Pronunciation check: how well the learner said a known sentence, sound by sound.

A wav2vec2 phone recogniser (ONNX, float16) gives the probability of every phone in every 20 ms of the recording,
on the GPU. The sentence's pronunciation comes from the CMU dictionary (eSpeak NG for words it lacks). For each
expected sound, the CTC likelihood of the whole sentence with that sound is compared with the same sentence with
any other sound, or no sound, in its place, the rest held fixed (an alignment-free Goodness of Pronunciation).
That gives how sure the model is that the expected sound was said, and what was most likely said instead.
Accepted variants count as the expected sound: the flap in "water", the merged vowel of "caught", a reduced
vowel in an unstressed syllable.

GPU only, and only while it is used: the model loads on the first check when there is enough free GPU memory,
is unloaded after IDLE_UNLOAD_S unused, and never falls back to the CPU and system RAM. The alignment arithmetic
(a few hundred small sequences per sentence) runs in numpy.
"""

import io
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pronouncing

from . import config
from .voice import VoiceError, _add_cuda_dll_dirs

SAMPLE_RATE = 16000
MAX_SECONDS = 20          # GPU memory grows with the recording (measured: +1.2 GB at 15 s, +1.9 GB at 30 s)
IDLE_UNLOAD_S = 600
# Free GPU memory needed beyond the model file before loading (measured: a 15 s recording needs about 1.2 GB in
# all, a 20 s one about 1.45 GB)
GPU_MARGIN_MB = 1000
VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
CONSONANTS = {"B", "CH", "D", "DH", "F", "G", "HH", "JH", "K", "L", "M", "N", "NG", "P", "R", "S", "SH", "T",
              "TH", "V", "W", "Y", "Z", "ZH"}
_NEG = -1e30
# A word's last consonant and the next word's first one said as one sound ("this city", "need to", "big car")
MERGES = {"T": {"T", "D"}, "D": {"T", "D"}, "P": {"P", "B"}, "B": {"P", "B"}, "K": {"K", "G"}, "G": {"K", "G"},
          "S": {"S", "Z", "SH"}, "Z": {"Z", "S", "SH"}}
ENDINGS = {"T", "D", "S", "Z"}   # the sounds of the -ed and -s endings
PAD_SECONDS = 0.3         # silence added at both ends: a sound at the very edge of a recording is misjudged
# A recording whose loudest 20 ms is quieter than this (about -45 dBFS) has no speech: quiet speech with the
# browser's automatic gain is far above it, microphone noise far below
SPEECH_RMS = 0.006
CONTEXT = 2               # sounds on each side of the one being judged, in its window


# --------------------------------------------------------------------------- CTC
def ctc_loglik(logp: np.ndarray, seqs: list[list[int]], blank: int) -> np.ndarray:
    """log P(sequence | audio) of each label sequence, summed over all CTC alignments (logp: frames x vocab)."""
    T, B = logp.shape[0], len(seqs)
    L = max(1, max(len(s) for s in seqs))
    S = 2 * L + 1
    ext = np.full((B, S), blank, dtype=np.int64)
    lengths = np.array([len(s) for s in seqs])
    for b, s in enumerate(seqs):
        ext[b, 1:2 * len(s):2] = s
    skip = np.zeros((B, S), dtype=bool)          # a label can follow the one two states back unless they are equal
    skip[:, 2:] = (ext[:, 2:] != blank) & (ext[:, 2:] != ext[:, :-2])
    alpha = np.full((B, S), _NEG)
    alpha[:, 0] = logp[0, blank]
    has = lengths > 0
    alpha[has, 1] = logp[0, ext[has, 1]]
    pad1, pad2 = np.full((B, 1), _NEG), np.full((B, 2), _NEG)
    for t in range(1, T):
        a1 = np.concatenate([pad1, alpha[:, :-1]], axis=1)
        a2 = np.where(skip, np.concatenate([pad2, alpha[:, :-2]], axis=1), _NEG)
        m = np.maximum(np.maximum(alpha, a1), a2)
        alpha = m + np.log(np.exp(alpha - m) + np.exp(a1 - m) + np.exp(a2 - m)) + logp[t][ext]
    rows = np.arange(B)
    end1 = alpha[rows, 2 * lengths]
    end2 = np.where(has, alpha[rows, np.maximum(2 * lengths - 1, 0)], _NEG)
    m = np.maximum(end1, end2)
    return m + np.log(np.exp(end1 - m) + np.exp(end2 - m))


def ctc_align(logp: np.ndarray, seq: list[int], blank: int) -> np.ndarray | None:
    """The first and last frame of each label of seq on the most likely CTC path (Viterbi), or None when the
    recording is too short for the sequence."""
    T, L = logp.shape[0], len(seq)
    S = 2 * L + 1
    ext = np.full(S, blank, dtype=np.int64)
    ext[1::2] = seq
    skip = np.zeros(S, dtype=bool)
    skip[2:] = (ext[2:] != blank) & (ext[2:] != ext[:-2])
    dp = np.full(S, _NEG)
    dp[0] = logp[0, blank]
    if L:
        dp[1] = logp[0, ext[1]]
    back = np.zeros((T, S), dtype=np.int8)
    cols = np.arange(S)
    for t in range(1, T):
        cand = np.stack([dp, np.concatenate([[_NEG], dp[:-1]]),
                         np.where(skip, np.concatenate([[_NEG, _NEG], dp[:-2]]), _NEG)])
        k = cand.argmax(0)
        dp = cand[k, cols] + logp[t, ext]
        back[t] = k
    s = S - 1 if L == 0 or dp[S - 1] >= dp[S - 2] else S - 2
    if dp[s] < -1e29:
        return None
    spans = np.full((L, 2), -1, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        if s % 2:
            j = s // 2
            if spans[j, 1] < 0:
                spans[j, 1] = t
            spans[j, 0] = t
        s -= int(back[t, s])
    return spans


def _logsumexp(values) -> float:
    v = np.asarray(values, dtype=np.float64)
    m = v.max()
    return float(m + np.log(np.exp(v - m).sum()))


# --------------------------------------------------------------------------- pronunciations
_ESPEAK = {"aɪ": "AY", "aʊ": "AW", "eɪ": "EY", "oʊ": "OW", "ɔɪ": "OY", "ɑː": "AA", "ɑ": "AA", "ɔː": "AO",
           "ɔ": "AO", "ɜː": "ER", "ɜ": "ER", "ɚ": "ER", "ə": "AH", "ɐ": "AH", "ʌ": "AH", "æ": "AE", "ɛ": "EH",
           "ɪ": "IH", "ᵻ": "IH", "iː": "IY", "i": "IY", "ʊ": "UH", "uː": "UW", "u": "UW", "tʃ": "CH", "dʒ": "JH",
           "ŋ": "NG", "θ": "TH", "ð": "DH", "ʃ": "SH", "ʒ": "ZH", "ɹ": "R", "ɾ": "T", "ɡ": "G", "j": "Y",
           "h": "HH", "ʔ": "T", "b": "B", "d": "D", "f": "F", "k": "K", "l": "L", "m": "M", "n": "N", "p": "P",
           "s": "S", "t": "T", "v": "V", "w": "W", "z": "Z"}
_ESPEAK_KEYS = sorted(_ESPEAK, key=len, reverse=True)
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[0-9]+")   # numbers are read by eSpeak NG ("3": "three")


def _espeak(word: str) -> list[tuple[str, str]] | None:
    """A pronunciation from eSpeak NG for a word the CMU dictionary lacks: (phone, stress) pairs."""
    try:
        out = subprocess.run([config.ESPEAK, "-q", "--ipa", "-v", "en-us", word], capture_output=True,
                             text=True, encoding="utf-8", timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    phones, stress, i = [], "0", 0
    while i < len(out):
        ch = out[i]
        if ch in "ˈˌ":
            stress = "1" if ch == "ˈ" else "2"
            i += 1
            continue
        key = next((k for k in _ESPEAK_KEYS if out.startswith(k, i)), None)
        if key is None:
            i += 1                          # length marks, syllabic marks, spaces
            continue
        p = _ESPEAK[key]
        if p in VOWELS:
            phones.append((p, stress))
            stress = "0"
        else:
            phones.append((p, ""))
        i += len(key)
    return phones or None


# Weak forms of grammar words in connected speech ("for" /fər/, "and" /ən/), which the dictionary does not list;
# every vowel of these words may also be reduced
WEAK_FORMS = {
    "a": ["AH0"], "an": ["AH0 N"], "and": ["AH0 N D", "AH0 N", "N"], "are": ["ER0", "AA0"], "as": ["AH0 Z"],
    "at": ["AH0 T"], "be": ["B IH0"], "been": ["B IH0 N"], "but": ["B AH0 T"], "can": ["K AH0 N"],
    "could": ["K AH0 D"], "do": ["D AH0"], "does": ["D AH0 Z"], "for": ["F ER0", "F AH0"], "from": ["F R AH0 M"],
    "had": ["HH AH0 D", "AH0 D"], "has": ["HH AH0 Z", "AH0 Z"], "have": ["HH AH0 V", "AH0 V"],
    "he": ["IY0", "HH IH0"], "her": ["ER0", "HH AH0"], "him": ["IH0 M"], "his": ["IH0 Z"],
    "just": ["JH AH0 S T", "JH AH0 S"], "me": ["M IH0"], "must": ["M AH0 S T", "M AH0 S"],
    "of": ["AH0 V", "AH0"], "or": ["ER0", "AH0"], "she": ["SH IH0"], "should": ["SH AH0 D"], "some": ["S AH0 M"],
    "than": ["DH AH0 N"], "that": ["DH AH0 T"], "the": ["DH AH0", "DH IY0"], "them": ["DH AH0 M", "AH0 M"],
    "there": ["DH ER0", "DH AH0"], "to": ["T AH0", "T IH0", "T UH0"], "us": ["AH0 S"], "was": ["W AH0 Z"],
    "we": ["W IH0"], "were": ["W ER0", "W AH0"], "will": ["W AH0 L", "AH0 L"], "would": ["W AH0 D", "AH0 D"],
    "you": ["Y AH0", "Y UH0"], "your": ["Y ER0", "Y AH0"],
}
REDUCIBLE = set(WEAK_FORMS) | {"in", "it", "is", "on", "if", "into", "onto", "upon"}


def _pairs(entry: str) -> list[tuple[str, str]]:
    # some dictionary entries end with a comment ("... # foreign")
    phones = entry.split("#")[0].split()
    return [(p.rstrip("012"), p[-1] if p[-1].isdigit() else "") for p in phones if p.rstrip("012") in VOWELS | CONSONANTS]


def pronunciations(word: str) -> list[list[tuple[str, str]]]:
    """Every dictionary pronunciation of a word as (phone, stress) pairs; stress "" for consonants."""
    w = word.lower().replace("’", "'")
    entries = list(dict.fromkeys(pronouncing.phones_for_word(w) + WEAK_FORMS.get(w, [])))
    out = [_pairs(e) for e in entries]
    if not out:
        guess = _espeak(word)
        if guess:
            out.append(guess)
    if w in REDUCIBLE:
        out = [[(p, "0" if p in VOWELS else s) for p, s in pron] for pron in out]
    return out


def expected_ipa(text: str) -> list[dict]:
    """The dictionary pronunciation of each word, in the course's symbols, to show before recording."""
    from .pronunciation import ipa
    out = []
    for w in _WORD.findall(text)[:40]:
        prons = pronunciations(w)
        out.append({"text": w, "ipa": ipa([p + s for p, s in prons[0]]) if prons else None})
    return out


# --------------------------------------------------------------------------- the model's phone symbols
@dataclass
class PhoneSet:
    """How the model writes each phone: accepted forms per phone (the first is the standard one), the forms an
    unstressed vowel may take, and the separator between words."""
    forms: dict[str, list[str]]
    unstressed: list[str]
    plain_er: list[str]           # /ɜː/ and /ə/ without r-colour (British English)
    flap: list[str]               # the American t in "water"
    separator: str | None
    char_tokens: bool             # the model's vocabulary is single characters (forms are spelled out)


PHONE_SETS = {
    # Fine-tuned on the L2-ARCTIC learner corpus (mrrubino/wav2vec2-large-xlsr-53-l2-arctic-phoneme)
    "l2arctic": PhoneSet(
        forms={"AA": ["ɑ"], "AE": ["æ"], "AH": ["ʌ"], "AO": ["ɔ", "ɑ"], "AW": ["aʊ"], "AY": ["aɪ"], "EH": ["ɛ"],
               "ER": ["ɚ"], "EY": ["eɪ"], "IH": ["ɪ"], "IY": ["i"], "OW": ["oʊ"], "OY": ["ɔɪ"], "UH": ["ʊ"],
               "UW": ["u"], "B": ["b"], "CH": ["t͡ʃ"], "D": ["d"], "DH": ["ð"], "F": ["f"], "G": ["ɡ"],
               "HH": ["h"], "JH": ["d͡ʒ"], "K": ["k"], "L": ["l"], "M": ["m"], "N": ["n"], "NG": ["ŋ"], "P": ["p"],
               "R": ["ɹ"], "S": ["s"], "SH": ["ʃ"], "T": ["t"], "TH": ["θ"], "V": ["v"], "W": ["w"], "Y": ["j"],
               "Z": ["z"], "ZH": ["ʒ"]},
        unstressed=["ʌ", "ɪ"], plain_er=["ʌ"], flap=["d"], separator=" ", char_tokens=True),
    # Meta's multilingual phone recogniser, fine-tuned on Common Voice (facebook/wav2vec2-lv-60-espeak-cv-ft)
    "lv60": PhoneSet(
        forms={"AA": ["ɑː", "ɑ"], "AE": ["æ"], "AH": ["ʌ", "ə", "ɐ"], "AO": ["ɔː", "ɔ", "ɑː"], "AW": ["aʊ"],
               "AY": ["aɪ"], "EH": ["ɛ"], "ER": ["ɜː", "ɚ", "ɜ"], "EY": ["eɪ", "e"], "IH": ["ɪ", "ᵻ"],
               "IY": ["iː", "i"], "OW": ["oʊ", "o"], "OY": ["ɔɪ"], "UH": ["ʊ"], "UW": ["uː", "u"], "B": ["b"],
               "CH": ["tʃ"], "D": ["d", "ɾ"], "DH": ["ð"], "F": ["f"], "G": ["ɡ"], "HH": ["h"], "JH": ["dʒ"],
               "K": ["k"], "L": ["l"], "M": ["m"], "N": ["n"], "NG": ["ŋ"], "P": ["p"], "R": ["ɹ"], "S": ["s"],
               "SH": ["ʃ"], "T": ["t"], "TH": ["θ"], "V": ["v"], "W": ["w"], "Y": ["j"], "Z": ["z"],
               "ZH": ["ʒ"]},
        unstressed=["ə", "ɪ", "ᵻ", "ʌ", "ɐ"], plain_er=["ə"], flap=["ɾ", "d"], separator=None, char_tokens=False),
}

# Decision thresholds on the log posterior of the expected sound. Set on speechocean762 (2,500 sentences by
# non-native speakers, every sound scored by five experts; thresholds chosen on half of the speakers, reported on
# the other half: 1,240 held-out sentences). At -8: 2.0% of the sounds all experts scored correct are flagged, and
# 49% of the clear errors (scored 0.8 or less out of 2) are found; at -6: 3.8% and 62%. Many of the remaining
# "false alarms" are /ð/ said as /z/ or /θ/ as /s/, which those experts accepted from Chinese speakers and a teacher
# of Arabic speakers would not.
WRONG_BELOW = -8.0        # the expected sound was not said
UNSURE_BELOW = -6.0       # probably not quite right: worth checking
# In a drill of one contrast ("ship" / "sheep"), the question is only which of the two sounds was said, which is
# far more reliable than an open question: a lower threshold applies to that substitution
FOCUS_BELOW = -3.0
ADDED_ABOVE = 4.0         # an added sound is this much (log likelihood) more likely than none


# --------------------------------------------------------------------------- the model on the GPU
def free_gpu_mb() -> float | None:
    try:
        import pynvml
        pynvml.nvmlInit()
        info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
        return info.free / 2**20
    except Exception:
        return None


class PhoneModel:
    """The phone recogniser: ONNX Runtime on CUDA only, loaded on first use and unloaded when idle."""

    def __init__(self, model_dir: Path = config.PRON_MODEL_DIR, idle_unload_s: float = IDLE_UNLOAD_S):
        self.dir = Path(model_dir)
        self.file = self.dir / "model_fp16.onnx"
        self.idle_unload_s = idle_unload_s
        self.phones = PHONE_SETS.get(self.dir.name)
        self._session = None
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._last_used = 0.0
        self.vocab: dict[str, int] = {}
        self.blank = 0
        self.unloads = 0

    @property
    def available(self) -> bool:
        return self.file.exists() and self.phones is not None

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self):
        with self._lock:
            if self._session is not None:
                return self._session
            if not self.available:
                raise VoiceError(f"The pronunciation model is missing in {self.dir}")
            need = self.file.stat().st_size / 2**20 + GPU_MARGIN_MB
            free = free_gpu_mb()
            if free is not None and free < need:
                raise VoiceError(f"Not enough free GPU memory for the pronunciation check: it needs about "
                                 f"{need / 1024:.1f} GB and {free / 1024:.1f} GB is free. Close other programs that "
                                 f"use the graphics card and try again.")
            self.vocab = json.loads((self.dir / "vocab.json").read_text(encoding="utf-8"))
            self.blank = json.loads((self.dir / "config.json").read_text(encoding="utf-8"))["pad_token_id"]
            _add_cuda_dll_dirs()
            import onnxruntime as ort

            ort.preload_dlls()
            options = ort.SessionOptions()
            options.log_severity_level = 3
            options.enable_cpu_mem_arena = False
            options.add_session_config_entry("session.use_device_allocator_for_initializers", "1")
            cuda = ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested",
                                              "cudnn_conv_algo_search": "HEURISTIC"})
            try:
                session = ort.InferenceSession(str(self.file), sess_options=options, providers=[cuda])
                session.disable_fallback()
            except Exception as e:
                raise VoiceError(f"The pronunciation model could not start on the GPU: {e}") from e
            if "CUDAExecutionProvider" not in session.get_providers():
                raise VoiceError("The pronunciation model could not start on the GPU (CUDA is not available)")
            self._session = session
            self._run_options = ort.RunOptions()
            # Return the memory a long recording needed as soon as it is done (measured: 0.75 GB stays loaded)
            self._run_options.add_run_config_entry("memory.enable_memory_arena_shrinkage", "gpu:0")
            self._last_used = time.monotonic()
            threading.Thread(target=self._unload_when_idle, daemon=True).start()
            return session

    def _unload_when_idle(self) -> None:
        while self._session is not None:
            time.sleep(min(30.0, self.idle_unload_s / 4))
            if time.monotonic() - self._last_used > self.idle_unload_s:
                self.unload()

    def unload(self) -> None:
        with self._lock, self._run_lock:
            if self._session is not None:
                self._session = None
                self.unloads += 1
                import gc
                gc.collect()

    def log_probs(self, samples: np.ndarray) -> np.ndarray:
        """Frame-level log probabilities (about 50 frames a second) of 16 kHz mono samples."""
        session = self.load()
        x = (samples - samples.mean()) / np.sqrt(samples.var() + 1e-7)
        with self._run_lock:
            try:
                logits = session.run(None, {"input_values": x[None, :].astype(np.float32)}, self._run_options)[0][0]
            except Exception as e:   # out of GPU memory, or a CUDA error: never retried on the CPU
                raise VoiceError(f"The pronunciation check failed on the GPU: {e}") from e
        self._last_used = time.monotonic()
        logits = logits.astype(np.float32)
        m = logits.max(1, keepdims=True)
        return logits - m - np.log(np.exp(logits - m).sum(1, keepdims=True))

    def tokens(self, form: str) -> list[int]:
        return [self.vocab[c] for c in form] if self.phones.char_tokens else [self.vocab[form]]

    def status(self) -> dict:
        return {"available": self.available, "loaded": self.loaded, "model": self.dir.name}


# --------------------------------------------------------------------------- scoring
def _ipa(phone: str, stress: str) -> str:
    from .pronunciation import IPA
    if phone == "AH":
        return "ə" if stress == "0" else "ʌ"
    if phone == "ER":
        return "ər" if stress == "0" else "ɜr"
    return IPA[phone]


def trim_silence(samples: np.ndarray, pad_s: float = 0.25) -> np.ndarray:
    """The recording without long silence at either end (20 ms frames, relative to the loudest part); nothing at
    all when even the loudest part is below SPEECH_RMS."""
    frame = SAMPLE_RATE // 50
    n = len(samples) // frame
    if n < 3:
        return samples
    energy = np.sqrt((samples[:n * frame].reshape(n, frame) ** 2).mean(1))
    if energy.max() < SPEECH_RMS:
        return samples[:0]
    loud = np.nonzero(energy > max(energy.max() * 0.05, 1e-4))[0]
    if not len(loud):
        return samples[:0]
    pad = int(pad_s * 50)
    start, end = max(loud[0] - pad, 0), min(loud[-1] + pad + 1, n)
    return samples[start * frame:end * frame]


class PronunciationChecker:
    def __init__(self, model: PhoneModel | None = None):
        self.model = model or PhoneModel()

    def status(self) -> dict:
        return self.model.status()

    def check(self, audio: bytes, text: str, focus: set[str] = frozenset()) -> dict:
        from faster_whisper import decode_audio
        try:
            samples = decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE)
        except Exception as e:   # a broken upload is the request's fault, not the model's
            raise ValueError(f"Could not read the recording: {e}") from e
        return self.check_array(samples, text, focus)

    def check_array(self, samples: np.ndarray, text: str, focus: set[str] = frozenset()) -> dict:
        words = [w for w in _WORD.findall(text)]
        if not words:
            raise ValueError("Type the word or sentence you want to practise.")
        if len(words) > 40:
            raise ValueError("Practise one sentence at a time (up to 40 words).")
        samples = trim_silence(samples.astype(np.float32))
        duration = len(samples) / SAMPLE_RATE
        if duration < 0.25:
            raise ValueError("I could not hear any speech. Check the microphone and try again.")
        if duration > MAX_SECONDS:
            raise ValueError(f"The recording is too long ({duration:.0f} s). Record one sentence, up to "
                             f"{MAX_SECONDS} seconds.")
        prons = [pronunciations(w) for w in words]
        missing = [w for w, p in zip(words, prons) if not p]
        if missing:
            raise ValueError(f"I do not know how to pronounce: {', '.join(missing)}")
        pad = np.zeros(int(PAD_SECONDS * SAMPLE_RATE), dtype=np.float32)
        logp = self.model.log_probs(np.concatenate([pad, samples, pad]))
        result = score(logp, words, prons, self.model, set(focus))
        result["duration"] = round(duration, 1)
        result["text"] = text
        return result


def score(logp: np.ndarray, words: list[str], prons: list[list[list[tuple[str, str]]]], model: PhoneModel,
          focus: set[str] = frozenset()) -> dict:
    """Judge every sound of the sentence. focus: the sounds a drill contrasts (for example P and B); a swap between
    them is judged more strictly. None in focus: a word-final T, D, S or Z of focus left out is judged so too.

    Each sound is judged in a window: the sound with CONTEXT sounds on each side, over the frames where the best
    alignment of the whole sentence puts them. Measured: whole-sentence likelihoods for every alternative took 30 s
    for a 33-word sentence; windows make the work grow with the length of the sentence, not its square.
    """
    ps = model.phones
    sep = model.tokens(ps.separator) if ps.separator else []
    first_form = {p: model.tokens(f[0]) for p, f in ps.forms.items()}
    T = logp.shape[0]

    def forms_of(pron: list[tuple[str, str]], pi: int, next_word: list[tuple[str, str]] | None = None) -> list[list[int]]:
        """The accepted ways to say sound pi of a word, the standard one first."""
        phone, stress = pron[pi]
        prev = pron[pi - 1][0] if pi else None
        nxt, nxt_stress = pron[pi + 1] if pi + 1 < len(pron) else (None, "")
        forms = list(ps.forms[phone])
        if phone in VOWELS and stress == "0" and phone != "ER":
            forms += ps.unstressed
        if phone == "ER":                       # British English: no r-colour ("bird" /bɜːd/, "better" /betə/)
            forms += ps.plain_er
        if phone == "R" and prev in VOWELS and nxt not in VOWELS:
            forms.append("")                    # British English: no r after a vowel ("car", "card")
        if phone == "T" and (prev in VOWELS or prev == "R") and nxt in VOWELS and nxt_stress in ("0", "2"):
            forms += ps.flap                    # American English: "water", "better", "city", "photo" (OW2)
        if nxt is None and next_word and phone in CONSONANTS and next_word[0][0] in MERGES.get(phone, {phone}):
            forms.append("")                    # one long sound across the words: "this city", "need to"
        out = []
        for f in dict.fromkeys(forms):
            toks = model.tokens(f) if f else []
            if toks not in out:
                out.append(toks)
        return out

    current = [p[0] for p in prons]

    def layout():
        """Every sound of the sentence in order, with the accepted forms of each."""
        slots = [(wi, pi) for wi, pron in enumerate(current) for pi in range(len(pron))]
        forms = {(wi, pi): forms_of(pron, pi, current[wi + 1] if wi + 1 < len(current) else None)
                 for wi, pron in enumerate(current) for pi in range(len(pron))}
        return slots, forms

    def sequence(slots, chosen, a, b, inserted=None):
        """The labels of sounds a..b (slot indexes), with word separators inside the window."""
        out = []
        for j in range(a, b + 1):
            wi, pi = slots[j]
            if j > a and pi == 0:
                out += sep
            if inserted:
                out += inserted.get((wi, pi), [])
            out += chosen[(wi, pi)]
            if inserted and pi == len(current[wi]) - 1:
                out += inserted.get((wi, pi + 1), [])
        return out

    def frame_spans(slots, chosen):
        """First and last+1 frame of each sound on the best path of the whole sentence."""
        seq, owner = [], []
        for j, (wi, pi) in enumerate(slots):
            if j and pi == 0:
                seq += sep
                owner += [None] * len(sep)
            seq += chosen[(wi, pi)]
            owner += [j] * len(chosen[(wi, pi)])
        spans = ctc_align(logp, seq, model.blank)
        if spans is None:
            raise ValueError("The recording is too short for this text. Say the whole sentence.")
        out = [[None, None] for _ in slots]
        for (f0, f1), j in zip(spans, owner):
            if j is not None:
                out[j][0] = f0 if out[j][0] is None else min(out[j][0], f0)
                out[j][1] = f1 + 1 if out[j][1] is None else max(out[j][1], f1 + 1)
        last = 0
        for s in out:                           # a sound said as nothing: an empty span where it would be
            if s[0] is None:
                s[0] = s[1] = last
            last = s[1]
        return out

    def window(slots, spans, a, b, context, margin):
        a, b = max(0, a - context), min(len(slots) - 1, b + context)
        f0 = 0 if a == 0 else max(0, spans[a][0] - margin)
        f1 = T if b == len(slots) - 1 else min(T, spans[b][1] + margin)
        return a, b, f0, f1

    def local_ll(slots, spans, a, b, build) -> tuple[np.ndarray, int, int]:
        """Likelihoods of the sequences build(a, b) over a window around sounds a..b, widened if too tight."""
        for context, margin in ((CONTEXT, 2), (CONTEXT + 2, 8), (len(slots), T)):
            wa, wb, f0, f1 = window(slots, spans, a, b, context, margin)
            seqs = build(wa, wb)
            ll = ctc_loglik(logp[f0:f1], seqs, model.blank)
            if ll[0] > -1e29:
                return ll, wa, wb
        raise ValueError("The recording is too short for this text. Say the whole sentence.")

    # 1. the dictionary pronunciation of each word that fits the recording best
    slots, forms = layout()
    chosen = {k: f[0] for k, f in forms.items()}
    spans = frame_spans(slots, chosen)
    changed = False
    for wi, options in enumerate(prons):
        if len(options) < 2:
            continue
        idx = [j for j, (w, _) in enumerate(slots) if w == wi]
        next_word = current[wi + 1] if wi + 1 < len(current) else None

        def build(wa, wb, idx=idx, options=options, next_word=next_word):
            left = sequence(slots, chosen, wa, idx[0] - 1) if wa < idx[0] else []
            right = sequence(slots, chosen, idx[-1] + 1, wb) if wb > idx[-1] else []
            out = []
            for pron in options:
                word = [t for pi in range(len(pron)) for t in forms_of(pron, pi, next_word)[0]]
                out.append(left + (sep if left else []) + word + (sep if right else []) + right)
            return out

        ll, _, _ = local_ll(slots, spans, idx[0], idx[-1], build)
        best = int(np.argmax(ll))
        if best:
            current[wi] = options[best]
            changed = True
    if changed:
        slots, forms = layout()
        chosen = {k: f[0] for k, f in forms.items()}
        spans = frame_spans(slots, chosen)

    # 2. the accepted form of each sound that fits best
    for j, k in enumerate(slots):
        if len(forms[k]) > 1:
            ll, _, _ = local_ll(slots, spans, j, j, lambda wa, wb, k=k: [
                sequence(slots, {**chosen, k: f}, wa, wb) for f in forms[k]])
            chosen[k] = forms[k][int(np.argmax(ll))]
    spans = frame_spans(slots, chosen)

    # 3. each sound against every other sound and no sound, and added sounds where learners add them
    vowel_forms = [("AH", model.tokens(ps.unstressed[0])), ("IH", first_form["IH"])]
    additions = {}                               # slot index -> [(insert position, phone, labels)]
    for wi, pron in enumerate(current):
        phones = [p for p, _ in pron]
        base_j = next(j for j, k in enumerate(slots) if k == (wi, 0))
        # a vowel between two consonants ("sitreet", "ask-ed"), or before an s-group ("istreet")
        places = [pi + 1 for pi in range(len(phones) - 1)
                  if phones[pi] in CONSONANTS and phones[pi + 1] in CONSONANTS and phones[pi + 1] not in ("Y", "W")]
        if len(phones) > 1 and phones[0] == "S" and phones[1] in CONSONANTS:
            places.append(0)
        # or before an -ed or -s ending after a vowel ("play-id", "play-iz")
        if len(phones) > 1 and phones[-1] in ENDINGS and phones[-2] in VOWELS:
            places.append(len(phones) - 1)
        for at in places:
            additions.setdefault(base_j + at, []).extend((at, q, f) for q, f in vowel_forms)
        if phones and phones[-1] == "NG":         # "sing-g"
            additions.setdefault(base_j + len(phones) - 1, []).append((len(phones), "G", first_form["G"]))

    own_ll, alt_ll, added = {}, {}, {}
    for j, k in enumerate(slots):
        wi, pi = k
        own = {tuple(f) for f in forms[k]}
        alts = [(q, [] if q is None else first_form[q]) for q in list(ps.forms) + [None]
                if q != current[wi][pi][0] and tuple([] if q is None else first_form[q]) not in own]
        adds = additions.get(j, [])

        def build(wa, wb, k=k, alts=alts, adds=adds, wi=wi):
            return ([sequence(slots, chosen, wa, wb)] +
                    [sequence(slots, {**chosen, k: f}, wa, wb) for f in forms[k]] +
                    [sequence(slots, {**chosen, k: f}, wa, wb) for _, f in alts] +
                    [sequence(slots, chosen, wa, wb, {(wi, at): f}) for at, _, f in adds])

        ll, _, _ = local_ll(slots, spans, j, j, build)
        base = ll[0]
        n_own = len(forms[k])
        own_ll[k] = list(ll[1:1 + n_own])
        alt_ll[k] = [(q, x) for (q, _), x in zip(alts, ll[1 + n_own:1 + n_own + len(alts)])]
        for (at, q, _), x in zip(adds, ll[1 + n_own + len(alts):]):
            gain = x - base
            if gain > ADDED_ABOVE and gain > added.get((wi, at), (None, -np.inf))[1]:
                added[(wi, at)] = (q, gain)

    from .drills import advice
    out_words, issues = [], []
    for wi, pron in enumerate(current):
        phones_out = []
        for pi, (ph, st) in enumerate(pron):
            own = _logsumexp(own_ll[(wi, pi)])
            alts = alt_ll[(wi, pi)]
            gop = own - _logsumexp([own] + [x for _, x in alts])
            q, _ = max(alts, key=lambda a: a[1])
            status = "good"
            if gop < UNSURE_BELOW:
                status = "wrong" if gop < WRONG_BELOW else "unsure"
            # None in focus: a word-final T, D, S or Z left out ("walk(ed)", "book(s)") is the drill's error too
            swap = q in focus and (q is not None or (pi == len(pron) - 1 and ph in ENDINGS))
            if ph in focus and swap and gop < FOCUS_BELOW:
                status = "wrong"
            # a weak vowel can disappear in natural speech ("fam(i)ly"): not counted as an error, unless it is
            # the whole word ("a")
            if status != "good" and q is None and ph in VOWELS and st == "0" and len(pron) > 1:
                status = "good"
            item = {"phone": ph, "ipa": _ipa(ph, st), "status": status, "confidence": round(float(np.exp(gop)), 3)}
            if status != "good":
                item["heard"] = q
                item["heard_ipa"] = _ipa(q, "1") if q else None
                item["tip"] = advice(ph, q)
                issues.append({"word": words[wi], "word_index": wi, "phone": ph, "ipa": item["ipa"], "heard": q,
                               "heard_ipa": item["heard_ipa"], "status": status, "tip": item["tip"],
                               "confidence": item["confidence"]})
            phones_out.append(item)
        word_added = []
        for (awi, api), (q, _gain) in sorted(added.items()):
            if awi == wi:
                word_added.append({"before": api, "heard": q, "heard_ipa": _ipa(q, "0"), "tip": advice(None, q)})
                issues.append({"word": words[wi], "word_index": wi, "phone": None, "ipa": None, "heard": q,
                               "heard_ipa": _ipa(q, "0"), "status": "wrong", "tip": advice(None, q),
                               "confidence": 0.0})
        conf = [p["confidence"] for p in phones_out]
        wscore = round(100 * float(np.mean(conf))) if conf else 0
        if word_added:
            wscore = min(wscore, 60)
        status = ("wrong" if any(p["status"] == "wrong" for p in phones_out) or word_added else
                  "unsure" if any(p["status"] == "unsure" for p in phones_out) else "good")
        out_words.append({"text": words[wi], "phones": phones_out, "added": word_added, "score": wscore,
                          "status": status})
    issues.sort(key=lambda i: (i["status"] != "wrong", i["confidence"]))
    n = sum(len(w["phones"]) for w in out_words)
    overall = round(sum(w["score"] * len(w["phones"]) for w in out_words) / max(n, 1))
    return {"score": overall, "words": out_words, "issues": issues}
