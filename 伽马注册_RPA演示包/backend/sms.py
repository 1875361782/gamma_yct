"""阿里云短信出口（自 短信验证/sms.py 适配，去掉 af_core 依赖）。

只调 SendSms；RPC 签名自实现，不引阿里云 SDK。
环境变量（与 AF_SMS_* 兼容，也认 GAMMA_SMS_*）：
  GAMMA_SMS_ACCESS_KEY_ID / AF_SMS_ACCESS_KEY_ID
  GAMMA_SMS_ACCESS_KEY_SECRET / AF_SMS_ACCESS_KEY_SECRET
  GAMMA_SMS_SIGN_NAME / AF_SMS_SIGN_NAME
  GAMMA_SMS_TEMPLATE_CODE / AF_SMS_TEMPLATE_CODE
  可选：GAMMA_SMS_ENDPOINT、GAMMA_SMS_REGION_ID、GAMMA_SMS_TIMEOUT
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

log = logging.getLogger("gamma.sms")

__all__ = [
    "AliyunSmsSender",
    "SmsSendFailed",
    "build_sms_sender",
    "mask_phone",
]

_API_VERSION = "2017-05-25"
_ACTION = "SendSms"

_PERMANENT_CODES = frozenset(
    {
        "isv.SMS_SIGNATURE_ILLEGAL",
        "isv.SMS_TEMPLATE_ILLEGAL",
        "isv.MOBILE_NUMBER_ILLEGAL",
        "isv.AMOUNT_NOT_ENOUGH",
        "isv.BUSINESS_LIMIT_CONTROL",
        "isv.DAY_LIMIT_CONTROL",
        "isv.OUT_OF_SERVICE",
        "InvalidAccessKeyId.NotFound",
        "SignatureDoesNotMatch",
        "Forbidden.RAM",
        "Forbidden",
        "InvalidAccessKeyId.Inactive",
        "SecurityToken.Expired",
    }
)

_CODE_HINTS: dict[str, str] = {
    "isv.SMS_SIGNATURE_ILLEGAL": "签名不存在或未过审，核对 SIGN_NAME 是否逐字一致",
    "isv.SMS_TEMPLATE_ILLEGAL": "模板不存在或未过审；须为验证码类型模板",
    "isv.TEMPLATE_MISSING_PARAMETERS": '模板变量须为 ${code}，本平台固定传 {"code": ...}',
    "isv.MOBILE_NUMBER_ILLEGAL": "号码格式被阿里云判非法",
    "isv.AMOUNT_NOT_ENOUGH": "账户余额不足",
    "isv.BUSINESS_LIMIT_CONTROL": "撞阿里云频控（同号 1 分钟 1 条等）",
    "isv.DAY_LIMIT_CONTROL": "撞阿里云按天频控",
    "isv.OUT_OF_SERVICE": "短信业务已停用：到阿里云控制台开通/恢复「短信服务」，并确认签名与模板仍有效",
    "InvalidAccessKeyId.NotFound": "AccessKey ID 不存在",
    "SignatureDoesNotMatch": "Secret 可能抄错或带空格，或机器时钟偏差过大",
    "Forbidden.RAM": "RAM 用户缺少短信权限",
    "Forbidden": "AccessKey 被拒绝/禁用，需轮换",
    "InvalidAccessKeyId.Inactive": "AccessKey 已禁用，需轮换",
}


def _hint_for(provider_code: str) -> str:
    return _CODE_HINTS.get(provider_code, "未登记的错误码，拿 request_id 去阿里云工单问")


def mask_phone(phone: str) -> str:
    value = (phone or "").strip()
    return f"{value[:3]}****{value[-4:]}" if len(value) >= 7 else value


class SmsSendFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider_code: str = "",
        provider_message: str = "",
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.provider_message = provider_message
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.provider_code not in _PERMANENT_CODES

    @property
    def hint(self) -> str:
        return _hint_for(self.provider_code)


def _percent(value: Any) -> str:
    return quote(str(value), safe="")


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return default


@dataclass(frozen=True)
class AliyunSmsSender:
    access_key_id: str
    access_key_secret: str
    sign_name: str
    template_code: str
    endpoint: str = "https://dysmsapi.aliyuncs.com"
    region_id: str = "cn-hangzhou"
    timeout: float = 10.0

    def describe(self) -> str:
        key_id = self.access_key_id
        return (
            f"endpoint={self.endpoint} region={self.region_id} "
            f"sign_name={self.sign_name!r} template={self.template_code!r} "
            f"access_key_id={key_id[:8]}…(len={len(key_id)}) "
            f"secret_len={len(self.access_key_secret)} timeout={self.timeout}s"
        )

    def send_code(self, phone: str, code: str) -> dict[str, Any]:
        params = {
            "PhoneNumbers": phone,
            "SignName": self.sign_name,
            "TemplateCode": self.template_code,
            "TemplateParam": json.dumps({"code": code}, ensure_ascii=False),
        }
        body = self._signed_form(params)
        masked = mask_phone(phone)

        import httpx

        started = time.monotonic()
        try:
            response = httpx.post(
                self.endpoint,
                content=body.encode("utf-8"),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"
                },
                timeout=self.timeout,
            )
        except Exception as exc:
            elapsed_ms = (time.monotonic() - started) * 1000
            log.error(
                "短信网关不可达 phone=%s 耗时=%.0fms 异常=%s: %s | %s",
                masked,
                elapsed_ms,
                type(exc).__name__,
                exc,
                self.describe(),
            )
            raise SmsSendFailed(f"短信网关不可达：{type(exc).__name__}") from exc

        elapsed_ms = (time.monotonic() - started) * 1000
        try:
            payload = response.json()
        except Exception:
            body_head = (response.text or "")[:500]
            log.error(
                "短信网关返回了无法解析的响应 phone=%s http=%s 耗时=%.0fms 正文=%r | %s",
                masked,
                response.status_code,
                elapsed_ms,
                body_head,
                self.describe(),
            )
            raise SmsSendFailed(
                f"短信网关返回了无法解析的响应（HTTP {response.status_code}）"
            ) from None

        request_id = str(payload.get("RequestId") or "")
        provider_code = str(payload.get("Code") or "")

        if provider_code != "OK":
            provider_message = str(payload.get("Message") or "")
            log.error(
                "短信发送失败 phone=%s code=%s message=%s http=%s 耗时=%.0fms "
                "request_id=%s 可重试=%s | %s | 排查：%s",
                masked,
                provider_code,
                provider_message,
                response.status_code,
                elapsed_ms,
                request_id,
                provider_code not in _PERMANENT_CODES,
                self.describe(),
                _hint_for(provider_code),
            )
            raise SmsSendFailed(
                "短信发送失败",
                provider_code=provider_code,
                provider_message=provider_message,
                request_id=request_id,
            )

        biz_id = str(payload.get("BizId") or "")
        log.info(
            "短信已提交 phone=%s biz_id=%s request_id=%s 耗时=%.0fms | %s",
            masked,
            biz_id,
            request_id,
            elapsed_ms,
            self.describe(),
        )
        return {
            "submitted": True,
            "provider_message_id": biz_id,
            "request_id": request_id,
        }

    def _signed_form(self, params: dict[str, str]) -> str:
        common = {
            "AccessKeyId": self.access_key_id,
            "Action": _ACTION,
            "Format": "JSON",
            "RegionId": self.region_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": f"{uuid.uuid4()}-{secrets.token_hex(8)}",
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": _API_VERSION,
        }
        merged = {**common, **params}
        canonical = "&".join(
            f"{_percent(k)}={_percent(v)}" for k, v in sorted(merged.items())
        )
        string_to_sign = f"POST&{_percent('/')}&{_percent(canonical)}"
        signature = base64.b64encode(
            hmac.new(
                f"{self.access_key_secret}&".encode(),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("ascii")
        return f"Signature={_percent(signature)}&{canonical}"


def build_sms_sender() -> AliyunSmsSender | None:
    access_key_id = _env("GAMMA_SMS_ACCESS_KEY_ID", "AF_SMS_ACCESS_KEY_ID")
    access_key_secret = _env(
        "GAMMA_SMS_ACCESS_KEY_SECRET", "AF_SMS_ACCESS_KEY_SECRET"
    )
    sign_name = _env("GAMMA_SMS_SIGN_NAME", "AF_SMS_SIGN_NAME")
    template_code = _env("GAMMA_SMS_TEMPLATE_CODE", "AF_SMS_TEMPLATE_CODE")
    if not (access_key_id and access_key_secret and sign_name and template_code):
        return None
    return AliyunSmsSender(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        sign_name=sign_name,
        template_code=template_code,
        endpoint=_env(
            "GAMMA_SMS_ENDPOINT",
            "AF_SMS_ENDPOINT",
            default="https://dysmsapi.aliyuncs.com",
        ),
        region_id=_env(
            "GAMMA_SMS_REGION_ID", "AF_SMS_REGION_ID", default="cn-hangzhou"
        ),
        timeout=float(
            _env("GAMMA_SMS_TIMEOUT", "AF_SMS_TIMEOUT_SECONDS", default="10") or "10"
        ),
    )
