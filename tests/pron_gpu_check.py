"""The pronunciation check next to everything else on the GPU: the tutor model (Ollama), Whisper and Kokoro.

    uv run python tests/pron_gpu_check.py <synthetic folder from pron_synth.py>

Starts a second server on port 8799 with throwaway data (the learner's statistics are not touched), loads the
tutor model and the voice, then checks recordings and reports GPU memory (NVML), whether the tutor model is
still entirely on the GPU, and the tutor model's speed before and after. Ollama must be running.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import pynvml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from tutor import config  # noqa: E402
from tutor.voice import wav_bytes  # noqa: E402

PORT = 8799
pynvml.nvmlInit()
GPU = pynvml.nvmlDeviceGetHandleByIndex(0)


def gpu(label: str) -> float:
    m = pynvml.nvmlDeviceGetMemoryInfo(GPU)
    models = httpx.get(f"{config.OLLAMA_URL}/api/ps", timeout=10).json().get("models", [])
    placed = ", ".join(f"{x['name']} {100 * x['size_vram'] / x['size']:.0f}% on GPU" for x in models) or "no model loaded"
    print(f"{label:36} GPU used {m.used / 2**30:5.2f} GB, free {m.free / 2**30:5.2f} GB | {placed}")
    return m.used / 2**30


def tutor_speed() -> float:
    r = httpx.post(f"{config.OLLAMA_URL}/api/generate", timeout=300, json={
        "model": config.MODEL, "prompt": "Explain operant conditioning in three sentences.", "stream": False,
        "think": False, "options": {"num_predict": 120}}).json()
    return r["eval_count"] / (r["eval_duration"] / 1e9)


def main(folder: str) -> int:
    folder = Path(folder)
    data = Path(tempfile.mkdtemp(prefix="pron-gpu-"))
    env = {**os.environ, "TUTOR_PORT": str(PORT), "TUTOR_DATA_DIR": str(data / "data"),
           "TUTOR_MATERIALS_DIR": str(data / "materials")}
    server = subprocess.Popen([sys.executable, "-m", "tutor"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                              stderr=open(data / "server.err.log", "w"))
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=300)
        for _ in range(60):
            try:
                c.get("/api/profile")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        gpu("start")
        print(f"tutor model: {tutor_speed():.0f} tokens/s")
        gpu("tutor model loaded")
        c.post("/api/voice/warmup")
        base = gpu("+ Whisper and Kokoro")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        item = next(i for i in manifest if i["text"].startswith("Please pass") and i["kind"] == "error")
        audio = (folder / f"{item['id']}.wav").read_bytes()
        for n in range(3):
            t0 = time.perf_counter()
            r = c.post("/api/pronunciation/check", files={"audio": ("s.wav", audio, "audio/wav")},
                       data={"text": item["text"], "drill": "p-b"})
            r.raise_for_status()
            print(f"check {n + 1}: {time.perf_counter() - t0:.2f} s, score {r.json()['score']}, issues "
                  f"{[(i['word'], i['phone'], i['heard']) for i in r.json()['issues']]}")
        after = gpu("+ pronunciation model")
        print(f"  the pronunciation model added {after - base:.2f} GB")
        # a long sentence (about 15 s): the most memory a check needs, returned right after
        sentences = [i for i in manifest if i["kind"] == "correct" and i["voice"] == "af_heart" and " " in i["text"]]
        import io
        import wave
        parts, text = [], []
        for i in sentences[:6]:
            with wave.open(io.BytesIO((folder / f"{i['id']}.wav").read_bytes())) as w:
                parts.append(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768)
                rate = w.getframerate()
            text.append(i["text"])
        long_audio = wav_bytes(np.concatenate(parts), rate)
        t0 = time.perf_counter()
        r = c.post("/api/pronunciation/check", files={"audio": ("s.wav", long_audio, "audio/wav")},
                   data={"text": " ".join(text)})
        r.raise_for_status()
        print(f"long check ({r.json()['duration']} s of speech, {sum(len(t.split()) for t in text)} words): "
              f"{time.perf_counter() - t0:.2f} s, score {r.json()['score']}")
        gpu("after the long check")
        print(f"tutor model after: {tutor_speed():.0f} tokens/s")
        gpu("end")
    finally:
        server.terminate()
        server.wait(timeout=30)
    gpu("test server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
