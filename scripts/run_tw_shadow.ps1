# Weekly: refresh 0050 candles, then append one shadow-track row.
# Called by the scheduled task "TwShadow0050". Weekly rather than daily
# because TWSE's WAF punishes frequent sweeps; the shadow script refuses
# to record when the data is stale, so a missed week is visible.

$ErrorActionPreference = "Continue"
Set-Location "D:\TW-Stock-Trading"

$runDir = "data\runtime\shadow_runs"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$log = Join-Path $runDir "tw_shadow_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

& ".venv\Scripts\python.exe" -m scripts.ingest_public_ohlcv *> $log
"ingest exit=$LASTEXITCODE" | Add-Content $log
& ".venv\Scripts\python.exe" -m scripts.shadow_signal_tw *>> $log
"shadow exit=$LASTEXITCODE finished=$(Get-Date -Format o)" | Add-Content $log
