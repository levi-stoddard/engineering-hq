# Verified backup of the registers and release history (safe while the supervisor runs: the files
# are append-only). Writes <state_dir>\..\backups\<timestamp>\ with SHA256SUMS, then verifies the
# copies with check.py. A copy that does not verify fails the backup.
# Run it daily and before any change to the config or a restore.
#   powershell -ExecutionPolicy Bypass -File scripts\backup.ps1 -Config examples\acme\config.yaml
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$ConfigPath = (Resolve-Path $Config).Path
$Paths = & $Python -c "import sys; sys.path.insert(0, r'$Repo\supervisor'); from config import load_config; c = load_config(sys.argv[1]); print(c['state_dir']); print(c['releases']['dir'])" $ConfigPath
$State, $Releases = $Paths
$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$Target = Join-Path (Split-Path -Parent $State) "backups\$Stamp"
New-Item -ItemType Directory -Force -Path $Target | Out-Null

$Sources = @(Get-ChildItem (Join-Path $State 'registers') -Filter *.jsonl -ErrorAction SilentlyContinue)
$History = Join-Path $Releases 'history.jsonl'
if (Test-Path $History) { $Sources += Get-Item $History }
if (-not $Sources) { Write-Host 'Nothing to back up yet.'; exit 0 }
$Lines = foreach ($Source in $Sources) {
    Copy-Item $Source.FullName (Join-Path $Target $Source.Name)
    "$((Get-FileHash $Source.FullName -Algorithm SHA256).Hash.ToLower())  $($Source.Name)"
}
$Lines | Set-Content (Join-Path $Target 'SHA256SUMS') -Encoding ascii

& $Python "$Repo\scripts\check.py" --config $ConfigPath --backup $Target
if ($LASTEXITCODE -ne 0) { throw "BACKUP NOT VERIFIED: $Target" }
Write-Host "Verified backup: $Target"
