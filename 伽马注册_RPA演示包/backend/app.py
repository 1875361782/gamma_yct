"""FastAPI 应用：REST 健康检查 + WebSocket /ws/{sid} + H5 页面托管。

两页演示：
  - /acquire   : A 页（二维码获取页），展示 RPA 实时截图流 + 操作日志
  - /qrlogin   : B 页（扫码登录页），手机 H5 风格，展示同步过来的 QR
  - /ws/{sid}  : WebSocket，A/B 两页绑定同 sid，服务端广播事件
"""
from __future__ import annotations
import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, Form, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse

from .sessions import SessionManager
from .rpa.browser import Listener
from .protocol import (
    msg_bound, msg_qr_update, msg_qr_refreshing,
    msg_login_success, msg_login_failed, msg_error, msg_pong,
    msg_step, msg_screenshot,
)
from .relay import relay_hub
from . import gamaz_proxy


# ---------- 配置 ----------
BACKEND_DIR = Path(__file__).resolve().parent
MOCK_GOV_DIR = BACKEND_DIR / "mock_gov"

# 真实场景：从上海企业登记在线门户开始
REAL_PORTAL_URL = "https://yct.sh.gov.cn/portal_yct/"
# mock 场景：直接进入 mock 登录页（保留旧 mock 用于离线验证）
MOCK_GOV_URL = "/mock/index.html"

app = FastAPI(title="伽马注册 RPA QR 同步服务", version="0.2.0")
sessions = SessionManager(ttl_seconds=600)

# 静态文件：mock 政务网登录页（用于离线开发/演示）
app.mount("/mock", StaticFiles(directory=str(MOCK_GOV_DIR)), name="mock")
# 演示静态资源（人工客服二维码等）
_STATIC_ASSETS = BACKEND_DIR / "static"
_STATIC_ASSETS.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_ASSETS)), name="static")


# ---------- 页面路由 ----------
# 9 屏原型（同源托管，屏 6 内嵌 /qrlogin 同域，避免 iframe cross-origin）
PROTOTYPE_HTML = BACKEND_DIR / "static_prototype.html"

LANDING_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>伽马注册 RPA 演示</title>
<style>
  *{box-sizing:border-box} body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  margin:0; background:#F5F7FA; color:#0B2E66; padding:40px 20px;}
  .wrap{max-width:820px; margin:0 auto;}
  h1{font-size:24px; margin:0 0 6px}
  .sub{color:#5A6B85; margin:0 0 28px; line-height:1.6}
  .cards{display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px}
  @media (max-width:680px){.cards{grid-template-columns:1fr}}
  .card{background:#fff; border:1px solid #E5E7EB; border-radius:14px; padding:22px; text-decoration:none; color:inherit; display:block; transition:.15s}
  .card:hover{border-color:#1E5FD9; box-shadow:0 6px 20px rgba(30,95,217,.12)}
  .card .tag{display:inline-block; font-size:12px; font-weight:700; color:#1E5FD9; background:#E8F0FE; padding:3px 10px; border-radius:99px; margin-bottom:12px}
  .card.b .tag{color:#2BB673; background:#F0FDF4}
  .card.p .tag{color:#7C3AED; background:#F5F3FF}
  .card h2{font-size:18px; margin:0 0 8px}
  .card p{font-size:13px; color:#5A6B85; line-height:1.6; margin:0}
  .note{margin-top:28px; font-size:13px; color:#5A6B85; line-height:1.7; background:#fff; border:1px solid #E5E7EB; border-radius:14px; padding:18px}
  .note code{background:#1e293b; color:#86efac; padding:2px 8px; border-radius:6px; font-size:12px}
  .note b{color:#0B2E66}
</style></head>
<body><div class="wrap">
  <h1>伽马注册 · RPA 扫码登录演示</h1>
  <p class="sub">演示「手机 H5 提交 → Web RPA 自动抓取一网通办登录二维码 → 手机扫码 → RPA 自动跳转」。<br>
  请在同一台机器上打开下面页面，<b>用页面上的互跳按钮保证 sid 一致</b>。</p>
  <div class="cards">
    <a class="card p" href="/prototype">
      <span class="tag">9 屏原型</span>
      <h2>完整产品原型</h2>
      <p>屏 1-9 全流程。填完前 5 屏后，屏 6 内嵌 B 页（/qrlogin）自动启动 RPA 并展示真实二维码，扫码成功自动进屏 7。</p>
    </a>
    <a class="card a" href="/acquire">
      <span class="tag">A 页 · 操作端</span>
      <h2>二维码获取页（RPA 端）</h2>
      <p>桌面端。启动 RPA 浏览器，按「门户 → 关弹窗 → 开办 → 企业 → 扫码登录」自动操作，实时显示截图流与二维码捕获。</p>
    </a>
    <a class="card b" href="/qrlogin">
      <span class="tag">B 页 · 用户端</span>
      <h2>扫码登录页（H5）</h2>
      <p>手机 H5 风格。展示 RPA 同步过来的真实二维码，用「随申办」APP 扫码；扫码成功自动跳转。</p>
    </a>
  </div>
  <div class="note">
    <b>演示顺序（9 屏原型整合）：</b><br>
    1. 打开 <b>9 屏原型</b>（/prototype）→ 走完屏 1-5<br>
    2. 进入屏 6 时，内部 B 页 iframe 自动绑定 sid 并<b>触发 RPA</b>，实时显示真实二维码<br>
    3. 用「随申办」APP 扫码 → 屏 6 自动跳转屏 7（进度跟踪）<br>
    4. 可选：屏 6 点「↗ 打开 A 页（监控）」看 RPA 实时截图（同 sid，不重启 RPA）<br><br>
    <b>真实政务网（默认）：</b> 直接打开 <code>/prototype</code> 或 <code>/prototype?real=1</code> 即驱动上海企业登记在线真实站点；<b>离线测试：</b> 原型或 A/B 页 URL 加 <code>?mock=1</code><br>
    <b>健康检查：</b> <code>curl http://localhost:8766/health</code>
  </div>
</div></body></html>"""


@app.get("/", include_in_schema=False)
async def index():
    """演示落地页：列出 9 屏原型 / A / B 两页入口。"""
    return HTMLResponse(LANDING_HTML)


@app.get("/prototype", include_in_schema=False)
async def prototype():
    """9 屏原型（同源托管，屏 6 内嵌 /qrlogin）。"""
    if not PROTOTYPE_HTML.exists():
        return JSONResponse({"error": "prototype not found (expected backend/static_prototype.html)"}, status_code=404)
    return FileResponse(
        PROTOTYPE_HTML,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/acquire", include_in_schema=False)
async def acquire():
    """A 页：RPA 二维码获取页（桌面端，展示 RPA 浏览器截图流）。"""
    path = BACKEND_DIR / "static_acquire.html"
    if not path.exists():
        return JSONResponse({"error": "acquire page not found"}, status_code=404)
    return FileResponse(path, media_type="text/html")


@app.get("/qrlogin", include_in_schema=False)
async def qrlogin():
    """B 页：独立的「扫码登录」手机 H5 单页。"""
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
            "prototype": "http://localhost:8766/prototype",
            "acquire": "http://localhost:8766/acquire",
            "qrlogin": "http://localhost:8766/qrlogin",
            "index": "http://localhost:8766/",
        },
    }


REPO_ROOT = BACKEND_DIR.parents[1]  # …/伽马账（backend → 演示包 → 仓库根）
GAMMA_CASES_DIR = REPO_ROOT / "gamma-yct" / "data" / "cases"
DEFAULT_CASE_ID = "GM202608062231EF"


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
        raise RuntimeError("缺少 Pillow，无法压缩身份证图片") from e

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
async def sync_case_payload(body: dict):
    """把原型表单组装的 gamma-yct payload 写入本地案件（默认 GM202608062231EF）。"""
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

    # 尽量用 gamma-yct schema 校验结构（不强制业务 validate_payload）
    try:
        import sys

        gamma_root = str(REPO_ROOT / "gamma-yct")
        if gamma_root not in sys.path:
            sys.path.insert(0, gamma_root)
        from apps.api.payload import CasePayload

        merged = CasePayload.model_validate(merged).model_dump()
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"payload schema invalid: {e}", "caseId": case_id},
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
async def start_automation(body: dict):
    """同步案件后启动可见 Chrome 填报，并把二维码中继到 sid。"""
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

    gamma_root = str(REPO_ROOT / "gamma-yct")
    # 默认可见窗口，便于确认自动化已启动；二维码仍投射到演示页
    headless = bool(body.get("headless", False))
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
            "已启动后台 Chrome 窗口填报" if started and not headless
            else ("已启动后台无头填报" if started else "同 sid 填报已在运行")
        ),
    }


@app.get("/api/automation/{sid}")
async def automation_status(sid: str):
    job = await relay_hub.get(sid)
    if job is None:
        return JSONResponse({"ok": False, "error": "unknown sid"}, status_code=404)

    progress = "等待填报进度…"
    case_status = ""
    customer_message = ""
    reason = ""
    name_failed = False
    stage = "pending"
    stages_done: list[str] = []

    case_id = (job.case_id or "").strip()
    if case_id:
        case_dir = GAMMA_CASES_DIR / case_id
        meta_path = case_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                case_status = str(meta.get("status") or "")
                customer_message = str(meta.get("customerMessage") or "")
                reason = str(meta.get("reason") or "")
            except Exception:
                pass

        stage_files = [
            ("name", "fill-name-result.json", "名称信息"),
            ("basic", "fill-basic-info-result.json", "基本信息"),
            ("capital", "fill-capital-result.json", "出资情况"),
            ("personnel", "fill-personnel-result.json", "人员信息"),
            ("opening", "fill-opening-result.json", "企业开办"),
        ]
        for key, fname, _label in stage_files:
            if (case_dir / fname).exists():
                stages_done.append(key)

        stage = "done" if len(stages_done) >= len(stage_files) else "pending"
        for key, fname, label in stage_files:
            if key not in stages_done:
                stage = key
                if not customer_message:
                    progress = f"正在填写「{label}」表"
                break

        if customer_message:
            progress = customer_message
        elif job.logged_in and stage == "pending" and not stages_done:
            progress = "登录成功，正在进入名称申报…"
        elif not job.logged_in and bool(job.thread and job.thread.is_alive()):
            progress = "等待扫码登录…"
        elif not progress:
            progress = "正在同步填报进度…"

        name_failed = case_status == "name_failed" or (
            "比对未通过" in reason or "名称未通过" in reason or "生僻字" in reason
        )
        if name_failed:
            progress = customer_message or reason or "核名不通过，请返回修改字号并重试"
            if "核名不通过" not in progress and "生僻字" not in progress:
                progress = "核名不通过：" + progress

    needs_human = (not name_failed) and case_status in {
        "need_human",
        "human_confirmation_required",
    }

    fill_ok = bool(job.ok) or case_status in {
        "ready_for_user_submit",
        "name_next_reached",
    }
    # 核名失败不算填报成功
    if name_failed:
        fill_ok = False

    # 案件已过扫码阶段时，即使 job.logged_in 因调度丢失未置位，也视为已登录（驱动演示页跳进度）
    past_qr_statuses = {
        "logged_in",
        "name_checking",
        "name_failed",
        "human_confirmation_required",
        "name_next_reached",
        "filling_establishment",
        "choosing_process",
        "filling_addons",
        "ready_for_user_submit",
        "need_human",
        "offline_required",
        "failed",
    }
    logged_in = bool(job.logged_in) or case_status in past_qr_statuses

    return {
        "ok": True,
        "sid": sid,
        "caseId": job.case_id,
        "hasQr": bool(job.qr_b64),
        "qr": job.qr_b64 if job.qr_b64 else None,
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

    import sys
    from datetime import datetime

    gamma_root = str(REPO_ROOT / "gamma-yct")
    if gamma_root not in sys.path:
        sys.path.insert(0, gamma_root)
    try:
        from apps.api import store as gamma_store

        gamma_store.request_resume(case_id)
    except Exception:
        case_dir = GAMMA_CASES_DIR / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "resume.flag").write_text(datetime.now().isoformat(), encoding="utf-8")

    return {"ok": True, "sid": sid, "caseId": case_id, "message": "已通知继续填报"}


@app.post("/api/automation/{sid}/cancel")
async def cancel_automation(sid: str):
    """用户取消填报：终止 RPA 并关闭浏览器。无 job 时也尽量按 caseId 关窗。"""
    job = await relay_hub.get(sid)
    case_id = ""
    if job is not None:
        case_id = (job.case_id or "").strip()
        result = relay_hub.cancel_job(sid)
    else:
        result = {"ok": True, "sid": sid, "message": "无进行中的自动化会话"}

    # 无论 job 是否还在，都按案件号强制关 Chrome（解决取消未命中会话时窗口残留）
    try:
        import sys

        gamma_root = str(REPO_ROOT / "gamma-yct")
        if gamma_root not in sys.path:
            sys.path.insert(0, gamma_root)
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
async def cancel_automation_by_case(body: dict):
    """按 caseId 取消并强制关闭填报 Chrome（不依赖 sid）。"""
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

    import sys

    gamma_root = str(REPO_ROOT / "gamma-yct")
    if gamma_root not in sys.path:
        sys.path.insert(0, gamma_root)
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
