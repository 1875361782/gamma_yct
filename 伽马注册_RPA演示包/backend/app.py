"""FastAPI 应用：REST 健康检查 + WebSocket /ws/{sid} + H5 页面托管。

两页演示：
  - /acquire   : A 页（二维码获取页），展示 RPA 实时截图流 + 操作日志
  - /qrlogin   : B 页（扫码登录页），手机 H5 风格，展示同步过来的 QR
  - /ws/{sid}  : WebSocket，A/B 两页绑定同 sid，服务端广播事件
"""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse

from .sessions import SessionManager
from .rpa.browser import Listener
from .protocol import (
    msg_bound, msg_qr_update, msg_qr_refreshing,
    msg_login_success, msg_login_failed, msg_error, msg_pong,
    msg_step, msg_screenshot,
)


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
    return FileResponse(PROTOTYPE_HTML, media_type="text/html")


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
    return FileResponse(qr_path, media_type="text/html")


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

    async def safe_send(text: str):
        try:
            await websocket.send_text(text)
        except Exception:
            pass

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

    # 通知客户端：会话已绑定
    await safe_send(msg_bound(sid, expires_at=time.time() + 600))

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
