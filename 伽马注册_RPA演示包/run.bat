@echo off
REM ============================================================
REM  伽马注册 RPA 扫码登录演示 · Windows 一键启动
REM  - 自建 .venv（不污染系统 Python）
REM  - 安装依赖 + Playwright Chromium（首次需联网）
REM  - 启动 FastAPI 服务（端口 8766）
REM
REM  用法：
REM    run.bat              # 默认启动（真实政务网）
REM    run.bat --no-browser # 仅起服务，不装 Chromium（纯 mock 离线演示）
REM
REM  前置：已安装 Python 3.10+ 并勾选 "Add python.exe to PATH"
REM ============================================================
cd /d "%~dp0"

REM 让 Chromium 安装进 .venv 内，整包可跨同架构 Windows 离线拷贝
set PLAYWRIGHT_BROWSERS_PATH=0

REM 选择 Python 解释器（python 优先，否则回退到 py 启动器）
set PY=python
%PY% --version >nul 2>&1
if errorlevel 1 (
  set PY=py -3
  %PY% --version >nul 2>&1
  if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.10+ 并勾选 "Add to PATH"。
    pause
    exit /b 1
  )
)

REM 1. 创建虚拟环境
if not exist .venv (
  echo -^> 创建虚拟环境 .venv ...
  %PY% -m venv .venv
)

REM 2. 激活并安装 Python 依赖
call .venv\Scripts\activate.bat
echo -^> 安装 Python 依赖（fastapi / uvicorn / websockets / playwright）...
python -m pip install --upgrade pip
pip install -q -r backend\requirements.txt

REM 3. 安装 / 校验 Playwright Chromium（真实模式必需）
if "%1"=="--no-browser" (
  echo -^> 跳过 Chromium 安装（--no-browser）。纯 mock 演示可运行。
) else (
  echo -^> 安装 / 校验 Playwright Chromium（首次需联网，约 150MB）...
  python -m playwright install chromium
)

REM 4. 启动服务（前台运行，请勿关闭此窗口）
echo.
echo ==================================================
echo   伽马注册 RPA 扫码登录演示
echo ==================================================
echo   落地页:  http://localhost:8766/
echo   A 页:    http://localhost:8766/acquire
echo   B 页:    http://localhost:8766/qrlogin
echo   健康检查: curl http://localhost:8766/health
echo ==================================================
echo.
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8766 --log-level info
