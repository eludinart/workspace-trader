# Relance l'engine UNIQUEMENT s'il ne répond plus (pas de kill préventif).
# Usage: powershell -File scripts/watchdog-engine.ps1

$ErrorActionPreference = "SilentlyContinue"
$EngineUrl = "http://127.0.0.1:8100/health"
$EngineDir = Join-Path $PSScriptRoot "..\apps\engine" | Resolve-Path
$Python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $Python) { $Python = "python" }
$Log = Join-Path $PSScriptRoot "..\apps\engine\data\watchdog.log"

function Write-Log([string]$msg) {
  $line = "{0:u} {1}" -f (Get-Date).ToUniversalTime(), $msg
  $dir = Split-Path $Log
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
  Add-Content -Path $Log -Value $line
}

function Test-EngineOk {
  try {
    $r = Invoke-WebRequest -Uri $EngineUrl -UseBasicParsing -TimeoutSec 5
    return ($r.StatusCode -eq 200)
  } catch {
    return $false
  }
}

function Start-EngineDetached {
  Write-Log "starting uvicorn (detached)"
  Start-Process -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8100") `
    -WorkingDirectory $EngineDir `
    -WindowStyle Hidden
}

Write-Log "watchdog started"
while ($true) {
  if (-not (Test-EngineOk)) {
    Write-Log "health failed — restart"
    Start-EngineDetached
    Start-Sleep -Seconds 45
    if (Test-EngineOk) { Write-Log "engine healthy after restart" }
    else { Write-Log "engine still down after restart" }
  }
  Start-Sleep -Seconds 120
}
