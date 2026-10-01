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

# Each step's code is captured, and each call is preceded by a sentinel. If
# command discovery fails, PowerShell leaves $LASTEXITCODE untouched - and the
# probe above has just set it to 0 - so without the sentinel a vanished
# interpreter reads as a clean run. Measured on 2026-10-01: sentinel present,
# the wrapper exits 125 on an unresolvable call, 0 on a healthy run, 1 when the
# recorder refuses.
$global:LASTEXITCODE = 125
& $python -m scripts.ingest_public_ohlcv *>> $log
$ingestTw = $LASTEXITCODE
"ingest 0050 exit=$ingestTw" | Add-Content $log
$global:LASTEXITCODE = 125
& $python -m scripts.ingest_us_etf_ohlcv *>> $log
$ingestGld = $LASTEXITCODE
"ingest GLD exit=$ingestGld" | Add-Content $log
$global:LASTEXITCODE = 125
& $python -m scripts.shadow_signal_tw *>> $log
$recorderExit = $LASTEXITCODE
"shadow exit=$recorderExit finished=$(Get-Date -Format o)" | Add-Content $log

# Repair-command note, measured 2026-09-30: plain `pip install -e .[dev]` made
# zero progress in two separate ~9-minute runs here (it hangs creating the
# isolated build environment); `--no-build-isolation` resolved and installed
# immediately, so that is the form recorded above. `pyarrow` is declared in
# pyproject and its 28 MB wheel stalls on this link in three separate attempts -
# it is imported by nothing (grep over `src/` and `scripts/` returns zero hits
# apart from this comment, which is itself the only match) and is not on the
# recorder path, so it may be skipped when repairing under time pressure.

# Exit-code propagation, added 2026-10-01 (iteration 72), and the ingest codes
# are propagated for a measured reason rather than for symmetry.
#
# Until today this script ended on an Add-Content, so its process exit code was
# that cmdlet's success and Task Scheduler recorded LastTaskResult 0 whatever
# happened underneath. The module's own docstring claims "the exit code is
# non-zero if any refused, so a scheduled run still shows up as failed" - true
# of scripts/shadow_signal_tw.py, false of the scheduled run, which is the only
# layer anything looks at.
#
# Why the INGEST codes matter here: across the eleven weekly logs that recorded
# anything, exactly one step ever returned non-zero - `ingest 0050 exit=1` on
# 2026-08-01 - and that run is the one where the 0050 sleeve silently lost its
# 2026-07-31 session. TWSE answered the August month request with
# stat='很抱歉，沒有符合條件的資料!' because August had no trading day yet
# (_verification_months starts its cursor at the current month and STOCK_DAY
# returns one calendar month per request), so the candle file never refreshed.
# The recorder then found it 7 days stale against MAX_STALENESS_DAYS = 10,
# which is LARGER than the weekly refresh cadence, so it did not refuse: it
# took the `rows[-1]["date"] >= session` branch, printed "0050: already
# recorded through 2026-07-24" and returned True. Recorder exit 0, wrapper
# exit 0, scheduler LastTaskResult 0, one session gone. Exactly one missed
# refresh is invisible to the staleness guard by construction - the wrapper is
# the only layer that holds the evidence, and until now it threw it away.
# The same Saturday-on-the-first condition recurs 2027-05-01, 2028-01-01,
# 2028-04-01 and 2028-07-01.
#
# A non-zero ingest with a fresh-enough file is a degraded run, not necessarily
# a lost row; the log distinguishes them and this exit code deliberately does
# not. Being loud is the conservative direction on the only evidence stream
# this program can still generate, and a row is never recoverable: the recorder
# appends at most one row per run and never back-fills.
$runExit = 0
if ($null -eq $ingestTw) { $ingestTw = 125 }
if ($null -eq $ingestGld) { $ingestGld = 125 }
if ($null -eq $recorderExit) { $recorderExit = 125 }
if ($ingestTw -ne 0) {
    "FATAL: the 0050 ingest exited $ingestTw - its candle file may not have refreshed; a 7-day-stale file is below the 10-day staleness guard and loses the session silently." | Add-Content $log
    $runExit = $ingestTw
}
if ($ingestGld -ne 0) {
    "FATAL: the GLD ingest exited $ingestGld - same exposure as the 0050 case above." | Add-Content $log
    $runExit = $ingestGld
}
if ($recorderExit -ne 0) {
    "FATAL: the recorder exited $recorderExit - at least one sleeve refused to record and the missed session cannot be back-filled." | Add-Content $log
    $runExit = $recorderExit
}
exit $runExit
