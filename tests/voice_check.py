"""Measures the real speech engines on this PC.

Kokoro speaks test sentences (saved to logs/voice-samples so you can listen), then Whisper transcribes
that audio on the GPU. Reports load times, synthesis speed, transcription speed, word accuracy and memory.
Run with:  uv run python tests/voice_check.py
"""

import json
import re
import subprocess
import time
from pathlib import Path

import psutil

from tutor import config
from tutor.voice import SpeechToText, TextToSpeech

SENTENCES = [
    "Classical conditioning is a type of learning in which a neutral stimulus comes to trigger a response.",
    "Pavlov noticed that the dogs began to salivate before the food arrived.",
    "Negative reinforcement removes something unpleasant, which makes a behaviour more likely.",
    "Can you explain the difference between extinction and spontaneous recovery?",
    "Cognitive dissonance is the discomfort we feel when our actions do not match our beliefs.",
]
VOICES = ["af_heart", "bf_emma"]
OUT = config.ROOT / "logs" / "voice-samples"


def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower().replace("behavior", "behaviour"))


def word_error_rate(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1]))
    return d[len(r)][len(h)] / max(len(r), 1)


def vram_mb() -> int:
    out = subprocess.run([r"C:\Windows\System32\nvidia-smi.exe", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.strip().splitlines()[0])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    proc = psutil.Process()
    report = {}

    rss0 = proc.memory_info().rss
    tts = TextToSpeech()
    t0 = time.perf_counter()
    tts.load()
    report["kokoro_load_s"] = round(time.perf_counter() - t0, 2)
    report["kokoro_device"] = tts.device
    tts.speak("Warm up.", "af_heart", 1.0)   # first call compiles kernels; not counted

    clips = []
    synth_s = audio_s = 0.0
    for voice in VOICES:
        for i, text in enumerate(SENTENCES):
            t0 = time.perf_counter()
            wav = tts.speak(text, voice, 1.0)
            synth_s += time.perf_counter() - t0
            seconds = (len(wav) - 44) / 2 / 24000
            audio_s += seconds
            path = OUT / f"{voice}-{i + 1}.wav"
            path.write_bytes(wav)
            clips.append((voice, text, wav))
    report["kokoro"] = {"sentences": len(clips), "audio_seconds": round(audio_s, 1), "synthesis_seconds": round(synth_s, 1),
                        "real_time_factor": round(synth_s / audio_s, 3),
                        "first_sentence_ready_s": round(synth_s / len(clips), 2)}
    report["ram_after_kokoro_mb"] = round((proc.memory_info().rss - rss0) / 1e6)

    v0 = vram_mb()   # after Kokoro, so only Whisper's own memory is counted
    stt = SpeechToText()
    t0 = time.perf_counter()
    stt.load()
    report["whisper_load_s"] = round(time.perf_counter() - t0, 2)
    report["whisper_vram_mb"] = vram_mb() - v0
    stt.transcribe(clips[0][2])   # warm-up

    results, total_t, total_wer = [], 0.0, 0.0
    for voice, text, wav in clips:
        t0 = time.perf_counter()
        r = stt.transcribe(wav, hint="Intro Psychology. Learning")
        elapsed = time.perf_counter() - t0
        wer = word_error_rate(text, r["text"])
        total_t += elapsed
        total_wer += wer
        results.append({"voice": voice, "said": text, "heard": r["text"], "language": r["language"],
                        "seconds": round(elapsed, 2), "wer": round(wer, 3)})
    report["whisper"] = {"clips": len(results), "avg_seconds_per_clip": round(total_t / len(results), 2),
                         "avg_word_error_rate": round(total_wer / len(results), 3)}
    report["ram_total_mb"] = round((proc.memory_info().rss - rss0) / 1e6)

    print(json.dumps(report, indent=2))
    for r in results:
        if r["wer"] > 0:
            print(f"\n[{r['voice']}] said:  {r['said']}\n          heard: {r['heard']}  (WER {r['wer']})")
    out = config.ROOT / "logs" / f"voice-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"report": report, "results": results}, indent=2), encoding="utf-8")
    print(f"\nSamples: {OUT}\nSaved: {out}")


if __name__ == "__main__":
    main()
