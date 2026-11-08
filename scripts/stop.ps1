# Stops the tutor server and Ollama, freeing all GPU and system memory they use.
. "$PSScriptRoot\env.ps1"

# Only the tutor's own processes, by command line: a uv run, pytest or build in another window uses the same
# Python and must keep running. Ollama (server, runners, llama-server), the web server and the Arabic voice
# worker; each venv python.exe is a launcher whose child, tools\python\...\python.exe, carries its arguments.
$all = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase) })
$tutor = @($all | Where-Object {
    $exe, $cmd = $_.ExecutablePath, "$($_.CommandLine)"
    ($exe -like "$Root\tools\ollama\*" -and ($_.Name -eq "llama-server.exe" -or $cmd -match '\s(serve|runner)(\s|$)')) -or
    ($_.Name -eq "python.exe" -and $cmd -match '\s-m\s+tutor\s*$') -or
    ($exe -like "$Root\workers\arabic_tts\*" -and $cmd -match '\sworker\.py\s*$')
})
$ids = @($tutor | ForEach-Object { $_.ProcessId })
$tutor += @($all | Where-Object { $_.ExecutablePath -like "$Root\tools\python\*" -and $ids -contains $_.ParentProcessId -and $ids -notcontains $_.ProcessId })

$tutor | ForEach-Object {
    Write-Host "Stopping $($_.Name) ($($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
