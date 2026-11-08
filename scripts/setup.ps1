# One-time installer for Personal Tutor on Windows 10/11 (64-bit) with an NVIDIA graphics card.
#
#   Double-click "Install Personal Tutor.cmd" in the project folder, or run:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-WithArabicVoice | -NoArabicVoice] [-NoShortcut] [-Yes]
#
# Everything is downloaded into this project folder (tools, Python, models, caches): nothing is installed
# system-wide, no administrator rights are needed, and deleting the folder removes it all.
# Safe to run again: finished steps are skipped, so a run that was interrupted continues where it stopped.
# Works in Windows PowerShell 5.1 and PowerShell 7. Keep this file ASCII-only (5.1 reads it as ANSI).

param(
    [switch]$WithArabicVoice,   # also install the Arabic voice (SILMA TTS) without asking
    [switch]$NoArabicVoice,     # skip the Arabic voice without asking
    [switch]$NoShortcut,        # do not create the desktop shortcut
    [switch]$Yes                # accept the defaults for every question
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"     # Invoke-WebRequest is many times slower with its progress bar
. "$PSScriptRoot\env.ps1"

# Pinned versions: the ones this project was built and measured with
$UvVersion     = "0.12.21"
$OllamaVersion = "v0.35.0"
$EspeakVersion = "1.52.0"
$BaseModel     = "qwen3.5:9b"
$Urls = @{
    Uv      = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
    Ollama  = "https://github.com/ollama/ollama/releases/download/$OllamaVersion/ollama-windows-amd64.zip"
    Espeak  = "https://github.com/espeak-ng/espeak-ng/releases/download/$EspeakVersion/espeak-ng.msi"
    Kokoro  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    Voices  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
}
$Downloads = "$Root\cache\downloads"
$TotalSteps = 9

foreach ($dir in "$Root\logs", "$Root\data", "$Root\tools", $Downloads) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
try { Start-Transcript -Path "$Root\logs\setup.log" -Append | Out-Null } catch {}

# ------------------------------------------------------------------------------------------------ helpers
function Step([int]$n, [string]$text) {
    Write-Host ""
    Write-Host "[$n/$TotalSteps] $text" -ForegroundColor Cyan
}
function Ok([string]$text)   { Write-Host "    OK   $text" -ForegroundColor Green }
function Info([string]$text) { Write-Host "         $text" -ForegroundColor Gray }
function Warn([string]$text) { Write-Host "    !    $text" -ForegroundColor Yellow }
function Fail([string]$text) {
    Write-Host ""
    Write-Host "    X    $text" -ForegroundColor Red
    Write-Host "         Fix the problem above, then run the installer again: finished steps are skipped." -ForegroundColor Red
    Write-Host "         Full log: $Root\logs\setup.log"
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
function Ask([string]$question, [bool]$default) {
    if ($Yes) { return $default }
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "    ?    $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}
# Runs a native program and stops the installer if it fails
function Run([string]$what, [scriptblock]$command) {
    $global:LASTEXITCODE = 0
    & $command
    if ($LASTEXITCODE) { Fail "$what failed (exit code $LASTEXITCODE)." }
}
# Downloads with curl.exe (built into Windows 10 1803+): resumes, retries, shows progress
function Download([string]$url, [string]$dest) {
    if (Test-Path $dest) { Info "already downloaded: $(Split-Path $dest -Leaf)"; return }
    Info "downloading $(Split-Path $dest -Leaf) ..."
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -# -o "$dest.part" $url
    if ($LASTEXITCODE) { Fail "Download failed: $url  (check your internet connection)" }
    Move-Item -Force "$dest.part" $dest
}
function Test-Url([string]$url) {
    try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

Write-Host ""
Write-Host "  Personal Tutor - installer" -ForegroundColor White
Write-Host "  Project folder: $Root"
Write-Host "  Everything is installed inside this folder. Expect 20-30 GB of downloads on the first run."

# ------------------------------------------------------------------------------------------------ 1. checks
Step 1 "Checking this computer"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "A 64-bit Windows is required." }
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { Fail "curl.exe is missing: update Windows 10 to version 1803 or newer." }
Ok "64-bit Windows $([Environment]::OSVersion.Version)"

$gpus = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" })
if (-not $gpus) {
    Fail ("No NVIDIA graphics card was found. Every model in this project runs on an NVIDIA GPU (CUDA) " +
          "on purpose, with no slow CPU fallback.")
}
$smi = @("$env:SystemRoot\System32\nvidia-smi.exe", "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($smi) {
    $q = (& $smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits | Select-Object -First 1) -split ",\s*"
    $vramGb = [math]::Round([double]$q[1] / 1024, 1)
    Ok "$($q[0]), $vramGb GB of video memory, driver $($q[2])"
    if ($vramGb -lt 11.5) {
        Warn "Less than 12 GB of video memory: the tutor model alone needs about 6 GB and the voice another 2-3 GB."
        Warn "It may not fit. 16 GB is recommended (measured: 11.7-13.2 GB in use with everything loaded)."
        if (-not (Ask "Continue anyway?" $false)) { Fail "Stopped at your request." }
    } elseif ($vramGb -lt 15) {
        Warn "12-15 GB of video memory: fine for the tutor and the English voice; leave out the Arabic voice."
    }
    if ([int]($q[2].Split(".")[0]) -lt 560) {
        Warn "Your NVIDIA driver is old ($($q[2])). Install the latest Game Ready or Studio driver from nvidia.com first."
    }
} else {
    Ok "$($gpus[0].Name)"
    Warn "nvidia-smi was not found: install the latest NVIDIA driver from nvidia.com if the tutor fails to start."
}

$drive = Get-PSDrive -Name (Split-Path -Qualifier $Root).TrimEnd(":")
$freeGb = [math]::Round($drive.Free / 1GB)
Ok "$freeGb GB free on drive $($drive.Name):"
if ($freeGb -lt 25) { Warn "At least 25 GB free is needed (about 36 GB with the Arabic voice)." }

$installArabic = $false
if ($WithArabicVoice) { $installArabic = $true }
elseif (-not $NoArabicVoice) {
    $default = (-not $smi) -or ($vramGb -ge 15)
    Info "Optional: an Arabic voice, so Arabic words in the tutor's answers are spoken too (about 11 GB more)."
    $installArabic = Ask "Install the Arabic voice?" $default
}

# ------------------------------------------------------------------------------------------------ 2. uv
Step 2 "uv (Python package manager) $UvVersion"
if (Test-Path "$Root\tools\uv\uv.exe") { Ok "already installed" } else {
    Download $Urls.Uv "$Downloads\uv-$UvVersion.zip"
    New-Item -ItemType Directory -Force "$Root\tools\uv" | Out-Null
    Run "Unpacking uv" { tar.exe -xf "$Downloads\uv-$UvVersion.zip" -C "$Root\tools\uv" }
    Ok "installed in tools\uv"
}

# ------------------------------------------------------------------------------------------------ 3. Ollama
Step 3 "Ollama (runs the language model on the GPU) $OllamaVersion"
if (Test-Path "$Root\tools\ollama\ollama.exe") { Ok "already installed" } else {
    $zip = "$Downloads\ollama-$OllamaVersion.zip"
    Download $Urls.Ollama $zip
    New-Item -ItemType Directory -Force "$Root\tools\ollama" | Out-Null
    Info "unpacking (about 2 GB) ..."
    Run "Unpacking Ollama" { tar.exe -xf $zip -C "$Root\tools\ollama" }
    Remove-Item $zip -Force
    Ok "installed in tools\ollama (portable: no Windows service, no tray icon)"
}

# ------------------------------------------------------------------------------------------------ 4. eSpeak NG
Step 4 "eSpeak NG $EspeakVersion (phonemes for the English voice and the pronunciation check)"
if (Test-Path "$Root\tools\espeak-ng\eSpeak NG\espeak-ng.exe") { Ok "already installed" } else {
    $msi = "$Downloads\espeak-ng-$EspeakVersion.msi"
    Download $Urls.Espeak $msi
    # An "administrative install" only unpacks the files into the folder: nothing is registered in Windows
    $p = Start-Process msiexec.exe -ArgumentList "/a `"$msi`" /qn TARGETDIR=`"$Root\tools\espeak-ng`"" -Wait -PassThru
    if ($p.ExitCode -or -not (Test-Path "$Root\tools\espeak-ng\eSpeak NG\espeak-ng.exe")) {
        Fail "Unpacking eSpeak NG failed (msiexec exit code $($p.ExitCode))."
    }
    Remove-Item "$Root\tools\espeak-ng\espeak-ng-$EspeakVersion.msi" -Force -ErrorAction SilentlyContinue   # the copy msiexec leaves
    Ok "installed in tools\espeak-ng"
}

# ------------------------------------------------------------------------------------------------ 5. Python env
Step 5 "Python 3.12 and the app's libraries (FastAPI, faster-whisper, ONNX Runtime GPU, CUDA 12 runtime)"
Info "about 3 GB on the first run ..."
Push-Location $Root
Run "Installing the Python environment" { uv sync --frozen --no-dev }
Pop-Location
Ok "environment ready in .venv"

# ------------------------------------------------------------------------------------------------ 6. speech models
Step 6 "Speech models (English voice, speech recognition, pronunciation check)"
New-Item -ItemType Directory -Force "$Root\models\kokoro" | Out-Null
Download $Urls.Kokoro "$Root\models\kokoro\kokoro-v1.0.onnx"
Download $Urls.Voices "$Root\models\kokoro\voices-v1.0.bin"
Ok "Kokoro v1.0 (the tutor's English voice)"

Info "Whisper large-v3-turbo (your speech to text, about 1.6 GB) ..."
Push-Location $Root
Run "Downloading Whisper" {
    & "$Root\.venv\Scripts\python.exe" -c "import sys; from faster_whisper.utils import download_model; download_model('large-v3-turbo', cache_dir=sys.argv[1])" "$Root\models\whisper"
}
Pop-Location
Ok "Whisper large-v3-turbo"

if (Test-Path "$Root\models\pron\l2arctic\model_fp16.onnx") { Ok "pronunciation model (already built)" } else {
    Info "Pronunciation model: wav2vec2 fine-tuned on L2-ARCTIC, converted to ONNX float16."
    Info "This downloads PyTorch into a temporary environment and takes 5-10 minutes ..."
    Push-Location $Root
    Run "Building the pronunciation model" {
        uv run --no-project --python 3.12 --with "torch==2.8.0" --with "transformers>=4.44,<5" --with "onnx>=1.17" `
            --with "onnxconverter-common>=1.14" --with onnxruntime python scripts\export_pron_model.py
    }
    Pop-Location
    # The PyTorch original (1.2 GB) is no longer needed once the ONNX model is built
    Remove-Item -Recurse -Force "$Root\models\hf\hub\models--mrrubino--wav2vec2-large-xlsr-53-l2-arctic-phoneme" -ErrorAction SilentlyContinue
    Ok "pronunciation model built in models\pron\l2arctic"
}

# ------------------------------------------------------------------------------------------------ 7. language model
Step 7 "Language model: $BaseModel (about 6.6 GB)"
Run "Starting Ollama" { & "$PSScriptRoot\start-ollama.ps1" }
$installed = @((Invoke-RestMethod "http://$env:OLLAMA_HOST/api/tags").models | ForEach-Object { $_.name })
if ($installed -contains $BaseModel) { Ok "$BaseModel already downloaded" } else {
    Run "Downloading $BaseModel" { ollama pull $BaseModel }
    Ok "$BaseModel downloaded"
}
if ($installed -contains "tutor:latest") { Ok "tutor model already created" } else {
    Run "Creating the tutor model" { ollama create tutor -f "$Root\models\Modelfile.tutor" }
    Ok "tutor model created from models\Modelfile.tutor (every layer on the GPU)"
}

# ------------------------------------------------------------------------------------------------ 8. Arabic voice
Step 8 "Arabic voice (optional)"
$arabicDir = "$Root\workers\arabic_tts"
if (-not $installArabic) {
    Ok "skipped (run the installer again with -WithArabicVoice to add it later)"
} else {
    Info "PyTorch 2.8 with CUDA 12.8 and SILMA TTS in their own environment (about 8 GB) ..."
    Run "Installing the Arabic voice environment" { uv sync --frozen --project $arabicDir }
    Ok "environment ready in workers\arabic_tts\.venv"
}

# ------------------------------------------------------------------------------------------------ 9. start, courses
Step 9 "Starting the tutor and loading the bundled courses"
$noBrowser = $env:TUTOR_NO_BROWSER
$env:TUTOR_NO_BROWSER = "1"
Run "Starting the tutor" { & "$PSScriptRoot\start.ps1" }
$env:TUTOR_NO_BROWSER = $noBrowser

$courses = @(Invoke-RestMethod "http://127.0.0.1:8765/api/courses")
if ($courses.Count -gt 0) {
    Ok "your database already has $($courses.Count) course(s): nothing loaded"
} else {
    Info "6 courses (about 190 lessons) and 2 vocabulary lists, about 2 minutes ..."
    Push-Location $Root
    Run "Loading the courses" { & "$Root\.venv\Scripts\python.exe" scripts\load_bundled_courses.py }
    Pop-Location
    Ok "courses loaded"
}

if ($installArabic) {
    Info "Downloading the Arabic voice models on first start (about 2.5 GB) ..."
    for ($i = 0; $i -lt 60 -and -not (Test-Url "http://127.0.0.1:8766/health"); $i++) { Start-Sleep -Seconds 1 }
    try {
        Invoke-RestMethod -Method Post "http://127.0.0.1:8766/warmup" -TimeoutSec 3600 | Out-Null
        Ok "Arabic voice ready"
    } catch {
        Warn "The Arabic voice did not warm up ($($_.Exception.Message)). See logs\arabic-tts.err.log."
        Warn "The tutor works without it: Arabic words are simply not spoken."
    }
}

if (-not $NoShortcut -and (Ask "Create a desktop shortcut?" $true)) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut("$desktop\Personal Tutor.lnk")
    $link.TargetPath = "$Root\Start Personal Tutor.cmd"
    $link.WorkingDirectory = $Root
    $link.WindowStyle = 7          # minimized: the launcher's console window is not shown
    $link.Description = "Personal Tutor: your local AI study partner"
    $link.IconLocation = "$Root\app\tutor\web\icon.ico"
    $link.Save()
    Ok "shortcut created on the desktop"
}

Write-Host ""
Write-Host "  Done. Personal Tutor is installed and running." -ForegroundColor Green
Write-Host "  Start it any time with 'Start Personal Tutor.cmd' (or the desktop shortcut);"
Write-Host "  stop it and free the GPU with 'Stop Personal Tutor.cmd'."
Write-Host ""
try { Stop-Transcript | Out-Null } catch {}

if (Ask "Open the tutor now?" $true) { & "$PSScriptRoot\start.ps1" }
exit 0
