# Project environment. Dot-source this file (. .\scripts\env.ps1) before working on the project.
# Every tool, cache, and model is redirected into the project folder (wherever it was cloned), so nothing
# lands in the user profile on C: and deleting the folder removes everything.

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Ollama: models in the project, one model and one request slot at a time so VRAM is never oversubscribed
$env:OLLAMA_MODELS            = "$Root\models\ollama"
$env:OLLAMA_HOST              = "127.0.0.1:11434"
$env:OLLAMA_FLASH_ATTENTION   = "1"
$env:OLLAMA_CONTEXT_LENGTH    = "16384"
$env:OLLAMA_NUM_PARALLEL      = "1"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_KEEP_ALIVE        = "30m"

# Ollama's llama-server inherits these. Measured on this PC: with the defaults (8 GB host prompt cache,
# 32 context checkpoints) the engine grew to 6.4 GB of private RAM after a few dozen requests.
$env:LLAMA_ARG_CACHE_RAM       = "0"   # no prompt cache in system RAM
$env:LLAMA_ARG_CTX_CHECKPOINTS = "4"   # few saved states of the recurrent layers (small, in RAM)

# Python tooling (uv) and its caches
$env:UV_CACHE_DIR          = "$Root\cache\uv"
$env:UV_PYTHON_INSTALL_DIR = "$Root\tools\python"
$env:UV_PYTHON_PREFERENCE  = "only-managed"
$env:UV_TOOL_DIR           = "$Root\tools\uv-tools"
$env:UV_TOOL_BIN_DIR       = "$Root\tools\uv-tools\bin"
$env:UV_PYTHON_BIN_DIR     = "$Root\tools\python\bin"
$env:PIP_CACHE_DIR         = "$Root\cache\pip"

# CUDA's compiled-kernel cache: libraries without native Blackwell (sm_120) kernels are compiled once
# on first use; keep that cache in the project and large enough that it is not evicted (default: 256 MB on C:)
$env:CUDA_CACHE_PATH    = "$Root\cache\cuda"
$env:CUDA_CACHE_MAXSIZE = "2147483648"

# Model caches for the voice stack (Whisper, Kokoro, wav2vec2, embeddings)
$env:HF_HOME    = "$Root\models\hf"
$env:TORCH_HOME = "$Root\models\torch"

# espeak-ng (used by Kokoro and the pronunciation module)
$env:PHONEMIZER_ESPEAK_LIBRARY = "$Root\tools\espeak-ng\eSpeak NG\libespeak-ng.dll"
$env:PHONEMIZER_ESPEAK_PATH    = "$Root\tools\espeak-ng\eSpeak NG\espeak-ng.exe"
$env:ESPEAK_DATA_PATH          = "$Root\tools\espeak-ng\eSpeak NG\espeak-ng-data"

$env:PATH = "$Root\tools\ollama;$Root\tools\uv;$Root\tools\espeak-ng\eSpeak NG;C:\Windows\System32;$env:PATH"
