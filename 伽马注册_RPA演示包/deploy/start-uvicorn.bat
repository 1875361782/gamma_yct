@echo off
setlocal
cd /d "%~dp0.."
if not exist "D:\gamma\logs" mkdir "D:\gamma\logs" 2>nul
if not exist "logs" mkdir "logs" 2>nul

REM 生产默认无头；本机调试可在 backend\.env 写 GAMMA_HEADLESS=0
if "%GAMMA_HEADLESS%"=="" set GAMMA_HEADLESS=1

echo Starting uvicorn on 127.0.0.1:8766 (GAMMA_HEADLESS=%GAMMA_HEADLESS%) ...
".venv\Scripts\python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port 8766 --log-level info
endlocal
