# Starts the project's portable Ollama server in the background (no-op if it is already running).
. "$PSScriptRoot\env.ps1"

try {
    Invoke-RestMethod "http://$env:OLLAMA_HOST/api/version" -TimeoutSec 2 | Out-Null
    Write-Host "Ollama is already running on $env:OLLAMA_HOST"
    return
} catch {}

$log = "$Root\logs\ollama-server.log"
$err = "$Root\logs\ollama-server.err.log"
Start-Process -FilePath "$Root\tools\ollama\ollama.exe" -ArgumentList "serve" `
    -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $err

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $v = Invoke-RestMethod "http://$env:OLLAMA_HOST/api/version" -TimeoutSec 2
        Write-Host "Ollama $($v.version) started on $env:OLLAMA_HOST (log: $err)"
        return
    } catch {}
}
Write-Error "Ollama did not start; see $err"
exit 1
