@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0frontend"

if not exist "node_modules" (
  echo Installing frontend dependencies...
  call npm install --registry=https://mirrors.cloud.tencent.com/npm/
)

echo.
echo Vite dev server: http://localhost:5173  (API proxied to 127.0.0.1:8077)
call npm run dev
pause
