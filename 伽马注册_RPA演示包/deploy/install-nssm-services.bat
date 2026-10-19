@echo off
setlocal EnableExtensions
REM 需管理员权限。先把 NSSM 的 nssm.exe 放到 PATH，或改下面 NSSM 变量。
REM 默认布局：D:\gamma\伽马注册_RPA演示包 与 D:\gamma\gamma-yct

set "NSSM=nssm"
set "APP_ROOT=D:\gamma\伽马注册_RPA演示包"
set "PY=%APP_ROOT%\.venv\Scripts\python.exe"
set "LOG_DIR=D:\gamma\logs"
set "NGINX_DIR=C:\nginx"
set "NGINX_EXE=%NGINX_DIR%\nginx.exe"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%PY%" (
  echo FAILED: venv python not found: %PY%
  echo Run install-deps.bat first and fix APP_ROOT if needed.
  exit /b 1
)

where %NSSM% >nul 2>&1
if errorlevel 1 (
  echo FAILED: nssm.exe not in PATH. Download from https://nssm.cc/ and add to PATH.
  exit /b 1
)

echo Installing GammaUvicorn ...
%NSSM% stop GammaUvicorn >nul 2>&1
%NSSM% remove GammaUvicorn confirm >nul 2>&1
%NSSM% install GammaUvicorn "%PY%"
%NSSM% set GammaUvicorn AppDirectory "%APP_ROOT%"
%NSSM% set GammaUvicorn AppParameters -m uvicorn backend.app:app --host 127.0.0.1 --port 8766 --log-level info
%NSSM% set GammaUvicorn AppEnvironmentExtra GAMMA_HEADLESS=1
%NSSM% set GammaUvicorn AppStdout "%LOG_DIR%\uvicorn.out.log"
%NSSM% set GammaUvicorn AppStderr "%LOG_DIR%\uvicorn.err.log"
%NSSM% set GammaUvicorn AppRotateFiles 1
%NSSM% set GammaUvicorn AppRotateBytes 10485760
%NSSM% set GammaUvicorn Start SERVICE_AUTO_START
%NSSM% start GammaUvicorn

if exist "%NGINX_EXE%" (
  echo Installing GammaNginx ...
  %NSSM% stop GammaNginx >nul 2>&1
  %NSSM% remove GammaNginx confirm >nul 2>&1
  %NSSM% install GammaNginx "%NGINX_EXE%"
  %NSSM% set GammaNginx AppDirectory "%NGINX_DIR%"
  %NSSM% set GammaNginx AppStdout "%LOG_DIR%\nginx.out.log"
  %NSSM% set GammaNginx AppStderr "%LOG_DIR%\nginx.err.log"
  %NSSM% set GammaNginx Start SERVICE_AUTO_START
  %NSSM% start GammaNginx
) else (
  echo SKIP GammaNginx: %NGINX_EXE% not found. Install Nginx and re-run, or start nginx manually.
)

echo Done. Check: curl http://127.0.0.1:8766/health
endlocal
