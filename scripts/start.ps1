# Starts the supervisor in the background for one config, and records exactly what it started.
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Config examples\acme\config.yaml
# Refuses if a supervisor for this config is already running or a supervised port is taken.
# Records the PID and command line in <state_dir>\session.json so stop.ps1 can stop only that process.
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$ConfigPath = (Resolve-Path $Config).Path
$State = & $Python -c "import sys; sys.path.insert(0, r'$Repo\supervisor'); from config import load_config; print(load_config(sys.argv[1])['state_dir'])" $ConfigPath
if ($LASTEXITCODE -ne 0) { Write-Host 'The config did not load; nothing was started.'; exit 2 }

$Lock = Join-Path $State 'supervisor.lock'
if (Test-Path $Lock) {
    $LockPid = [int](Get-Content $Lock -Raw)
    if (Get-Process -Id $LockPid -ErrorAction SilentlyContinue) { Write-Host "A supervisor is already running for this config (pid $LockPid). Nothing was started."; exit 2 }
}
$Ports = & $Python -c "import sys; sys.path.insert(0, r'$Repo\supervisor'); from config import load_config; print(' '.join(str(s['port']) for s in load_config(sys.argv[1]).get('services') or []))" $ConfigPath
foreach ($Port in ($Ports -split ' ' | Where-Object { $_ })) {
    if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "Port $Port is already in use. Nothing was started."; exit 2
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $State 'logs') | Out-Null
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$ArgList = @("$Repo\supervisor\hq_supervisor.py", '--config', $ConfigPath, 'run')
$Process = Start-Process $Python -ArgumentList $ArgList -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $State "logs\supervisor_$Stamp.log") -RedirectStandardError (Join-Path $State "logs\supervisor_$Stamp.err.log")

$Session = [ordered]@{ started_utc = (Get-Date).ToUniversalTime().ToString('o'); pid = $Process.Id; command = ($ArgList -join ' '); config = $ConfigPath }
$Session | ConvertTo-Json | Set-Content (Join-Path $State 'session.json') -Encoding utf8
Write-Host "Supervisor started (pid $($Process.Id)). Check: python scripts\check.py --config $Config   Stop: scripts\stop.ps1 -Config $Config"
