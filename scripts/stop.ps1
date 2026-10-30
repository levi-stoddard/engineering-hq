# Stops ONLY the supervisor recorded by start.ps1. First asks it to stop cleanly (a STOP file it
# checks every pass, so it can stop the services it started); after 20 seconds, stops the recorded
# process itself, but only if its command line still matches. Touches nothing else.
#   powershell -ExecutionPolicy Bypass -File scripts\stop.ps1 -Config examples\acme\config.yaml
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Python = 'python'
)
$Repo = Split-Path -Parent $PSScriptRoot
$ConfigPath = (Resolve-Path $Config).Path
$State = & $Python -c "import sys; sys.path.insert(0, r'$Repo\supervisor'); from config import load_config; print(load_config(sys.argv[1])['state_dir'])" $ConfigPath
$File = Join-Path $State 'session.json'
if (-not (Test-Path $File)) { Write-Host 'No recorded session; nothing stopped.'; exit 0 }
$Session = Get-Content $File -Raw | ConvertFrom-Json

function Ours {
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Session.pid)" -ErrorAction SilentlyContinue
    return ($Process -and $Process.CommandLine -match 'hq_supervisor\.py' -and $Process.CommandLine -match [regex]::Escape($ConfigPath))
}

if (Ours) {
    Set-Content (Join-Path $State 'STOP') 'stop requested by scripts\stop.ps1' -Encoding ascii
    for ($i = 0; $i -lt 40 -and (Ours); $i++) { Start-Sleep -Milliseconds 500 }
    if (Ours) {
        Stop-Process -Id $Session.pid
        Write-Host "The supervisor (pid $($Session.pid)) did not stop within 20 s; stopped it. Its services stop with it (Windows job object)."
    } else {
        Write-Host "Supervisor (pid $($Session.pid)) stopped cleanly."
    }
} else {
    Write-Host "pid $($Session.pid) is not running or is not this supervisor; left alone."
}
Rename-Item $File ('session_stopped_' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '.json')
