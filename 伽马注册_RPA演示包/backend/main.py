"""伽马注册 RPA QR 同步服务 - 启动入口。

启动：
  python -m backend.main
或：
  cd backend && python main.py
"""
from __future__ import annotations
import uvicorn


def main():
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=8766,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()