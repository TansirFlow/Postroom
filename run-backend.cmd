@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating virtual environment...
  python -m venv .venv
  echo [2/2] Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo.
echo Starting Postroom...
".venv\Scripts\python.exe" run.py
pause
