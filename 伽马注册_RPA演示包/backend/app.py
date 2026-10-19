"""FastAPI 应用：REST 健康检查 + WebSocket /ws/{sid} + H5 页面托管。

两页演示：
  - /acquire   : A 页（二维码获取页），展示 RPA 实时截图流 + 操作日志
  - /qrlogin   : B 页（扫码登录页），手机 H5 风格，展示同步过来的 QR
  - /ws/{sid}  : WebSocket，A/B 两页绑定同 sid，服务端广播事件
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, Form, UploadFile, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

from .sessions import SessionManager
from .rpa.browser import Listener
from .protocol import (
    msg_bound, msg_qr_update,
    msg_login_success, msg_error, msg_pong,
    msg_step, msg_screenshot,
)
from .relay import relay_hub
from . import gamaz_proxy


# ---------- 配置 ----------
BACKEND_DIR = Path(__file__).resolve().parent
MOCK_GOV_DIR = BACKEND_DIR / "mock_gov"


def _load_dotenv_file(path: Path) -> None:
    """轻量加载 backend/.env（不覆盖已有环境变量）。"""
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


_load_dotenv_file(BACKEND_DIR / ".env")
_load_dotenv_file(BACKEND_DIR.parent / ".env")

# 真实场景：从上海企业登记在线门户开始
REAL_PORTAL_URL = "https://yct.sh.gov.cn/portal_yct/"
# mock 场景：直接进入 mock 登录页（保留旧 mock 用于离线验证）
MOCK_GOV_URL = "/mock/index.html"

def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# 外网简易鉴权：环境变量 GAMMA_API_TOKEN 为共享口令（可选）；
# 无论是否配置，页面都会通过 /api/bootstrap 领取短时 session token。
GAMMA_API_TOKEN = (os.environ.get("GAMMA_API_TOKEN") or "").strip()
# 本机调试才允许打开 A 页监控：GAMMA_ALLOW_MONITOR=1 或来自 localhost
GAMMA_ALLOW_MONITOR = _env_flag("GAMMA_ALLOW_MONITOR", default=False)
# 管理页口令：未设置时仅本机可调 /api/admin/*；设置后公网也可凭 X-Gamma-Admin-Token 访问
GAMMA_ADMIN_TOKEN = (os.environ.get("GAMMA_ADMIN_TOKEN") or "").strip()
# 生产默认无头；本机要看 Chrome 窗时在 .env 设 GAMMA_HEADLESS=0
GAMMA_HEADLESS = _env_flag("GAMMA_HEADLESS", default=True)
_BOOTSTRAP_SECRET = (os.environ.get("GAMMA_BOOTSTRAP_SECRET") or secrets.token_hex(16)).encode("utf-8")
_SESSION_TOKENS: dict[str, float] = {}  # token -> expires_at epoch
_SESSION_TOKEN_TTL_SEC = 4 * 3600

app = FastAPI(title="伽马注册 RPA QR 同步服务", version="0.2.0")
sessions = SessionManager(ttl_seconds=600)

# 静态文件：mock 政务网登录页（用于离线开发/演示）
app.mount("/mock", StaticFiles(directory=str(MOCK_GOV_DIR)), name="mock")
# 演示静态资源（人工客服二维码等）
_STATIC_ASSETS = BACKEND_DIR / "static"
_STATIC_ASSETS.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_ASSETS)), name="static")


def _purge_expired_tokens() -> None:
    now = time.time()
    dead = [t for t, exp in _SESSION_TOKENS.items() if exp <= now]
    for t in dead:
        _SESSION_TOKENS.pop(t, None)


def _mint_session_token() -> tuple[str, float]:
    _purge_expired_tokens()
    raw = secrets.token_urlsafe(24)
    sig = hmac.new(_BOOTSTRAP_SECRET, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    token = f"{raw}.{sig}"
    expires = time.time() + _SESSION_TOKEN_TTL_SEC
    _SESSION_TOKENS[token] = expires
    return token, expires


def _token_ok(token: str | None) -> bool:
    if not token:
        return False
    token = str(token).strip()
    if GAMMA_API_TOKEN and hmac.compare_digest(token, GAMMA_API_TOKEN):
        return True
    _purge_expired_tokens()
    exp = _SESSION_TOKENS.get(token)
    return bool(exp and exp > time.time())


def _extract_api_token(request: Request, body: dict | None = None) -> str | None:
    header = request.headers.get("x-gamma-token") or request.headers.get("X-Gamma-Token")
    if header:
        return header.strip()
    if body and isinstance(body, dict):
        t = body.get("token") or body.get("apiToken")
        if t:
            return str(t).strip()
    return None


def _require_api_token(request: Request, body: dict | None = None):
    """启动/取消等写接口需要 bootstrap token 或 GAMMA_API_TOKEN。"""
    if _token_ok(_extract_api_token(request, body)):
        return None
    return JSONResponse(
        {"ok": False, "error": "unauthorized", "message": "缺少或无效的访问令牌，请刷新页面重试"},
        status_code=401,
    )


def _client_is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


def _require_admin(request: Request, body: dict | None = None):
    """管理接口：本机放行；非本机需 GAMMA_ADMIN_TOKEN。"""
    if _client_is_local(request):
        return None
    if not GAMMA_ADMIN_TOKEN:
        return JSONResponse(
            {
                "ok": False,
                "error": "admin locked",
                "message": "管理接口未配置 GAMMA_ADMIN_TOKEN，仅本机可访问",
            },
            status_code=403,
        )
    token = request.headers.get("x-gamma-admin-token") or request.headers.get("X-Gamma-Admin-Token")
    if not token and body:
        token = str(body.get("adminToken") or body.get("token") or "").strip()
    if token and hmac.compare_digest(str(token).strip(), GAMMA_ADMIN_TOKEN):
        return None
    return JSONResponse(
        {"ok": False, "error": "unauthorized", "message": "需要有效的管理口令"},
        status_code=401,
    )


# ---------- 页面路由 ----------
# 9 屏原型（同源托管，屏 6 内嵌 /qrlogin 同域，避免 iframe cross-origin）
PROTOTYPE_HTML = BACKEND_DIR / "static_prototype.html"
ADMIN_HTML = BACKEND_DIR / "static_admin.html"


def _serve_prototype():
    """首页与 /prototype 均返回 9 屏产品原型。"""
    if not PROTOTYPE_HTML.exists():
        return JSONResponse({"error": "prototype not found (expected backend/static_prototype.html)"}, status_code=404)
    return FileResponse(
        PROTOTYPE_HTML,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/", include_in_schema=False)
async def index():
    """首页：直接进入 9 屏产品原型。"""
    return _serve_prototype()


@app.get("/prototype", include_in_schema=False)
async def prototype():
    """兼容旧入口 /prototype。"""
    return _serve_prototype()


@app.get("/admin", include_in_schema=False)
async def admin_page():
    """任务与 RPA 进度管理页。"""
    if not ADMIN_HTML.exists():
        return JSONResponse({"error": "admin page not found"}, status_code=404)
    return FileResponse(
        ADMIN_HTML,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/acquire", include_in_schema=False)
async def acquire(request: Request):
    """A 页监控：仅本机或显式开启 GAMMA_ALLOW_MONITOR 时可用（不对公网产品用户开放）。"""
    if not (GAMMA_ALLOW_MONITOR or _client_is_local(request)):
        return JSONResponse(
            {"ok": False, "error": "monitor disabled", "message": "监控页仅本机调试可用"},
            status_code=404,
        )
    path = BACKEND_DIR / "static_acquire.html"
    if not path.exists():
        return JSONResponse({"error": "acquire page not found"}, status_code=404)
    return FileResponse(path, media_type="text/html")


@app.get("/api/bootstrap")
async def api_bootstrap():
    """产品页加载时领取短时 API token（外网访问写接口用）。"""
    token, expires = _mint_session_token()
    from .sms import build_sms_sender

    return {
        "ok": True,
        "token": token,
        "expiresAt": int(expires),
        "authRequired": True,
        "sharedTokenConfigured": bool(GAMMA_API_TOKEN),
        "smsConfigured": build_sms_sender() is not None,
    }


@app.post("/api/auth/sms/send")
async def api_sms_send(request: Request, body: dict):
    """注册页：发送短信验证码。"""
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    from . import sms_auth

    phone = str((body or {}).get("phone") or "").strip()
    result = await asyncio.to_thread(sms_auth.send_login_code, phone)
    status = 200 if result.get("ok") else 400
    if result.get("code") == "rate_limited":
        status = 429
    return JSONResponse(result, status_code=status)


@app.post("/api/auth/sms/verify")
async def api_sms_verify(request: Request, body: dict):
    """注册页：校验短信验证码（进入企业信息前）。"""
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    from . import sms_auth

    phone = str((body or {}).get("phone") or "").strip()
    code = str((body or {}).get("code") or "").strip()
    result = await asyncio.to_thread(sms_auth.verify_login_code, phone, code)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.get("/qrlogin", include_in_schema=False)
async def qrlogin():
    """B 页：独立的「扫码登录」手机 H5 单页（产品内嵌隐藏 iframe，勿单独外发）。"""
    qr_path = BACKEND_DIR / "static_qrlogin.html"
    if not qr_path.exists():
        return JSONResponse({"error": "QR login page not found"}, status_code=404)
    return FileResponse(
        qr_path,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ---------- REST ----------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "gamma-register-qr-sync",
        "version": "0.2.0",
        "active_sessions": await sessions.list_active(),
        "real_portal_url": REAL_PORTAL_URL,
        "mock_gov_url": MOCK_GOV_URL,
        "pages": {
            "index": "http://localhost:8766/",
            "prototype": "http://localhost:8766/prototype",
            "admin": "http://localhost:8766/admin",
            "qrlogin": "http://localhost:8766/qrlogin",
            "acquire": "local-only unless GAMMA_ALLOW_MONITOR=1",
        },
        "headless": GAMMA_HEADLESS,
        "headless_forced": GAMMA_HEADLESS,
        "cases_static_exposed": False,
        "admin_token_configured": bool(GAMMA_ADMIN_TOKEN),
        "public_tunnel_doc": "docs/PUBLIC_TUNNEL.md",
        "deploy_doc": "docs/ALIYUN_WINDOWS_DEPLOY.md",
    }


REPO_ROOT = BACKEND_DIR.parents[1]  # …/伽马账（backend → 演示包 → 仓库根）
GAMMA_ROOT = REPO_ROOT / "gamma-yct"
GAMMA_CASES_DIR = GAMMA_ROOT / "data" / "cases"
DEFAULT_CASE_ID = "GM202608062231EF"

_STAGE_FILES = [
    ("name", "fill-name-result.json", "名称信息"),
    ("basic", "fill-basic-info-result.json", "基本信息"),
    ("capital", "fill-capital-result.json", "出资情况"),
    ("personnel", "fill-personnel-result.json", "人员信息"),
    ("opening", "fill-opening-result.json", "企业开办"),
]

FILL_OK_STATUSES = frozenset(
    {
        "ready_for_user_submit",
        "name_next_reached",
        "signing",
        "awaiting_alipay_qr",
        "awaiting_sign_face_qr",
        "sign_done",
    }
)
PAST_QR_STATUSES = frozenset(
    {
        "logged_in",
        "name_checking",
        "name_failed",
        "human_confirmation_required",
        "name_next_reached",
        "filling_establishment",
        "choosing_process",
        "filling_addons",
        "ready_for_user_submit",
        "signing",
        "awaiting_alipay_qr",
        "awaiting_sign_face_qr",
        "sign_done",
        "need_human",
        "offline_required",
        "failed",
    }
)


def _ensure_gamma_path() -> str:
    """把 gamma-yct 挂到 sys.path，返回其绝对路径字符串。"""
    import sys

    root = str(GAMMA_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _read_json_file(path: Path) -> dict:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _case_bucket(row: dict[str, Any]) -> str:
    """运维看板状态分桶。"""
    st = str(row.get("caseStatus") or "")
    if row.get("running"):
        return "running"
    if row.get("nameFailed") or st == "name_failed":
        return "name_failed"
    if row.get("needsHuman"):
        return "need_human"
    if st == "sign_done" or row.get("signDone"):
        return "sign_done"
    if st in ("ready_for_user_submit", "awaiting_alipay_qr", "awaiting_sign_face_qr", "signing"):
        return "fill_done"
    if st in ("failed", "cancelled") or (row.get("finished") and row.get("error") and not row.get("fillOk")):
        return "failed"
    if st in ("draft", ""):
        return "draft"
    if row.get("fillOk") or row.get("stage") == "done":
        return "fill_done"
    return "in_progress"


def _case_summary(case_id: str, job=None) -> dict[str, Any]:
    """汇总单个案件的提交信息与 RPA 进度（供管理页）。"""
    case_id = (case_id or "").strip()
    case_dir = GAMMA_CASES_DIR / case_id if case_id else None
    meta = _read_json_file(case_dir / "meta.json") if case_dir else {}
    payload = _read_json_file(case_dir / "payload.json") if case_dir else {}
    name = payload.get("name") if isinstance(payload.get("name"), dict) else {}
    reg = payload.get("registration") if isinstance(payload.get("registration"), dict) else {}
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    company = payload.get("company") if isinstance(payload.get("company"), dict) else {}
    personnel = payload.get("personnel") if isinstance(payload.get("personnel"), dict) else {}
    legal = (
        personnel.get("legalRepresentative")
        if isinstance(personnel.get("legalRepresentative"), dict)
        else {}
    )
    addr = reg.get("address") if isinstance(reg.get("address"), dict) else {}

    case_status = str(meta.get("status") or "")
    customer_message = str(meta.get("customerMessage") or "")
    reason = str(meta.get("reason") or "")
    updated_at = str(meta.get("updatedAt") or meta.get("createdAt") or "")
    created_at = str(meta.get("createdAt") or "")

    stages_done: list[str] = []
    if case_dir and case_dir.exists():
        for key, fname, _label in _STAGE_FILES:
            if (case_dir / fname).exists():
                stages_done.append(key)

    stage = "pending"
    stage_label = "等待开始"
    if len(stages_done) >= len(_STAGE_FILES):
        stage = "done"
        stage_label = "已完成各板块"
    else:
        for key, _fname, label in _STAGE_FILES:
            if key not in stages_done:
                stage = key
                stage_label = label
                break

    progress = customer_message or ""
    running = bool(job and job.thread and job.thread.is_alive())
    logged_in = bool(job and job.logged_in)
    has_qr = bool(job and job.qr_b64)
    finished = bool(job.finished) if job else case_status not in ("", "draft", "queued")
    if job and not progress:
        if not logged_in and running:
            progress = "等待扫码登录…"
        elif logged_in and stage == "pending" and not stages_done:
            progress = "登录成功，正在进入名称申报…"
        elif stage != "done" and stage != "pending":
            progress = f"正在填写「{stage_label}」"
        elif not progress:
            progress = "正在同步填报进度…"
    if not progress:
        progress = reason or case_status or "—"

    name_failed = case_status == "name_failed" or any(
        k in reason for k in ("比对未通过", "名称未通过", "生僻字")
    )
    if name_failed:
        progress = customer_message or reason or "核名不通过，请返回修改字号并重试"
        if "核名不通过" not in progress and "生僻字" not in progress:
            progress = "核名不通过：" + progress

    trade = str(name.get("tradeName") or "").strip()
    preview = str(name.get("fullNamePreview") or "").strip()
    if not preview and trade:
        preview = f"上海{trade}{name.get('industry') or ''}{name.get('orgForm') or ''}"

    legal_name = str(legal.get("name") or "").strip()
    legal_mobile = str(legal.get("mobile") or legal.get("phone") or "").strip()
    contact_phone = str(
        contact.get("mobile")
        or contact.get("phone")
        or payload.get("authPhone")
        or meta.get("authPhone")
        or legal_mobile
        or ""
    ).strip()

    mtime = 0.0
    ctime = 0.0
    if case_dir and case_dir.exists():
        try:
            meta_path = case_dir / "meta.json"
            if meta_path.exists():
                mtime = meta_path.stat().st_mtime
                ctime = meta_path.stat().st_ctime
            else:
                mtime = case_dir.stat().st_mtime
                ctime = case_dir.stat().st_ctime
        except Exception:
            mtime = 0.0
    if job and getattr(job, "started_at", None):
        mtime = max(mtime, float(job.started_at))

    fill_ok = bool(job and job.ok) or case_status in FILL_OK_STATUSES
    if name_failed:
        fill_ok = False
    sign_done = case_status == "sign_done" or (
        bool(job) and getattr(job, "sign_phase", None) == "done" and bool(job.ok)
    )

    row = {
        "caseId": case_id,
        "sid": getattr(job, "sid", "") if job else "",
        "tradeName": trade,
        "companyName": preview,
        "orgForm": str(name.get("orgForm") or ""),
        "industry": str(name.get("industry") or ""),
        "district": str(reg.get("district") or addr.get("district") or ""),
        "addressDetail": str(addr.get("detail") or ""),
        "capital": company.get("registeredCapital"),
        "legalName": legal_name,
        "legalMobile": legal_mobile,
        "contactPhone": contact_phone,
        "authPhone": str(payload.get("authPhone") or meta.get("authPhone") or "").strip(),
        "caseStatus": case_status,
        "progress": progress,
        "customerMessage": customer_message,
        "reason": reason,
        "stage": stage,
        "stageLabel": stage_label,
        "stagesDone": stages_done,
        "running": running,
        "loggedIn": logged_in,
        "hasQr": has_qr,
        "finished": bool(finished) if job else bool(case_status and case_status not in ("draft",)),
        "fillOk": fill_ok,
        "signDone": sign_done,
        "nameFailed": name_failed,
        "needsHuman": (not name_failed)
        and case_status in ("need_human", "human_confirmation_required"),
        "error": (job.error if job else None)
        or (reason if name_failed or case_status == "failed" else None),
        "updatedAt": updated_at,
        "createdAt": created_at,
        "startedAt": float(job.started_at) if job else None,
        "mtime": mtime,
        "ctime": ctime,
        "source": "relay" if job else "case",
        "signPhase": getattr(job, "sign_phase", None) if job else None,
    }
    row["bucket"] = _case_bucket(row)
    return row


def _build_daily_series(rows: list[dict[str, Any]], days: int = 14) -> list[dict[str, Any]]:
    """按本地日历日统计提交量（用 ctime/mtime）。"""
    import datetime as _dt

    today = _dt.date.today()
    buckets: dict[str, dict[str, int]] = {}
    for i in range(days - 1, -1, -1):
        d = today - _dt.timedelta(days=i)
        key = d.isoformat()
        buckets[key] = {
            "date": key,
            "label": f"{d.month}/{d.day}",
            "total": 0,
            "fill_done": 0,
            "sign_done": 0,
            "failed": 0,
            "running": 0,
        }

    for row in rows:
        ts = float(row.get("ctime") or row.get("mtime") or 0)
        if ts <= 0:
            continue
        try:
            d = _dt.datetime.fromtimestamp(ts).date().isoformat()
        except Exception:
            continue
        if d not in buckets:
            continue
        buckets[d]["total"] += 1
        b = str(row.get("bucket") or "")
        if b in ("fill_done", "sign_done", "failed", "running"):
            buckets[d][b] += 1
        elif b in ("name_failed", "need_human"):
            buckets[d]["failed"] += 1
    return list(buckets.values())


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _save_case_upload(case_id: str, filename: str, content: bytes) -> str:
    uploads = GAMMA_CASES_DIR / case_id / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", filename or "upload.bin")
    file_id = f"{uuid.uuid4().hex[:10]}_{safe}"
    (uploads / file_id).write_bytes(content)
    return file_id


ID_IMAGE_MAX_BYTES = 500 * 1024


def _normalize_id_image(content: bytes, filename: str = "") -> tuple[bytes, str, dict]:
    """身份证图：已是 jpg 且 ≤500KB 则原样返回；否则转 jpg 并压到 500KB 内。"""
    meta: dict = {
        "originalSize": len(content),
        "originalName": filename or "",
        "processed": False,
    }
    lower = (filename or "").lower()
    is_jpeg_name = lower.endswith(".jpg") or lower.endswith(".jpeg")
    # 魔数判断 jpeg
    is_jpeg_bytes = len(content) >= 3 and content[0:3] == b"\xff\xd8\xff"
    if is_jpeg_bytes and len(content) <= ID_IMAGE_MAX_BYTES:
        out_name = filename if is_jpeg_name else re.sub(r"\.[^.]+$", "", filename or "id") + ".jpg"
        if not out_name.lower().endswith((".jpg", ".jpeg")):
            out_name = (out_name or "id") + ".jpg"
        meta["finalSize"] = len(content)
        meta["finalName"] = out_name
        return content, out_name, meta

    try:
        from io import BytesIO
        from PIL import Image
    except ImportError as e:
        # 无 Pillow：若已是 JPEG 且未超业务上限则直存；否则明确报错（避免整站卡死）
        if is_jpeg_bytes and len(content) <= 5 * 1024 * 1024:
            out_name = filename if is_jpeg_name else re.sub(r"\.[^.]+$", "", filename or "id") + ".jpg"
            if not out_name.lower().endswith((".jpg", ".jpeg")):
                out_name = (out_name or "id") + ".jpg"
            meta.update(
                {
                    "finalSize": len(content),
                    "finalName": out_name,
                    "warn": "pillow_missing_passthrough",
                }
            )
            return content, out_name, meta
        raise RuntimeError(
            "缺少 Pillow，无法压缩身份证图片。请在演示包 .venv 执行: python -m pip install Pillow"
        ) from e

    img = Image.open(BytesIO(content))
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    base = re.sub(r"\.[^.]+$", "", filename or "id") or "id"
    out_name = f"{base}.jpg"
    quality = 88
    scale = 1.0
    best = b""
    for _ in range(16):
        w = max(1, int(img.width * scale))
        h = max(1, int(img.height * scale))
        cur = img if (w == img.width and h == img.height) else img.resize((w, h), Image.Resampling.LANCZOS)
        buf = BytesIO()
        cur.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        best = data
        if len(data) <= ID_IMAGE_MAX_BYTES:
            meta.update(
                {
                    "processed": True,
                    "finalSize": len(data),
                    "finalName": out_name,
                    "quality": quality,
                    "scale": scale,
                }
            )
            return data, out_name, meta
        if quality > 55:
            quality -= 8
        else:
            scale *= 0.85
            quality = max(50, quality)

    # 仍超限则返回最后一帧（尽量小）
    meta.update(
        {
            "processed": True,
            "finalSize": len(best),
            "finalName": out_name,
            "quality": quality,
            "scale": scale,
            "warn": "still_over_limit",
        }
    )
    return best, out_name, meta


def _merge_id_cards(base_cards: list, patch_cards: list) -> list:
    """按 personKey 合并证件附件，保留已上传 fileId。"""
    by_key: dict[str, dict] = {}
    order: list[str] = []

    def _ingest(cards: list, prefer_files: bool) -> None:
        for c in cards:
            if not isinstance(c, dict):
                continue
            key = str(c.get("personKey") or "").strip()
            if not key:
                name = str(c.get("personName") or "").strip()
                key = f"p_{name}" if name else ""
            if not key:
                continue
            if key not in by_key:
                by_key[key] = {
                    "personKey": key,
                    "personName": str(c.get("personName") or ""),
                    "roles": list(c.get("roles") or []),
                    "frontFileId": "",
                    "backFileId": "",
                }
                order.append(key)
            cur = by_key[key]
            if c.get("personName"):
                cur["personName"] = str(c.get("personName"))
            roles = c.get("roles") or []
            if roles:
                seen = set(cur.get("roles") or [])
                for r in roles:
                    if r not in seen:
                        cur.setdefault("roles", []).append(r)
                        seen.add(r)
            if prefer_files or c.get("frontFileId"):
                if c.get("frontFileId"):
                    cur["frontFileId"] = c["frontFileId"]
            if prefer_files or c.get("backFileId"):
                if c.get("backFileId"):
                    cur["backFileId"] = c["backFileId"]

    _ingest(base_cards or [], prefer_files=True)
    _ingest(patch_cards or [], prefer_files=True)
    # patch 列表顺序优先（反映当前表单去重人员）
    if patch_cards:
        patch_order = []
        for c in patch_cards:
            if not isinstance(c, dict):
                continue
            key = str(c.get("personKey") or "").strip()
            if not key:
                name = str(c.get("personName") or "").strip()
                key = f"p_{name}" if name else ""
            if key and key not in patch_order:
                patch_order.append(key)
        if patch_order:
            order = [k for k in patch_order if k in by_key] + [k for k in order if k not in patch_order]
    return [by_key[k] for k in order if k in by_key]


def _patch_id_card_attachment(
    case_id: str,
    side: str,
    file_id: str,
    *,
    person_key: str,
    person_name: str = "",
    roles: list | None = None,
) -> dict:
    """写入指定 personKey 的 front/backFileId。"""
    case_dir = GAMMA_CASES_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    payload_path = case_dir / "payload.json"
    if payload_path.exists():
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    else:
        payload = {"schemaVersion": "1.0"}
    attachments = payload.setdefault("attachments", {})
    cards = attachments.setdefault("idCards", [])
    if not isinstance(cards, list):
        cards = []
        attachments["idCards"] = cards
    target = None
    for c in cards:
        if isinstance(c, dict) and str(c.get("personKey") or "") == person_key:
            target = c
            break
    if target is None:
        target = {
            "personKey": person_key,
            "personName": person_name or "",
            "roles": list(roles or []),
            "frontFileId": "",
            "backFileId": "",
        }
        cards.append(target)
    if person_name:
        target["personName"] = person_name
    if roles:
        target["roles"] = list(roles)
    key = "frontFileId" if side == "front" else "backFileId"
    target[key] = file_id
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


@app.post("/api/cases/{case_id}/address-proof")
async def upload_address_proof(
    case_id: str,
    file: UploadFile = File(...),
):
    """上传住所使用相关文件（PDF），写入案件 uploads 并回填 payload.attachments.addressProof。"""
    case_id = (case_id or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)
    if len(content) > 20 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "PDF 不能超过 20MB"}, status_code=400)
    name = file.filename or "address_proof.pdf"
    lower = name.lower()
    ctype = (file.content_type or "").lower()
    if not (lower.endswith(".pdf") or "pdf" in ctype):
        return JSONResponse({"ok": False, "error": "仅支持 PDF"}, status_code=400)
    if not lower.endswith(".pdf"):
        name = re.sub(r"\.[^.]+$", "", name) + ".pdf"
    file_id = _save_case_upload(case_id, name, content)
    payload_path = GAMMA_CASES_DIR / case_id / "payload.json"
    payload: dict = {}
    if payload_path.exists():
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    att = payload.setdefault("attachments", {})
    if not isinstance(att, dict):
        att = {}
        payload["attachments"] = att
    proofs = att.get("addressProof")
    if not isinstance(proofs, list):
        proofs = []
    if file_id not in proofs:
        proofs = [file_id] + [x for x in proofs if x != file_id]
    att["addressProof"] = proofs
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "caseId": case_id,
        "fileId": file_id,
        "fileName": name,
        "size": len(content),
    }


@app.post("/api/cases/{case_id}/id-card")
async def upload_id_card(
    case_id: str,
    side: str = Form(...),
    file: UploadFile = File(...),
    personKey: str = Form(""),
    personName: str = Form(""),
    roles: str = Form(""),
):
    """按人上传身份证人像面(front)或国徽面(back)。"""
    case_id = (case_id or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    side = (side or "").strip().lower()
    if side not in ("front", "back"):
        return JSONResponse({"ok": False, "error": "side must be front or back"}, status_code=400)
    person_key = (personKey or "").strip()
    person_name = (personName or "").strip()
    if not person_key:
        person_key = f"p_{person_name}" if person_name else "p_unknown"
    role_list: list[str] = []
    if roles.strip():
        try:
            parsed = json.loads(roles)
            if isinstance(parsed, list):
                role_list = [str(x) for x in parsed if str(x).strip()]
            else:
                role_list = [x.strip() for x in roles.split(",") if x.strip()]
        except Exception:
            role_list = [x.strip() for x in roles.split(",") if x.strip()]

    content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)
    if len(content) > 5 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "文件不能超过 5MB"}, status_code=400)

    name = file.filename or f"id_{side}.jpg"
    lower = name.lower()
    if not (lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png")):
        return JSONResponse({"ok": False, "error": "仅支持 jpg/png"}, status_code=400)

    try:
        content, name, normalize_meta = _normalize_id_image(content, name)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"图片处理失败: {e}"}, status_code=400)
    if len(content) > ID_IMAGE_MAX_BYTES:
        return JSONResponse(
            {"ok": False, "error": "压缩后仍超过 500KB，请更换更清晰较小的图片"},
            status_code=400,
        )

    file_id = _save_case_upload(case_id, name, content)
    card = _patch_id_card_attachment(
        case_id,
        side,
        file_id,
        person_key=person_key,
        person_name=person_name,
        roles=role_list,
    )
    return {
        "ok": True,
        "caseId": case_id,
        "side": side,
        "fileId": file_id,
        "fileName": name,
        "size": len(content),
        "personKey": person_key,
        "idCard": card,
        "normalize": normalize_meta,
    }


@app.get("/api/name_check/meta")
async def name_check_meta():
    return await gamaz_proxy.proxy_get_with_fallback(
        "/api/name_check/meta",
        fallback=gamaz_proxy.local_name_meta,
    )


@app.get("/api/name_check/industry")
async def name_check_industry(kw: str = "", limit: int = 20):
    lim = min(max(limit, 1), 50)
    return await gamaz_proxy.proxy_get_with_fallback(
        "/api/name_check/industry",
        {"kw": kw, "limit": lim},
        fallback=lambda: gamaz_proxy.local_industry_suggest(kw, lim),
    )


@app.post("/api/name_check/validate")
async def name_check_validate(body: dict):
    return await gamaz_proxy.proxy_post_with_fallback(
        "/api/name_check/validate",
        body or {},
        fallback=gamaz_proxy.local_name_validate,
    )


@app.post("/api/name_check/check")
async def name_check_check(body: dict):
    return await gamaz_proxy.proxy_post_with_fallback(
        "/api/name_check/check",
        body or {},
        timeout=gamaz_proxy.CHECK_TIMEOUT,
        fallback=gamaz_proxy.local_name_check,
    )


@app.post("/api/name_check/zihao_generate")
async def name_check_zihao_generate(body: dict):
    """生成字号候选：上游 → 大模型(GAMAZ_LLM_*) → 本地字库，默认 10 个。"""
    return await gamaz_proxy.generate_zihao_candidates(body or {})


@app.get("/api/biz_scope/meta")
async def biz_scope_meta():
    return await gamaz_proxy.proxy_get("/api/biz_scope/meta")


@app.get("/api/biz_scope/index")
async def biz_scope_index(
    keyword: str = "",
    permit_type: str = "",
    industry_code: str = "",
    page: int = 1,
    page_size: int = 20,
    ids: str = "",
):
    q: dict = {
        "keyword": keyword,
        "industry_code": industry_code,
        "page": page,
        "page_size": min(max(page_size, 1), 100),
        "ids": ids,
    }
    if permit_type != "":
        try:
            q["permit_type"] = int(permit_type)
        except ValueError:
            q["permit_type"] = permit_type
    return await gamaz_proxy.proxy_get("/api/biz_scope/index", q)


@app.post("/api/biz_scope/generate")
async def biz_scope_generate(body: dict):
    return await gamaz_proxy.proxy_post("/api/biz_scope/generate", body or {})


@app.post("/api/case-payload")
async def sync_case_payload(request: Request, body: dict):
    """把原型表单组装的 gamma-yct payload 写入本地案件（默认 GM202608062231EF）。"""
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    case_id = str(body.get("caseId") or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    patch = body.get("payload")
    if not isinstance(patch, dict):
        return JSONResponse({"ok": False, "error": "payload must be object"}, status_code=400)

    case_dir = GAMMA_CASES_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "uploads").mkdir(exist_ok=True)
    (case_dir / "screenshots").mkdir(exist_ok=True)

    payload_path = case_dir / "payload.json"
    meta_path = case_dir / "meta.json"

    if payload_path.exists():
        try:
            base = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            base = {}
    else:
        base = {}
    # 先合并表单；证件附件按 personKey 合并，保留已上传 fileId
    merged = _deep_merge(base, patch)
    base_cards = ((base.get("attachments") or {}).get("idCards") or [])
    patch_cards = ((patch.get("attachments") or {}).get("idCards") or [])
    merged.setdefault("attachments", {})["idCards"] = _merge_id_cards(base_cards, patch_cards)
    base_proof = ((base.get("attachments") or {}).get("addressProof") or [])
    patch_proof = ((patch.get("attachments") or {}).get("addressProof") or [])
    if isinstance(patch_proof, list) and any(str(x or "").strip() for x in patch_proof):
        merged.setdefault("attachments", {})["addressProof"] = [
            str(x).strip() for x in patch_proof if str(x or "").strip()
        ]
    elif isinstance(base_proof, list) and base_proof:
        merged.setdefault("attachments", {})["addressProof"] = base_proof

    # 尽量用 gamma-yct schema 校验结构（不强制业务 validate_payload）
    try:
        _ensure_gamma_path()
        from apps.api.payload import CasePayload

        merged = CasePayload.model_validate(merged).model_dump()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"payload schema invalid: {e}", "caseId": case_id},
            status_code=400,
        )

    # 住所必须结构化完整，否则 RPA 无法填「住所填报」弹窗
    reg = merged.get("registration") if isinstance(merged.get("registration"), dict) else {}
    addr = reg.get("address") if isinstance(reg.get("address"), dict) else {}
    missing_addr = [
        k
        for k in ("district", "street", "road", "detail")
        if not str(addr.get(k) or "").strip()
    ]
    if missing_addr:
        return JSONResponse(
            {
                "ok": False,
                "error": "住所未填完整，缺少: " + ",".join(missing_addr) + "。请在客户端重新选择区县/街道并填写路名门牌号。",
                "caseId": case_id,
                "missing": missing_addr,
            },
            status_code=400,
        )

    payload_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {
            "caseId": case_id,
            "flow": "name_declaration_v1",
            "createdAt": now,
            "artifacts": {"qrImageUrl": None, "screenshots": []},
            "result": {},
            "reason": None,
        }
    meta["caseId"] = case_id
    meta["status"] = "draft"
    meta["customerMessage"] = "草稿：已从原型同步表单字段"
    meta["currentStep"] = 1
    meta["updatedAt"] = now
    meta["workerLocked"] = False
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "caseId": case_id,
        "path": str(payload_path),
        "tradeName": ((merged.get("name") or {}).get("tradeName") or ""),
        "ownershipRatio": ((merged.get("beneficialOwner") or {}).get("ownershipRatio") or 0),
        "idCardCount": len(((merged.get("attachments") or {}).get("idCards") or [])),
        "idCards": [
            {
                "personKey": c.get("personKey"),
                "personName": c.get("personName"),
                "roles": c.get("roles") or [],
                "ready": bool(c.get("frontFileId") and c.get("backFileId")),
            }
            for c in ((merged.get("attachments") or {}).get("idCards") or [])
            if isinstance(c, dict)
        ],
        "district": (
            (merged.get("registration") or {}).get("district")
            or ((merged.get("registration") or {}).get("address") or {}).get("district")
            or ""
        ),
    }


@app.post("/api/start-automation")
async def start_automation(request: Request, body: dict):
    """同步案件后启动后台填报，二维码中继到 sid。

    无头由环境变量 GAMMA_HEADLESS 控制（默认 True；本机调试设 0）。
    """
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    case_id = str(body.get("caseId") or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    sid = str(body.get("sid") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "sid required"}, status_code=400)

    payload_path = GAMMA_CASES_DIR / case_id / "payload.json"
    if not payload_path.exists():
        return JSONResponse(
            {"ok": False, "error": f"payload missing for {case_id}; call /api/case-payload first"},
            status_code=400,
        )

    headless = GAMMA_HEADLESS
    gamma_root = _ensure_gamma_path()
    # 重试前清掉上一轮 cancel / 残留 Chrome，避免新会话被误杀或复用已登录 profile
    try:
        from apps.api import store as gamma_store
        from apps.worker.run_fill_session import abort_fill_browser

        abort_fill_browser(case_id, kill_all=False)
        gamma_store.consume_cancel(case_id)
    except Exception:
        pass
    await asyncio.sleep(0.8)
    await relay_hub.ensure_job(sid, case_id)
    started = relay_hub.start_fill_thread(sid, case_id, gamma_root, headless=headless)
    return {
        "ok": True,
        "caseId": case_id,
        "sid": sid,
        "started": started,
        "mode": "relay_visible" if not headless else "relay_headless",
        "headless": headless,
        "message": (
            ("已启动可见窗填报" if not headless else "已启动后台无头填报")
            if started
            else "同 sid 填报已在运行"
        ),
    }


@app.post("/api/start-signature")
async def start_signature(request: Request, body: dict):
    """在填报保留的浏览器上启动电子签名，二维码中继到 sid。"""
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    case_id = str(body.get("caseId") or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    sid = str(body.get("sid") or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "sid required"}, status_code=400)

    gamma_root = _ensure_gamma_path()
    try:
        from apps.api import store as gamma_store
        from apps.worker.run_fill_session import (
            get_active_browser,
            has_parked_sign_session,
            wait_until_sign_park_ready,
        )

        # 清除误置的取消标记，避免稍晚点「继续签名」被当成取消
        gamma_store.consume_cancel(case_id)

        # 填报结束 → park 入等有空窗；页面未超时前应能接上，多等一会
        for _ in range(20):
            if has_parked_sign_session(case_id) or get_active_browser(case_id) is not None:
                break
            await asyncio.sleep(0.25)
        if get_active_browser(case_id) is not None and not has_parked_sign_session(case_id):
            await asyncio.to_thread(
                lambda: wait_until_sign_park_ready(case_id, timeout_sec=12.0)
            )

        meta_path = GAMMA_CASES_DIR / case_id / "meta.json"
        st = ""
        if meta_path.exists():
            try:
                st = str(json.loads(meta_path.read_text(encoding="utf-8")).get("status") or "")
            except Exception:
                st = ""
        ready_like = st in {
            "ready_for_user_submit",
            "signing",
            "awaiting_alipay_qr",
            "awaiting_sign_face_qr",
            "name_next_reached",
            "sign_done",
        }
        if (
            not has_parked_sign_session(case_id)
            and get_active_browser(case_id) is None
            and not ready_like
        ):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "无可用签名会话，请先完成自动填报后再点「继续完成签名」",
                },
                status_code=409,
            )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"签名环境检查失败：{e}"},
            status_code=500,
        )

    await relay_hub.ensure_job(sid, case_id)
    relay_hub.clear_qr(sid)
    started = relay_hub.start_sign_thread(sid, case_id, gamma_root)
    if not started:
        return JSONResponse(
            {
                "ok": False,
                "error": "无法启动电子签名：填报线程仍占用浏览器且未挂起，请稍候再试",
            },
            status_code=409,
        )
    return {
        "ok": True,
        "caseId": case_id,
        "sid": sid,
        "started": True,
        "mode": "sign",
        "message": "已启动电子签名",
    }


@app.post("/api/refresh-sign-qr")
async def refresh_sign_qr(request: Request, body: dict):
    """第8/9页「重试」：仅重新抓取当前阶段二维码，不重跑签名流程。"""
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    case_id = str(body.get("caseId") or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    sid = str(body.get("sid") or "").strip()
    kind = str(body.get("kind") or "alipay").strip().lower() or "alipay"
    if kind not in ("alipay", "face"):
        kind = "alipay"
    if not sid:
        return JSONResponse({"ok": False, "error": "sid required"}, status_code=400)

    _ensure_gamma_path()
    try:
        from apps.worker.run_fill_session import request_sign_qr_refresh

        relay_hub.clear_qr(sid)
        request_sign_qr_refresh(case_id, kind)
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"刷新二维码失败：{e}"},
            status_code=500,
        )
    return {
        "ok": True,
        "caseId": case_id,
        "sid": sid,
        "kind": kind,
        "message": "已请求重新获取二维码",
    }


@app.get("/api/automation/{sid}")
async def automation_status(sid: str):
    job = await relay_hub.get(sid)
    if job is None:
        return JSONResponse({"ok": False, "error": "unknown sid"}, status_code=404)

    summary = _case_summary(job.case_id or "", job)
    case_status = str(summary.get("caseStatus") or "")
    customer_message = str(summary.get("customerMessage") or "")
    reason = str(summary.get("reason") or "")
    name_failed = bool(summary.get("nameFailed"))
    needs_human = bool(summary.get("needsHuman"))
    stage = str(summary.get("stage") or "pending")
    stages_done = list(summary.get("stagesDone") or [])
    progress = str(summary.get("progress") or "等待填报进度…")
    fill_ok = bool(summary.get("fillOk"))
    sign_done = bool(summary.get("signDone"))
    sign_phase = str(summary.get("signPhase") or "") or (job.sign_phase or "")
    if not sign_phase:
        if case_status == "awaiting_alipay_qr":
            sign_phase = "alipay_qr"
        elif case_status == "awaiting_sign_face_qr":
            sign_phase = "face_qr"
        elif case_status == "sign_done":
            sign_phase = "done"
        elif case_status == "signing":
            sign_phase = "signing"

    # 案件已过扫码阶段时，即使 job.logged_in 因调度丢失未置位，也视为已登录
    logged_in = bool(job.logged_in) or case_status in PAST_QR_STATUSES

    return {
        "ok": True,
        "sid": sid,
        "caseId": job.case_id,
        "hasQr": bool(job.qr_b64),
        "qr": job.qr_b64 if job.qr_b64 else None,
        "qrKind": job.qr_kind or None,
        "signPhase": sign_phase or None,
        "signDone": sign_done,
        "loggedIn": logged_in,
        "finished": job.finished,
        "fillOk": fill_ok,
        "error": job.error or (reason if job.finished and not fill_ok else None),
        "running": bool(job.thread and job.thread.is_alive()),
        "caseStatus": case_status,
        "progress": progress,
        "customerMessage": customer_message,
        "reason": reason,
        "nameFailed": name_failed,
        "needsHuman": needs_human,
        "stage": stage,
        "stagesDone": stages_done,
    }


async def _admin_collect_rows(limit: int = 200) -> list[dict[str, Any]]:
    jobs = await relay_hub.list_jobs()
    by_case: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []

    for job in jobs:
        row = _case_summary(job.case_id or "", job)
        rows.append(row)
        if job.case_id:
            by_case[job.case_id] = row

    try:
        case_dirs = [
            d
            for d in GAMMA_CASES_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ]
        case_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    except Exception:
        case_dirs = []

    for d in case_dirs:
        if d.name in by_case:
            continue
        if not (d / "payload.json").exists() and not (d / "meta.json").exists():
            continue
        rows.append(_case_summary(d.name, None))

    rows.sort(key=lambda r: float(r.get("mtime") or 0), reverse=True)
    return rows[: max(1, min(int(limit or 200), 500))]


@app.get("/api/admin/dashboard")
async def admin_dashboard(request: Request, limit: int = 120, days: int = 14):
    """运维看板：KPI、近 N 日提交趋势、实时任务与用户信息。"""
    denied = _require_admin(request)
    if denied is not None:
        return denied
    limit = max(1, min(int(limit or 120), 300))
    days = max(7, min(int(days or 14), 30))

    # 趋势用更大样本；列表仍按 limit 截断
    all_rows = await _admin_collect_rows(500)
    tasks = all_rows[:limit]

    import datetime as _dt

    today = _dt.date.today()
    today_ts0 = _dt.datetime.combine(today, _dt.time.min).timestamp()

    def _is_today(row: dict[str, Any]) -> bool:
        ts = float(row.get("ctime") or row.get("mtime") or 0)
        return ts >= today_ts0

    today_rows = [r for r in all_rows if _is_today(r)]
    counts = {
        "total": len(all_rows),
        "today": len(today_rows),
        "running": sum(1 for r in all_rows if r.get("bucket") == "running"),
        "fill_done": sum(1 for r in all_rows if r.get("bucket") == "fill_done"),
        "sign_done": sum(1 for r in all_rows if r.get("bucket") == "sign_done"),
        "failed": sum(1 for r in all_rows if r.get("bucket") == "failed"),
        "name_failed": sum(1 for r in all_rows if r.get("bucket") == "name_failed"),
        "need_human": sum(1 for r in all_rows if r.get("bucket") == "need_human"),
        "in_progress": sum(1 for r in all_rows if r.get("bucket") == "in_progress"),
        "today_sign_done": sum(1 for r in today_rows if r.get("bucket") == "sign_done"),
        "today_failed": sum(
            1 for r in today_rows if r.get("bucket") in ("failed", "name_failed")
        ),
    }

    # 最近活跃用户（按手机号聚合）
    users: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        phone = str(r.get("contactPhone") or r.get("authPhone") or r.get("legalMobile") or "").strip()
        if not phone:
            continue
        u = users.get(phone) or {
            "phone": phone,
            "legalName": "",
            "cases": 0,
            "lastCaseId": "",
            "lastCompany": "",
            "lastStatus": "",
            "lastMtime": 0.0,
        }
        u["cases"] += 1
        mt = float(r.get("mtime") or 0)
        if mt >= float(u.get("lastMtime") or 0):
            u["lastMtime"] = mt
            u["lastCaseId"] = r.get("caseId") or ""
            u["lastCompany"] = r.get("companyName") or r.get("tradeName") or ""
            u["lastStatus"] = r.get("caseStatus") or ""
            u["legalName"] = r.get("legalName") or u.get("legalName") or ""
        users[phone] = u
    user_list = sorted(
        users.values(), key=lambda x: float(x.get("lastMtime") or 0), reverse=True
    )[:40]

    return {
        "ok": True,
        "serverTime": time.time(),
        "counts": counts,
        "daily": _build_daily_series(all_rows, days=days),
        "tasks": tasks,
        "users": user_list,
        "alerts": [
            r
            for r in tasks
            if r.get("bucket") in ("need_human", "failed", "name_failed")
            or r.get("running")
        ][:20],
    }


@app.post("/api/automation/{sid}/resume")
async def resume_automation(sid: str):
    """人工处理完未知错误后，通知 worker 继续填报（不关浏览器）。"""
    job = await relay_hub.get(sid)
    if job is None:
        return JSONResponse({"ok": False, "error": "unknown sid"}, status_code=404)
    case_id = (job.case_id or "").strip()
    if not case_id:
        return JSONResponse({"ok": False, "error": "sid 未关联 caseId"}, status_code=400)
    if not bool(job.thread and job.thread.is_alive()):
        return JSONResponse(
            {"ok": False, "error": "自动化会话已结束，无法在原页继续，请重新发起填报"},
            status_code=409,
        )

    from datetime import datetime

    _ensure_gamma_path()
    try:
        from apps.api import store as gamma_store

        gamma_store.request_resume(case_id)
    except Exception:
        case_dir = GAMMA_CASES_DIR / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "resume.flag").write_text(datetime.now().isoformat(), encoding="utf-8")

    return {"ok": True, "sid": sid, "caseId": case_id, "message": "已通知继续填报"}


@app.post("/api/automation/{sid}/cancel")
async def cancel_automation(sid: str, request: Request):
    """用户取消填报：终止 RPA 并关闭浏览器。无 job 时也尽量按 caseId 关窗。"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    job = await relay_hub.get(sid)
    case_id = ""
    if job is not None:
        case_id = (job.case_id or "").strip()
        result = relay_hub.cancel_job(sid)
    else:
        result = {"ok": True, "sid": sid, "message": "无进行中的自动化会话"}

    # 无论 job 是否还在，都按案件号强制关 Chrome（解决取消未命中会话时窗口残留）
    try:
        _ensure_gamma_path()
        from apps.worker.run_fill_session import abort_fill_browser

        if not case_id:
            case_id = DEFAULT_CASE_ID
        # 只关本案件窗口，避免 kill_all 误杀其它/刚拉起的会话
        abort_fill_browser(case_id, kill_all=False)
        result["browserClosed"] = True
        result["caseId"] = case_id
    except Exception as e:
        result["browserWarn"] = str(e)
    return result


@app.post("/api/automation/cancel-case")
async def cancel_automation_by_case(request: Request, body: dict):
    """按 caseId 取消并强制关闭填报 Chrome（不依赖 sid）。"""
    denied = _require_api_token(request, body)
    if denied is not None:
        return denied
    case_id = str(body.get("caseId") or DEFAULT_CASE_ID).strip() or DEFAULT_CASE_ID
    # 顺带取消仍在跑的同 case 会话
    try:
        for sid, job in list(getattr(relay_hub, "_jobs", {}).items()):
            if (getattr(job, "case_id", "") or "") == case_id and not getattr(job, "finished", True):
                try:
                    relay_hub.cancel_job(sid)
                except Exception:
                    pass
    except Exception:
        pass

    _ensure_gamma_path()
    from apps.api import store as gamma_store
    from apps.worker.run_fill_session import abort_fill_browser

    try:
        gamma_store.request_cancel(case_id)
    except Exception:
        pass
    abort_fill_browser(case_id, kill_all=False)
    return {"ok": True, "caseId": case_id, "killed": 0, "message": "已取消并关闭填报浏览器"}


@app.get("/start/{sid}")
async def start_session(sid: str, mock: bool = False):
    """可选 REST 起始接口（不影响 WS 主流程）。"""
    target = MOCK_GOV_URL if mock else REAL_PORTAL_URL
    return {
        "sid": sid,
        "mode": "mock" if mock else "real",
        "target_url": target,
        "websocket": f"/ws/{sid}",
    }


@app.post("/test/trigger_login/{sid}")
async def trigger_mock_login(sid: str):
    """测试用：在 mock 政务网页上执行 markLoggedIn()，模拟用户扫码。"""
    session = await sessions.get(sid)
    if session is None or session.page is None:
        return JSONResponse({"error": "session not found or page not ready"}, status_code=404)
    try:
        await session.page.evaluate("markLoggedIn()")
        return {"sid": sid, "triggered": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------- WebSocket ----------
@app.websocket("/ws/{sid}")
async def websocket_endpoint(websocket: WebSocket, sid: str):
    await websocket.accept()
    lid = f"{sid}-{uuid.uuid4().hex[:8]}"
    target_url = REAL_PORTAL_URL

    # 解析首条消息：{action:"bind", sid:"...", mock:false, subscribe:["qr","step","screenshot"]}
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        data = json.loads(first)
        if data.get("action") != "bind":
            await websocket.send_text(msg_error("首条消息需为 {action: 'bind'}"))
            await websocket.close(code=1003)
            return
        if data.get("mock"):
            host = websocket.headers.get("host", "localhost:8766")
            target_url = f"http://{host}{MOCK_GOV_URL}?sid={sid}"
    except asyncio.TimeoutError:
        await websocket.send_text(msg_error("首条消息超时"))
        await websocket.close(code=1008)
        return
    except json.JSONDecodeError as e:
        await websocket.send_text(msg_error(f"首条消息 JSON 解析失败: {e}"))
        await websocket.close(code=1003)
        return

    subscribe = set(data.get("subscribe", ["qr", "login", "error", "step", "screenshot", "bound"]))
    relay_mode = bool(data.get("relay")) or str(data.get("mode") or "") == "relay"

    async def safe_send(text: str):
        try:
            await websocket.send_text(text)
        except Exception:
            pass

    # 通知客户端：会话已绑定
    await safe_send(msg_bound(sid, expires_at=time.time() + 600))

    # ---------- relay：只收填报推送的 QR/登录，不启演示包门户 RPA ----------
    if relay_mode:
        job = await relay_hub.add_client(sid, lid, safe_send, subscribe)
        if job is None:
            await safe_send(msg_error("relay 初始化失败"))
            await websocket.close(code=1011)
            return
        await safe_send(msg_step("relay_ready", "已连接中继，等待后台自动化推送二维码"))
        try:
            while True:
                msg = await websocket.receive_text()
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                action = data.get("action")
                if action == "ping":
                    await safe_send(msg_pong())
                elif action == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await relay_hub.remove_client(sid, lid)
            try:
                await websocket.close()
            except Exception:
                pass
        return

    listener = Listener(
        lid=lid,
        sid=sid,
        subscribe=subscribe,
        qr_callback=lambda s, qr: safe_send(msg_qr_update(qr, remaining_seconds=180)),
        login_success_callback=lambda s, user: safe_send(msg_login_success(s, user)),
        error_callback=lambda s, err: safe_send(msg_error(err)),
        step_callback=lambda s, step, data: safe_send(msg_step(step, data.get("message", ""), data)),
        screenshot_callback=lambda s, b64: safe_send(msg_screenshot(b64)),
    )

    # 启动 RPA 会话（如已存在则复用并追加监听器）
    try:
        await sessions.get_or_create(sid, target_url, initial_listener=listener)
    except Exception as e:
        await safe_send(msg_error(f"启动 RPA 失败: {e}"))
        await websocket.close(code=1011)
        return

    # 维持连接（接收 ping / stop）
    try:
        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            action = data.get("action")
            if action == "ping":
                await safe_send(msg_pong())
            elif action == "stop":
                break
            elif action == "skip_to_login":
                # 协同兜底：自动流程卡住但 RPA 浏览器实际已到登录页时，从当前页面接管后续步骤
                asyncio.create_task(_resume_after_login(sid))
    except WebSocketDisconnect:
        pass
    finally:
        await sessions.remove_listener(sid, lid)
        try:
            await websocket.close()
        except Exception:
            pass


# ---------- 协同兜底：从真实登录页接管后续步骤 ----------
async def _resume_after_login(sid: str):
    """当自动流程卡住但 RPA 浏览器实际已到 zwdtuser 登录页时，从当前页面继续：
    选择企业(已隐含) → 点扫码登录 → 抓二维码 → 广播。
    """
    try:
        session = await sessions.get(sid)
        if session is None:
            return
        # 1. 确认/切换到登录页
        ok = await session._ensure_login_page()
        if not ok:
            await session._broadcast_error("仍未检测到登录页，请确认浏览器已在 zwdtuser 登录页")
            return
        await session._broadcast_step("click_enterprise", "已选择「企业」，进入一网通办登录页")
        # 2. 点「扫码登录」tab
        clicked = await session._click_qr_login_tab()
        if clicked:
            await session._broadcast_step("click_qr_tab", "已点击「扫码登录」")
        else:
            await session._broadcast_error("未找到「扫码登录」tab（可能已在二维码视图）")
        # 3. 抓二维码
        await session._broadcast_step("wait_qr", "正在等待二维码渲染…")
        qr = await session._wait_for_qr(timeout=session.MAX_QR_WAIT_SECONDS)
        if qr:
            session.qr_count += 1
            await session._broadcast_qr(qr)
            await session._broadcast_step("qr_captured", "已捕获二维码并同步到扫码页")
        else:
            await session._broadcast_error("未能在登录页找到二维码（可再点一次「我已到登录页」）")
    except Exception as e:
        try:
            session = await sessions.get(sid)
            if session:
                await session._broadcast_error(f"接管流程异常: {e}")
        except Exception:
            pass


# ---------- 后台清理任务 ----------
async def _cleanup_loop():
    while True:
        try:
            await asyncio.sleep(60)
            n = await sessions.cleanup_idle()
            if n > 0:
                import logging
                logging.info(f"[cleanup] removed {n} idle sessions")
        except asyncio.CancelledError:
            return
        except Exception:
            pass


@app.on_event("startup")
async def _on_startup():
    relay_hub.bind_loop(asyncio.get_running_loop())
    app.state.cleanup_task = asyncio.create_task(_cleanup_loop())


@app.on_event("shutdown")
async def _on_shutdown():
    task = getattr(app.state, "cleanup_task", None)
    if task:
        task.cancel()
    for sid in await sessions.list_active():
        await sessions.remove(sid)
    from .rpa.browser import shutdown_shared_browser
    await shutdown_shared_browser()
