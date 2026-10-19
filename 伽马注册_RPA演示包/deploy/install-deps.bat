@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo Creating venv ...
  py -3 -m venv .venv
  if errorlevel 1 (
    echo FAILED: py -3 -m venv
    exit /b 1
  )
)

echo Upgrading pip ...
".venv\Scripts\python.exe" -m pip install -U pip
echo Installing demo requirements ...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 exit /b 1

if exist "..\gamma-yct\requirements.txt" (
  echo Installing gamma-yct requirements ...
  ".venv\Scripts\python.exe" -m pip install -r ..\gamma-yct\requirements.txt
  if errorlevel 1 exit /b 1
) else (
  echo WARNING: ..\gamma-yct\requirements.txt not found. Sibling layout required.
)

echo Installing Playwright Chromium ...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 exit /b 1

echo OK: deps installed.
endlocal
