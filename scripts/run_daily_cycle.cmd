@echo off
rem Daily signal cycle (Goal O gate 6): fetch latest close, decide, notify, score.
rem Idempotent: safe to re-run any number of times per day.
cd /d D:\Crypto-Trading || exit /b 1
if not exist data\runtime mkdir data\runtime
echo ==== daily cycle %date% %time% ==== >> data\runtime\daily_cycle.log
.\.venv\Scripts\python.exe -m scripts.run_paper_runtime --once >> data\runtime\daily_cycle.log 2>&1
