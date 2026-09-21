@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0frontend"

if not exist "node_modules" (
  echo Installing frontend dependencies...
  call npm install --registry=https://mirrors.cloud.tencent.com/npm/
)

echo.
echo Building frontend into frontend\dist ...
call npm run build
echo.
echo Done. Restart the backend to serve the built console at http://127.0.0.1:8077/
pause
