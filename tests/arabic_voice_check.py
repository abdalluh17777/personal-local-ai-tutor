"""Measures the Arabic voice: speed, and clarity judged by Whisper.

Each phrase is spoken by the Arabic voice worker (SILMA), then transcribed by Whisper and compared with the
original text (character error rate after normalizing hamza forms and removing diacritics). A mixed
English/Arabic sentence goes through the app's full path (Kokoro + SILMA). Samples are saved to
logs/voice-samples so they can be listened to.
Run with:  uv run python tests/arabic_voice_check.py   (the Arabic worker must be running)
"""

import json
import re
import subprocess
import time

from tutor import config
from tutor.voice import ArabicSpeech, SpeechToText, TextToSpeech, wav_bytes

PHRASES = [
    "إدراكي",
    "الانتباه والذاكرة العاملة",
    "الإشراط الكلاسيكي هو نوع من التعلم يربط بين مثيرين.",
    "هل تريد أن أشرح لك هذا المفهوم بالعربية؟",
    "الذاكرة قصيرة المدى تحتفظ بالمعلومات لثوانٍ قليلة فقط.",
]
MIXED = "The word cognitive means إدراكي, which is about thinking and memory."
OUT = config.ROOT / "logs" / "voice-samples"


def normalize_ar(text: str) -> str:
    text = re.sub(r"[\u064B-\u0652\u0670\u0640]", "", text)          # diacritics and tatweel
    text = re.sub(r"[إأآٱ]", "ا", text).replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[^\u0621-\u064A\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cer(ref: str, hyp: str) -> float:
    r, h = list(ref), list(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(len(r), 1)


def vram_mb() -> int:
    out = subprocess.run([r"C:\Windows\System32\nvidia-smi.exe", "--query-gpu=memory.used",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    arabic = ArabicSpeech()
    assert arabic.available(), "Start the Arabic worker first (scripts/start.ps1)"
    stt = SpeechToText()
    stt.load()

    results = []
    for i, text in enumerate(PHRASES, 1):
        t0 = time.perf_counter()
        samples, rate = arabic.speak(text, 1.0)
        took = time.perf_counter() - t0
        seconds = len(samples) / rate
        (OUT / f"arabic-{i}.wav").write_bytes(wav_bytes(samples, rate))
        heard = stt.transcribe_array(_to_16k(samples, rate))
        error = cer(normalize_ar(text), normalize_ar(heard["text"]))
        results.append({"said": text, "heard": heard["text"], "detected": heard["language"],
                        "audio_s": round(seconds, 2), "synth_s": round(took, 2),
                        "real_time_factor": round(took / seconds, 2), "cer": round(error, 3)})

    tts = TextToSpeech(arabic=arabic)
    t0 = time.perf_counter()
    samples, rate = tts.synthesize(MIXED, "af_heart", 1.0)
    mixed_s = time.perf_counter() - t0
    (OUT / "mixed-1.wav").write_bytes(wav_bytes(samples, rate))
    heard = stt.transcribe_array(_to_16k(samples, rate))

    summary = {
        "avg_cer": round(sum(r["cer"] for r in results) / len(results), 3),
        "avg_real_time_factor": round(sum(r["real_time_factor"] for r in results) / len(results), 2),
        "vram_used_total_mb": vram_mb(),
        "mixed": {"said": MIXED, "heard": heard["text"], "synth_s": round(mixed_s, 2),
                  "audio_s": round(len(samples) / rate, 2)},
    }
    print(json.dumps({"phrases": results, "summary": summary}, indent=2, ensure_ascii=False))
    (config.ROOT / "logs" / f"arabic-voice-check-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(
        json.dumps({"phrases": results, "summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSamples: {OUT}")


def _to_16k(samples, rate):
    import numpy as np
    n = int(len(samples) * 16000 / rate)
    return np.interp(np.linspace(0, len(samples), n, endpoint=False), np.arange(len(samples)), samples).astype("float32")


if __name__ == "__main__":
    main()
