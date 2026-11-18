@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See docs/cross-platform.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u camera_preview.py %*
set "result=%errorlevel%"
pause
exit /b %result%
