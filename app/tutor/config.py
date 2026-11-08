"""Paths and settings. Everything lives under the project root (models, data, logs, tools)."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Overridable so smoke tests can run against throwaway data instead of the learner's own
DATA_DIR = Path(os.environ.get("TUTOR_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "tutor.sqlite3"
BACKUP_DIR = DATA_DIR / "backups"
MATERIALS_DIR = Path(os.environ.get("TUTOR_MATERIALS_DIR", ROOT / "materials"))
LOGS_DIR = Path(os.environ.get("TUTOR_LOGS_DIR", ROOT / "logs"))   # tests point it elsewhere
MODELS_DIR = ROOT / "models"
WHISPER_MODEL = "large-v3-turbo"
KOKORO_MODEL = MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS_DIR / "kokoro" / "voices-v1.0.bin"
ARABIC_TTS_URL = os.environ.get("TUTOR_ARABIC_URL", "http://127.0.0.1:8766")   # workers/arabic_tts
PRON_MODEL_DIR = MODELS_DIR / "pron" / "l2arctic"     # phone recogniser for the pronunciation check
ESPEAK = os.environ.get("PHONEMIZER_ESPEAK_PATH", str(ROOT / "tools" / "espeak-ng" / "eSpeak NG" / "espeak-ng.exe"))
PROMPTS_DIR = Path(__file__).parent / "prompts"
WEB_DIR = Path(__file__).parent / "web"

OLLAMA_URL = os.environ.get("TUTOR_OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("TUTOR_MODEL", "tutor")
HOST = "127.0.0.1"
PORT = int(os.environ.get("TUTOR_PORT", "8765"))

NUM_CTX = 16384          # must match models/Modelfile.tutor
HISTORY_MESSAGES = 12    # recent session messages sent with each request
MAX_PASSAGES = 5         # course passages injected per question
MAX_UPLOAD_MB = 50
