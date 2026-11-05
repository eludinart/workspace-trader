# Démarre engine (détaché) + watchdog + Next.js sans tuer un engine déjà sain.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$EngineDir = Join-Path $Root "apps\engine"
$WebDir = Join-Path $Root "apps\web"
$Python = (Get-Command python).Source

function Test-Engine {
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:8100/health" -UseBasicParsing -TimeoutSec 4
    return $r.StatusCode -eq 200
  } catch { return $false }
}

if (-not (Test-Engine)) {
  Write-Host "Starting engine on :8100 ..."
  Start-Process -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8100") `
    -WorkingDirectory $EngineDir `
    -WindowStyle Hidden
  Start-Sleep -Seconds 8
} else {
  Write-Host "Engine already healthy on :8100"
}

$wd = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'watchdog-engine.ps1' }
if (-not $wd) {
  Write-Host "Starting watchdog ..."
  Start-Process powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "watchdog-engine.ps1")
  ) -WindowStyle Hidden
}

Write-Host "Starting Next.js on :3100 ..."
$env:ENGINE_URL = "http://127.0.0.1:8100"
Set-Location $WebDir
npx next dev -p 3100 -H 127.0.0.1
