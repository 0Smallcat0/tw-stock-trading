# Weekly: refresh 0050 candles, then append one shadow-track row.
# Called by the scheduled task "TwShadow0050". Weekly rather than daily
# because TWSE's WAF punishes frequent sweeps; the shadow script refuses
# to record when the data is stale, so a missed week is visible.

$ErrorActionPreference = "Continue"
Set-Location "D:\TW-Stock-Trading"

$runDir = "data\runtime\shadow_runs"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$log = Join-Path $runDir "tw_shadow_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Start marker and interpreter check, added 2026-09-29 after the 2026-09-26
# run recorded nothing and said nothing. The .venv had been removed; every
# `&` call then failed command discovery, and PowerShell resolves a command
# BEFORE applying its redirection, so the CommandNotFoundException went to
# the host instead of the file and $LASTEXITCODE kept its null value. The
# whole run left a 94-byte log of three blank "exit=" fields and zero error
# text. Measured, not assumed: a probe against a non-existent interpreter
# reproduces the same empty-exit-code log.
"started=$(Get-Date -Format o)" | Add-Content $log
$python = ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    "FATAL: interpreter missing at $python - nothing ran and no track row was recorded." | Add-Content $log
    "FATAL: rebuild with: py -3.12 -m venv .venv ; .venv\Scripts\python.exe -m pip install --no-build-isolation -e .[dev]" | Add-Content $log
    "shadow exit=127 finished=$(Get-Date -Format o)" | Add-Content $log
    exit 127
}

# Dependency probe, added 2026-09-30 (iteration 71 of the Crypto-Trading loop,
# whose P1 owns these tracks). The Test-Path guard above is NOT sufficient, and
# that is measured rather than feared: the .venv rebuilt here on 2026-09-29 at
# 21:40 was a bare venv - `py -3.12 -m venv .venv` with no `pip install` - so
# its interpreter existed, Test-Path passed, and all three modules below failed
# to import (`No module named 'yaml'`, `No module named 'httpx'`). The
# 2026-10-03 run would have lost the 0050 and GLD rows a second week running,
# with the new guard in place and reporting success. All three modules are
# behind `if __name__ == "__main__":`, so importing them runs nothing.
& $python -c "import scripts.ingest_public_ohlcv, scripts.ingest_us_etf_ohlcv, scripts.shadow_signal_tw" *>> $log
if ($LASTEXITCODE -ne 0) {
    "FATAL: interpreter at $python cannot import the recorder modules (exit $LASTEXITCODE) - dependencies missing; nothing ran and no track row was recorded." | Add-Content $log
    "FATAL: repair with: $python -m pip install --no-build-isolation -e .[dev]" | Add-Content $log
    "shadow exit=126 finished=$(Get-Date -Format o)" | Add-Content $log
    exit 126
}

& $python -m scripts.ingest_public_ohlcv *>> $log
"ingest 0050 exit=$LASTEXITCODE" | Add-Content $log
& $python -m scripts.ingest_us_etf_ohlcv *>> $log
"ingest GLD exit=$LASTEXITCODE" | Add-Content $log
& $python -m scripts.shadow_signal_tw *>> $log
"shadow exit=$LASTEXITCODE finished=$(Get-Date -Format o)" | Add-Content $log

# Repair-command note, measured 2026-09-30: plain `pip install -e .[dev]` made
# zero progress in two separate ~9-minute runs here (it hangs creating the
# isolated build environment); `--no-build-isolation` resolved and installed
# immediately, so that is the form recorded above. `pyarrow` is declared in
# pyproject and its 28 MB wheel stalls on this link in three separate attempts -
# it is imported by nothing (grep over `src/` and `scripts/` returns zero hits
# apart from this comment, which is itself the only match) and is not on the
# recorder path, so it may be skipped when repairing under time pressure.
