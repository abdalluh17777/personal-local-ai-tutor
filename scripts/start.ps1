# One-click start: Ollama on the GPU, the tutor model, the local web server, and the app window.
# Works in Windows PowerShell 5.1 and PowerShell 7.
. "$PSScriptRoot\env.ps1"

# A fresh clone has no tools, models or Python environment yet: they come from the installer
if (-not (Test-Path "$Root\.venv\Scripts\python.exe") -or -not (Test-Path "$Root\tools\ollama\ollama.exe")) {
    Write-Error "Personal Tutor is not installed yet: run 'Install Personal Tutor.cmd' in $Root first."
    exit 1
}

$global:LASTEXITCODE = 0          # a failed command earlier in this window must not read as a failure here
& "$PSScriptRoot\start-ollama.ps1"
if ($LASTEXITCODE) { exit 1 }     # start-ollama.ps1 has said why

# Create the tutor model from its Modelfile the first time (or after it was removed)
$installed = (Invoke-RestMethod "http://$env:OLLAMA_HOST/api/tags").models | ForEach-Object { $_.name }
if ($installed -notcontains "tutor:latest") {
    Write-Host "Creating the tutor model..."
    & "$Root\tools\ollama\ollama.exe" create tutor -f "$Root\models\Modelfile.tutor"
}

# The Arabic voice runs as its own process (separate Python environment: workers\arabic_tts)
$arabicDir = "$Root\workers\arabic_tts"
if (Test-Path "$arabicDir\.venv\Scripts\python.exe") {
    $arabicUp = $false
    try { Invoke-WebRequest "http://127.0.0.1:8766/health" -UseBasicParsing -TimeoutSec 2 | Out-Null; $arabicUp = $true } catch {}
    if (-not $arabicUp) {
        Start-Process -FilePath "$arabicDir\.venv\Scripts\python.exe" -ArgumentList "worker.py" -WorkingDirectory $arabicDir `
            -WindowStyle Hidden -RedirectStandardOutput "$Root\logs\arabic-tts.log" -RedirectStandardError "$Root\logs\arabic-tts.err.log"
    }
}

$url = "http://127.0.0.1:8765"
$running = $false
try { Invoke-WebRequest "$url/api/profile" -UseBasicParsing -TimeoutSec 2 | Out-Null; $running = $true } catch {}

if (-not $running) {
    Start-Process -FilePath "$Root\.venv\Scripts\python.exe" -ArgumentList "-m", "tutor" -WorkingDirectory $Root `
        -WindowStyle Hidden -RedirectStandardOutput "$Root\logs\server.log" -RedirectStandardError "$Root\logs\server.err.log"
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try { Invoke-WebRequest "$url/api/profile" -UseBasicParsing -TimeoutSec 2 | Out-Null; $running = $true; break } catch {}
    }
    if (-not $running) { Write-Error "The tutor server did not start; see $Root\logs\server.err.log"; exit 1 }
}

# Open as a standalone app window (no address bar); fall back to the default browser
if ($env:TUTOR_NO_BROWSER) { exit 0 }
$browsers = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
              "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")
$browser = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($browser) { Start-Process $browser "--app=$url" } else { Start-Process $url }
