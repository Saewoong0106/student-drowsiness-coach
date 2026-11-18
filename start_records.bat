@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
    echo Create the Python 3.11 .venv first. See docs/records-ui.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" records_app.py %*
if errorlevel 1 pause
