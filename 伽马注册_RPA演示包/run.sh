#!/usr/bin/env bash
# =====================================================================
#  伽马注册 RPA 扫码登录演示 · 一键启动脚本
#  - 自建 .venv（不污染系统 Python）
#  - 自动安装依赖 + Playwright Chromium
#  - 启动 FastAPI 服务（默认端口 8766）
#
#  用法：
#    bash run.sh              # 默认启动（真实政务网 + mock 兜底）
#    bash run.sh mock         # 强制全局 mock 提示（页面仍可在 URL 加 ?mock=true）
#    bash run.sh --no-browser # 仅起服务，不检查/安装 Chromium（纯 mock 演示用）
# =====================================================================
set -e

cd "$(dirname "$0")"

VENV=".venv"
PYTHON_BIN="python3"

# 1. 创建虚拟环境
if [ ! -d "$VENV" ]; then
  echo "→ 创建虚拟环境 $VENV ..."
  "$PYTHON_BIN" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"

# 2. 安装 Python 依赖
echo "→ 安装 Python 依赖（fastapi / uvicorn / websockets / playwright）..."
pip install -q --upgrade pip
pip install -q -r backend/requirements.txt

# 3. 安装 / 校验 Playwright Chromium（真实模式必需）
if [[ "$1" != "--no-browser" ]]; then
  echo "→ 校验 Playwright Chromium ..."
  if ! python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.launch(headless=True).close(); p.stop()" 2>/dev/null; then
    echo "  未检测到 Chromium，开始安装（约 150MB，需联网）..."
    python -m playwright install chromium
  else
    echo "  Chromium 已就绪。"
  fi
else
  echo "→ 跳过浏览器检查（--no-browser）。纯 mock 演示可正常运行。"
fi

# 4. 启动服务
echo ""
echo "=================================================="
echo "  伽马注册 RPA 扫码登录演示"
echo "=================================================="
echo "  落地页:  http://localhost:8766/"
echo "  A 页:    http://localhost:8766/acquire"
echo "  B 页:    http://localhost:8766/qrlogin"
echo "  健康检查: curl http://localhost:8766/health"
echo "=================================================="
echo ""
exec python -m uvicorn backend.app:app --host 0.0.0.0 --port 8766 --log-level info
