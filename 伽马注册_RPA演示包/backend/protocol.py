"""WebSocket 协议消息工厂。

借鉴 MediaCrawler 中"服务端截图 base64 + 调用方渲染"模式。
"""
from __future__ import annotations
import json
import time
from typing import Optional


def msg_bound(sid: str, expires_at: Optional[float] = None) -> str:
    return json.dumps({
        "type": "bound",
        "sid": sid,
        "expires_at": expires_at or time.time() + 600,
    }, ensure_ascii=False)


def msg_qr_update(qr_b64: str, remaining_seconds: int = 180,
                  captured_at: Optional[float] = None) -> str:
    return json.dumps({
        "type": "qr_update",
        "qr": qr_b64,
        "remaining_seconds": remaining_seconds,
        "captured_at": captured_at or time.time(),
    }, ensure_ascii=False)


def msg_qr_refreshing() -> str:
    return json.dumps({"type": "qr_refreshing"}, ensure_ascii=False)


def msg_step(step: str, message: str, extra: Optional[dict] = None) -> str:
    data = {"type": "step", "step": step, "message": message, "ts": time.time()}
    if extra:
        data.update(extra)
    return json.dumps(data, ensure_ascii=False)


def msg_screenshot(b64: str) -> str:
    return json.dumps({
        "type": "screenshot",
        "image": b64,
        "ts": time.time(),
    }, ensure_ascii=False)


def msg_login_success(sid: str, user: str = "法人") -> str:
    return json.dumps({
        "type": "login_success",
        "sid": sid,
        "user": user,
    }, ensure_ascii=False)


def msg_login_failed(reason: str) -> str:
    return json.dumps({"type": "login_failed", "reason": reason},
                     ensure_ascii=False)


def msg_error(message: str) -> str:
    return json.dumps({"type": "error", "message": message},
                     ensure_ascii=False)


def msg_pong() -> str:
    return json.dumps({"type": "pong"}, ensure_ascii=False)