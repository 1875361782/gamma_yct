"""注册页短信验证码：发码、校验、频控（进程内存储）。"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from typing import Any

from .sms import SmsSendFailed, build_sms_sender, mask_phone

log = logging.getLogger("gamma.sms_auth")

_PHONE_RE = re.compile(r"^1\d{10}$")
_CODE_TTL_SEC = 5 * 60
_RESEND_SEC = 60
_DAY_LIMIT = 10
_VERIFY_MAX_TRIES = 5

_lock = threading.Lock()
# phone -> {hash, salt, expires, sent_at, day, day_count, tries}
_CODES: dict[str, dict[str, Any]] = {}


def _day_key() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _hash_code(salt: str, code: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def _purge_locked() -> None:
    now = time.time()
    dead = [p for p, row in _CODES.items() if float(row.get("expires") or 0) <= now]
    for p in dead:
        _CODES.pop(p, None)


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-11:]


def is_valid_phone(phone: str) -> bool:
    return bool(_PHONE_RE.match(normalize_phone(phone)))


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def send_login_code(phone: str) -> dict[str, Any]:
    """发送登录验证码。未配置阿里云时：仅服务端日志打印码（演示模式）。"""
    phone = normalize_phone(phone)
    if not is_valid_phone(phone):
        return {"ok": False, "error": "手机号格式不正确", "code": "bad_phone"}

    now = time.time()
    with _lock:
        _purge_locked()
        row = _CODES.get(phone) or {}
        last = float(row.get("sent_at") or 0)
        if last and now - last < _RESEND_SEC:
            wait = int(_RESEND_SEC - (now - last))
            return {
                "ok": False,
                "error": f"发送过于频繁，请 {wait} 秒后再试",
                "code": "rate_limited",
                "retryAfter": wait,
            }
        day = _day_key()
        day_count = int(row.get("day_count") or 0)
        if row.get("day") != day:
            day_count = 0
        if day_count >= _DAY_LIMIT:
            return {
                "ok": False,
                "error": "今日发送次数已达上限，请明日再试",
                "code": "day_limit",
            }

        code = generate_code()
        salt = secrets.token_hex(8)
        _CODES[phone] = {
            "hash": _hash_code(salt, code),
            "salt": salt,
            "expires": now + _CODE_TTL_SEC,
            "sent_at": now,
            "day": day,
            "day_count": day_count + 1,
            "tries": 0,
        }

    sender = build_sms_sender()
    masked = mask_phone(phone)
    if sender is None:
        # 演示/未配置：不投递，验证码打进服务日志（绝不回传前端）
        log.warning(
            "【演示·短信未配置】phone=%s 验证码=%s（5 分钟内有效，请看服务端日志）",
            masked,
            code,
        )
        return {
            "ok": True,
            "demo": True,
            "message": "演示模式：验证码已写入服务端日志（未真实发信）",
            "retryAfter": _RESEND_SEC,
            "expiresIn": _CODE_TTL_SEC,
        }

    try:
        sender.send_code(phone, code)
    except SmsSendFailed as e:
        # 发送失败：清掉刚写入的码，允许立即重试（不占用成功发送的冷却）
        with _lock:
            _CODES.pop(phone, None)
        # 把可行动的排查提示带回前端（不含密钥）
        detail = (e.hint or e.provider_message or "").strip()
        error = "验证码发送失败，请稍后重试"
        if e.provider_code == "isv.OUT_OF_SERVICE":
            error = "短信业务已停用，请在阿里云控制台开通/恢复短信服务后再试"
        elif detail:
            error = f"验证码发送失败：{detail}"
        return {
            "ok": False,
            "error": error,
            "code": "send_failed",
            "providerCode": e.provider_code or None,
            "retryable": e.retryable,
        }
    except Exception:
        log.exception("短信发送异常 phone=%s", masked)
        with _lock:
            _CODES.pop(phone, None)
        return {
            "ok": False,
            "error": "验证码发送失败，请稍后重试",
            "code": "send_failed",
        }

    return {
        "ok": True,
        "demo": False,
        "message": "验证码已发送",
        "retryAfter": _RESEND_SEC,
        "expiresIn": _CODE_TTL_SEC,
    }


def verify_login_code(phone: str, code: str) -> dict[str, Any]:
    phone = normalize_phone(phone)
    code = re.sub(r"\D", "", str(code or ""))[:6]
    if not is_valid_phone(phone):
        return {"ok": False, "error": "手机号格式不正确", "code": "bad_phone"}
    if len(code) != 6:
        return {"ok": False, "error": "请填写 6 位验证码", "code": "bad_code"}

    now = time.time()
    with _lock:
        _purge_locked()
        row = _CODES.get(phone)
        if not row:
            return {
                "ok": False,
                "error": "验证码无效或已过期，请重新获取",
                "code": "expired",
            }
        if float(row.get("expires") or 0) <= now:
            _CODES.pop(phone, None)
            return {
                "ok": False,
                "error": "验证码已过期，请重新获取",
                "code": "expired",
            }
        tries = int(row.get("tries") or 0)
        if tries >= _VERIFY_MAX_TRIES:
            _CODES.pop(phone, None)
            return {
                "ok": False,
                "error": "验证次数过多，请重新获取验证码",
                "code": "too_many_tries",
            }
        expect = str(row.get("hash") or "")
        salt = str(row.get("salt") or "")
        got = _hash_code(salt, code)
        if not hmac.compare_digest(expect, got):
            row["tries"] = tries + 1
            left = _VERIFY_MAX_TRIES - row["tries"]
            return {
                "ok": False,
                "error": f"验证码错误，还可尝试 {left} 次",
                "code": "mismatch",
            }
        _CODES.pop(phone, None)

    return {"ok": True, "message": "验证通过", "phone": phone}
