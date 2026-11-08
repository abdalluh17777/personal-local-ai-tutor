"""Arabic speech worker: SILMA TTS (F5-TTS, Arabic/English) on the GPU behind a small local HTTP API.

It runs in its own process and environment because SILMA needs PyTorch and numpy 1.x, which would clash
with the main app's libraries. Only the tutor server talks to it.

    GET  /health                                 -> {"loaded": bool, "cuda": bool, "device": "cuda" | null}
                                                    (503 when CUDA is not available)
    POST /warmup                                 -> loads the model and speaks one word (once)
    POST /speak  {"text": "...", "speed": 1.0}   -> 24 kHz mono 16-bit WAV
"""

import io
import os
import sys
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("TQDM_DISABLE", "1")   # SILMA shows progress bars; they only clutter the log

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "shims"))      # import-only stand-in for nemo_text_processing (see shims/)

import numpy as np  # noqa: E402
import torch  # noqa: E402  (before onnxruntime, so CATT's ONNX Runtime can reuse PyTorch's CUDA libraries)
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("TUTOR_ARABIC_PORT", "8766"))
MODELS_DIR = Path(os.environ.get("TUTOR_SILMA_DIR", HERE.parents[1] / "models" / "silma"))

# The reference recording shipped with SILMA, and what it says (the voice is cloned from it)
REF_TEXT = "ويدقق النظر في القرآن الكريم وسائر الكتب السماوية ويتبع مسالك الرسل العظام عليهم الصلاة والسلام."
SEED = 1234        # same seed, same voice from one sentence to the next
NFE_STEPS = 16     # SILMA's default number of diffusion steps


class SpeakIn(BaseModel):
    text: str
    speed: float = 1.0


class Engine:
    def __init__(self):
        self.tts = None
        self.ref_file = None
        self.load_lock = threading.Lock()
        self.run_lock = threading.Lock()

    def load(self):
        with self.load_lock:
            if self.tts is None:
                if not torch.cuda.is_available():
                    raise RuntimeError("CUDA is not available: the Arabic voice only runs on the GPU")
                from importlib.resources import files

                from silma_tts.api import SilmaTTS

                MODELS_DIR.mkdir(parents=True, exist_ok=True)
                # Normalization is NeMo-based (not available on Windows) and stays off; diacritics are added by CATT
                self.tts = SilmaTTS(device="cuda", hf_cache_dir=str(MODELS_DIR),
                                    enable_normalizer=False, force_tashkeel=True)
                self.ref_file = str(files("silma_tts").joinpath("infer/ref_audio_samples/ar.ref.24k.wav"))
        return self.tts

    def speak(self, text: str, speed: float) -> tuple[np.ndarray, int]:
        tts = self.load()
        with self.run_lock:
            wav, sr, _ = tts.infer(ref_file=self.ref_file, ref_text=REF_TEXT, gen_text=text,
                                   speed=min(max(speed, 0.6), 1.4), nfe_step=NFE_STEPS, seed=SEED,
                                   normalize_numbers=False, force_tashkeel=True, show_info=lambda *a, **k: None)
        return np.asarray(wav, dtype=np.float32), int(sr)


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


engine = Engine()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def tutor_server_only(request: Request, call_next):
    # Only the tutor server (no browser: browsers send Origin) on this machine (Host blocks DNS rebinding)
    if request.headers.get("host") != f"{HOST}:{PORT}" or request.headers.get("origin"):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/health")
def health():
    # The real state: without CUDA the voice cannot run (it never falls back to the CPU), so it is not healthy
    cuda = torch.cuda.is_available()
    body = {"loaded": engine.tts is not None, "cuda": cuda, "device": "cuda" if cuda else None}
    return JSONResponse(body, status_code=200 if cuda else 503)


@app.post("/warmup")
def warmup():
    if engine.tts is not None:   # already loaded by an earlier warm-up or sentence: no second synthesis
        return {"loaded": True, "seconds": 0.0}
    t0 = time.perf_counter()
    try:
        engine.speak("مرحباً.", 1.0)
    except RuntimeError as e:   # no CUDA, or the model failed on the GPU
        return JSONResponse({"detail": str(e)}, status_code=503)
    return {"loaded": True, "seconds": round(time.perf_counter() - t0, 2)}


@app.post("/speak")
def speak(body: SpeakIn):
    text = body.text.strip()
    if not text:
        return JSONResponse({"detail": "Empty text"}, status_code=400)
    try:
        samples, rate = engine.speak(text[:600], body.speed)
    except RuntimeError as e:
        return JSONResponse({"detail": str(e)}, status_code=503)
    return Response(wav_bytes(samples, rate), media_type="audio/wav")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
