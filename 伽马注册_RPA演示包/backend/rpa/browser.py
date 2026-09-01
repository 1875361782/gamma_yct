"""Playwright 浏览器生命周期：按 sid 复用 BrowserContext + Page。

本模块实现「二维码获取页（A 页）」所需的完整 RPA 流程：
  1. 打开上海企业登记在线门户 https://yct.sh.gov.cn/portal_yct/
  2. 关闭「上海市场准入政策解读」弹窗
  3. 关闭「重要提示」弹窗
  4. 点击「开办」入口
  5. 在「请选择业务办理」弹窗中点击「企业」
  6. 页面跳转 zwdtuser 登录页后，点击「扫码登录」tab
  7. 截取二维码并广播给所有监听客户端
  8. 轮询检测登录成功后的页面跳转

同时提供截图流，让 A 页能实时看到 RPA 浏览器画面。
"""
from __future__ import annotations
import asyncio
import base64
import glob
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Set, Dict
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page, ElementHandle

from .qr_capture import capture_qr_base64
from .login_monitor import is_logged_in


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

QR_POLL_INTERVAL = 2.0          # 抓 QR 间隔
SCREENSHOT_INTERVAL = 1.2       # 截图流间隔
QR_REMAINING_DEFAULT = 180      # 政务网 QR 通常 3 分钟
MAX_QR_WAIT_SECONDS = 30        # 等待 QR 出现的最长时间


@dataclass
class Listener:
    """一个 WebSocket 客户端的监听配置。"""
    lid: str
    sid: str
    subscribe: Set[str] = field(default_factory=lambda: {"qr", "login", "error", "step", "screenshot", "bound"})
    qr_callback: Optional[Callable[[str, str], Awaitable[None]]] = None
    login_success_callback: Optional[Callable[[str, str], Awaitable[None]]] = None
    error_callback: Optional[Callable[[str, str], Awaitable[None]]] = None
    step_callback: Optional[Callable[[str, str, dict], Awaitable[None]]] = None
    screenshot_callback: Optional[Callable[[str, str], Awaitable[None]]] = None


def _playwright_cache_dirs() -> list[str]:
    dirs: list[str] = []
    env = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env and env != "0" and os.path.isdir(env):
        dirs.append(env)
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        dirs.append(os.path.join(local, "ms-playwright"))
    dirs.append(os.path.expanduser("~/Library/Caches/ms-playwright"))
    dirs.append(os.path.expanduser("~/.cache/ms-playwright"))
    # Cursor sandbox 偶发把浏览器装到 Temp 缓存；仅作探测，不强制
    temp = (os.environ.get("TEMP") or os.environ.get("TMP") or "").strip()
    if temp:
        dirs.extend(glob.glob(os.path.join(temp, "cursor-sandbox-cache", "*", "playwright")))
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        nd = os.path.normpath(d)
        if nd not in seen and os.path.isdir(nd):
            seen.add(nd)
            out.append(nd)
    return out


def _find_chromium_executable() -> Optional[str]:
    """自动探测可用的 chromium 可执行文件（Windows / macOS / Linux）。"""
    patterns = [
        # Windows
        "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-win/chrome.exe",
        # macOS
        "chromium_headless_shell-*/chrome-mac-arm64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-mac/chrome-headless-shell",
        "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chromium-*/chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        # Linux
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium-*/chrome-linux/chrome",
    ]
    for cache in _playwright_cache_dirs():
        for pat in patterns:
            for p in sorted(glob.glob(os.path.join(cache, pat)), reverse=True):
                if os.path.isfile(p):
                    return p
    return None


_CHROMIUM_EXEC = _find_chromium_executable()
# 与填报 worker 一致：无本地 chromium 时用系统 Chrome，避免缺 playwright install
_PLAYWRIGHT_CHANNEL = (os.environ.get("PLAYWRIGHT_CHANNEL") or "chrome").strip() or None


def _chromium_launch_kwargs(headless: bool) -> dict:
    kwargs: dict = {"headless": headless}
    if _CHROMIUM_EXEC:
        kwargs["executable_path"] = _CHROMIUM_EXEC
    elif _PLAYWRIGHT_CHANNEL:
        kwargs["channel"] = _PLAYWRIGHT_CHANNEL
    return kwargs


class RPASession:
    """一个 sid 一个 RPASession，支持多客户端监听。"""

    def __init__(self, sid: str, headless: bool = True):
        self.sid = sid
        self.headless = headless
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._stopped = False
        self.last_active = time.time()
        self.started_at = 0.0
        self.qr_count = 0
        self.logged_in = False
        self._listeners: Dict[str, Listener] = {}
        self._tasks: list[asyncio.Task] = []
        self._current_step = "idle"
        self._last_qr_b64: Optional[str] = None

    # ---------- 监听器管理 ----------
    def add_listener(self, listener: Listener) -> None:
        self._listeners[listener.lid] = listener
        self.last_active = time.time()

    def remove_listener(self, lid: str) -> None:
        self._listeners.pop(lid, None)

    def _want(self, lid: str, event: str) -> bool:
        listener = self._listeners.get(lid)
        if listener is None:
            return False
        return event in listener.subscribe

    async def _broadcast(self, event: str, payload_builder):
        """向所有订阅了 event 的监听器广播。"""
        for lid, listener in list(self._listeners.items()):
            if not self._want(lid, event):
                continue
            try:
                await payload_builder(listener)
            except Exception:
                pass

    async def _broadcast_step(self, step: str, message: str, extra: Optional[dict] = None):
        self._current_step = step
        data = {"step": step, "message": message, "ts": time.time()}
        if extra:
            data.update(extra)
        await self._broadcast("step", lambda l: l.step_callback(self.sid, step, data) if l.step_callback else asyncio.sleep(0))

    async def _broadcast_error(self, message: str):
        await self._broadcast("error", lambda l: l.error_callback(self.sid, message) if l.error_callback else asyncio.sleep(0))

    async def _broadcast_qr(self, qr_b64: str):
        self._last_qr_b64 = qr_b64
        await self._broadcast("qr", lambda l: l.qr_callback(self.sid, qr_b64) if l.qr_callback else asyncio.sleep(0))

    async def _broadcast_login(self, user: str = "法人"):
        self.logged_in = True
        await self._broadcast("login", lambda l: l.login_success_callback(self.sid, user) if l.login_success_callback else asyncio.sleep(0))

    async def _broadcast_screenshot(self, b64: str):
        await self._broadcast("screenshot", lambda l: l.screenshot_callback(self.sid, b64) if l.screenshot_callback else asyncio.sleep(0))

    async def replay_state_to(self, listener: Listener) -> None:
        """向新加入的监听器补发当前状态（QR / 登录成功 / 当前步骤）。"""
        try:
            if self._last_qr_b64 and self._want(listener.lid, "qr") and listener.qr_callback:
                await listener.qr_callback(self.sid, self._last_qr_b64)
            if self.logged_in and self._want(listener.lid, "login") and listener.login_success_callback:
                await listener.login_success_callback(self.sid, "法人")
            if self._current_step and self._current_step != "idle" and self._want(listener.lid, "step") and listener.step_callback:
                await listener.step_callback(self.sid, self._current_step, {"step": self._current_step, "message": "", "ts": time.time()})
        except Exception:
            pass

    # ---------- 生命周期 ----------
    async def start(self, url: str, timeout: int = 30) -> None:
        """启动浏览器并打开起始 URL，然后启动 RPA 流程与监控任务。"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            **_chromium_launch_kwargs(self.headless)
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=DEFAULT_UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self.page = await self.context.new_page()
        self.started_at = time.time()

        # 启动两个后台任务：RPA 流程 + 监控循环
        self._tasks.append(asyncio.create_task(self._run_flow(url, timeout)))
        self._tasks.append(asyncio.create_task(self._watch_loop()))

    async def stop(self) -> None:
        self._stopped = True
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass

    # ---------- RPA 主流程 ----------
    async def _run_flow(self, url: str, timeout: int) -> None:
        """按用户录屏路径操作到 QR 页面。mock 模式下走简化流程。"""
        is_mock = "mock" in url.lower() or "localhost" in url.lower() or "127.0.0.1" in url
        try:
            await self._broadcast_step("start", "RPA 浏览器已启动")

            # 1. 打开门户
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                # 等待弹窗/页面稳定（政策解读弹窗是 layui，通常在 1~3s 内渲染）
                await self.page.wait_for_timeout(2500)
                try:
                    await self.page.wait_for_selector(".layui-layer", timeout=8000)
                except Exception:
                    pass
                await self._broadcast_step("open_portal", f"已打开起始页：{url}")
            except Exception as e:
                await self._broadcast_error(f"打开起始页失败: {e}")
                return

            if is_mock:
                # mock 简化流程：直接等 QR
                await self._broadcast_step("login_page_loaded", "进入 Mock 登录页")
                qr_b64 = await self._wait_for_qr(timeout=MAX_QR_WAIT_SECONDS)
                if qr_b64:
                    self.qr_count += 1
                    await self._broadcast_qr(qr_b64)
                    await self._broadcast_step("qr_captured", "已捕获 Mock 二维码")
                else:
                    await self._broadcast_error("未能在 Mock 页找到二维码")
                return

            # 2. 关闭「上海市场准入政策解读」弹窗
            closed = await self._close_popup_by_title(["上海市场准入政策解读", "政策解读", "POLICY INTERPRETATION"])
            if closed:
                await self._broadcast_step("close_policy_popup", "已关闭「上海市场准入政策解读」弹窗")
            else:
                await self._broadcast_step("close_policy_popup", "未检测到政策解读弹窗，继续")

            # 3. 关闭「重要提示」弹窗
            closed = await self._close_popup_by_title(["重要提示", "登记代理人", "表明代理身份"])
            if closed:
                await self._broadcast_step("close_notice_popup", "已关闭「重要提示」弹窗")
            else:
                await self._broadcast_step("close_notice_popup", "未检测到重要提示弹窗，继续")

            # 4. 点击「开办」入口
            clicked = await self._click_element_by_text("开办")
            if not clicked:
                await self._broadcast_error("未找到「开办」入口")
                return
            await self._broadcast_step("click_kaiban", "已点击「开办」")
            await self.page.wait_for_timeout(1200)

            # 5. 在「请选择业务办理」弹窗（iframe）中点击「企业」，并切换到新打开的登录页
            #    （_click_enterprise_and_follow_popup 内部已做「等待落定 + 二次校验」，无需再重复）
            clicked = await self._click_enterprise_and_follow_popup()
            if not clicked:
                await self._broadcast_error("未找到「企业」选项或未能进入登录页")
                return
            await self._broadcast_step("click_enterprise", "已选择「企业」，进入一网通办登录页")

            # 6. 确认已进入 zwdtuser 登录页
            await self._broadcast_step("login_page_loaded", f"已进入登录页：{self.page.url}")

            # 7. 点击「扫码登录」tab（找不到也不阻断，capture_qr_base64 内部会再尝试点 tab）
            clicked = await self._click_qr_login_tab()
            if clicked:
                await self._broadcast_step("click_qr_tab", "已点击「扫码登录」")
            else:
                await self._broadcast_step("click_qr_tab", "未找到「扫码登录」tab，尝试直接捕获二维码")

            # 8. 等待 QR 出现
            await self._broadcast_step("wait_qr", "正在等待二维码渲染…")
            qr_b64 = await self._wait_for_qr(timeout=MAX_QR_WAIT_SECONDS)
            if qr_b64:
                # 二次校验：等二维码完全渲染后重抓一次，取稳定帧（避免显示半渲染的图片）
                await self.page.wait_for_timeout(800)
                qr_b64 = await capture_qr_base64(self.page) or qr_b64
                self.qr_count += 1
                await self._broadcast_qr(qr_b64)
                await self._broadcast_step("qr_captured", "已捕获二维码并同步到扫码页")
            else:
                await self._broadcast_error("未能在登录页找到二维码")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self._broadcast_error(f"RPA 流程异常: {e}")

    # ---------- 监控循环（QR / 登录态 / 截图流） ----------
    async def _watch_loop(self) -> None:
        """持续抓 QR、检测登录成功、推送截图流。"""
        last_qr_poll = 0
        last_screenshot = 0
        try:
            while not self._stopped:
                self.last_active = time.time()
                now = time.time()

                if self.page is None or self.page.is_closed():
                    await asyncio.sleep(1)
                    continue

                # 截图流（所有监听了 screenshot 的客户端）
                if now - last_screenshot > SCREENSHOT_INTERVAL:
                    last_screenshot = now
                    try:
                        png = await self.page.screenshot(type="png", scale="css", timeout=3000)
                        if png:
                            b64 = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                            await self._broadcast_screenshot(b64)
                    except Exception:
                        pass

                # 登录态检测
                try:
                    if not self.logged_in and await is_logged_in(self.page, self.context):
                        await self._broadcast_login("法人")
                        # 登录成功后继续截图，让 A 页能看到 RPA 浏览器自动跳转后的画面
                        await self._broadcast_step("login_success", "扫码成功，RPA 浏览器将自动跳转")
                except Exception:
                    pass

                # QR 刷新检测（如果之前已抓到 QR，则周期性检查是否变化）
                if now - last_qr_poll > QR_POLL_INTERVAL and self.qr_count > 0:
                    last_qr_poll = now
                    try:
                        qr_b64 = await capture_qr_base64(self.page)
                        if qr_b64 and qr_b64 != self._last_qr_b64:
                            self.qr_count += 1
                            await self._broadcast_qr(qr_b64)
                            await self._broadcast_step("qr_refreshed", "二维码已刷新")
                    except Exception:
                        pass

                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self._broadcast_error(f"监控循环异常: {e}")

    # ---------- 通用 RPA 辅助方法 ----------
    async def _close_popup_by_title(self, title_substrings: list[str]) -> bool:
        """根据标题文本定位弹窗并点击关闭按钮，支持 layui / element / iview 等常见弹窗。"""
        any_closed = False
        for attempt in range(4):
            # 策略 0：layui 弹窗专用关闭按钮（上海一网通办实测 close 在 .layui-layer 下）
            try:
                layui_close = self.page.locator(".layui-layer .layui-layer-close").first
                if await layui_close.count() > 0:
                    await layui_close.click(timeout=3000)
                    await self.page.wait_for_timeout(1000)
                    any_closed = True
                    continue
            except Exception:
                pass

            # 策略 0b：layui 全局 closeAll（兜底清掉所有 layui 弹窗/遮罩）
            try:
                remaining = await self.page.locator(".layui-layer").count()
                if remaining > 0:
                    await self.page.evaluate(
                        """() => {
                            if (typeof layer !== 'undefined' && layer.closeAll) layer.closeAll();
                        }"""
                    )
                    await self.page.wait_for_timeout(800)
                    any_closed = True
                    continue
            except Exception:
                pass

            for sub in title_substrings:
                try:
                    # 策略 1：找包含标题文本的固定定位/对话框元素
                    modal = await self.page.evaluate_handle(
                        """(sub) => {
                            const fixed = Array.from(document.querySelectorAll('*')).filter(e => {
                                const style = window.getComputedStyle(e);
                                const rect = e.getBoundingClientRect();
                                return (style.position === 'fixed' || style.position === 'absolute') && rect.width > 100 && rect.height > 100;
                            });
                            fixed.sort((a,b) => {
                                const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                                return (rb.width*rb.height) - (ra.width*ra.height);
                            });
                            for (const e of fixed) {
                                if ((e.textContent || '').includes(sub)) return e;
                            }
                            return null;
                        }""",
                        sub
                    )
                    modal_el = modal.as_element() if modal else None

                    if modal_el:
                        # 在弹窗内找关闭按钮
                        close_btn = await modal_el.evaluate_handle(
                            """(modal) => {
                                const candidates = modal.querySelectorAll('i, span, button, a, div, svg, [class*="close"], [class*="Close"], [class*="el-dialog__close"], [class*="ivu-modal-close"]');
                                for (const c of candidates) {
                                    const cls = (c.className || '').toLowerCase();
                                    const txt = (c.textContent || '').trim();
                                    if (cls.includes('close') || txt === '×' || txt === 'x' || txt === '✕' || txt === '关闭' || txt === 'close') return c;
                                }
                                return null;
                            }"""
                        )
                        btn_el = close_btn.as_element() if close_btn else None
                        if btn_el:
                            await btn_el.click(timeout=2000)
                            await self.page.wait_for_timeout(800)
                            return True
                        # 没有关闭按钮：点弹窗右上角
                        rect = await modal_el.bounding_box()
                        if rect:
                            await self.page.mouse.click(rect["x"] + rect["width"] - 20, rect["y"] + 20)
                            await self.page.wait_for_timeout(800)
                            return True
                except Exception:
                    continue
            # 策略 2：按 Escape 键尝试关闭当前弹窗
            try:
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(600)
            except Exception:
                pass
            await self.page.wait_for_timeout(800)
        return any_closed

    async def _click_element_by_text(self, text: str, scope_selector: Optional[str] = None, max_scope: int = 8) -> bool:
        """点击可见的精确文本元素。优先用 Playwright，再退化到 JS。"""
        # 策略 1：Playwright :has-text 选择器，尝试每个候选直到有一个可见且可点
        selectors = []
        if scope_selector:
            for scope in scope_selector.split(","):
                scope = scope.strip()
                selectors.append(f"{scope} a:has-text('{text}')")
                selectors.append(f"{scope} button:has-text('{text}')")
                selectors.append(f"{scope} [role='button']:has-text('{text}')")
        selectors.extend([
            f"a:has-text('{text}')",
            f"button:has-text('{text}')",
            f"[role='button']:has-text('{text}')",
        ])
        for sel in selectors:
            try:
                locs = self.page.locator(sel)
                n = await locs.count()
                for i in range(n):
                    try:
                        await locs.nth(i).click(timeout=1500)
                        await self.page.wait_for_timeout(500)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue

        # 策略 2：JS 兜底，找可见元素
        try:
            el = await self.page.evaluate_handle(
                """(args) => {
                    const [text, scope, maxScope] = args;
                    const roots = scope ? document.querySelectorAll(scope) : [document.body];
                    const isVisible = e => {
                        const rect = e.getBoundingClientRect();
                        return !!(rect.width && rect.height && e.offsetParent !== null);
                    };
                    for (const root of roots) {
                        const all = root.querySelectorAll('*');
                        for (const e of all) {
                            const txt = (e.textContent || '').trim();
                            if (txt !== text) continue;
                            if (!isVisible(e)) continue;
                            let p = e;
                            for (let i=0; i<maxScope && p; i++) {
                                const tag = p.tagName.toLowerCase();
                                if (tag === 'a' || tag === 'button' || p.onclick || p.getAttribute('role') === 'button') {
                                    return p;
                                }
                                p = p.parentElement;
                            }
                            return e;
                        }
                    }
                    return null;
                }""",
                [text, scope_selector, max_scope]
            )
            el = el.as_element() if el else None
            if el:
                await el.click(timeout=2000)
                await self.page.wait_for_timeout(600)
                return True
        except Exception:
            pass
        return False

    async def _click_modal_option(self, option_text: str, modal_title: Optional[str] = None) -> bool:
        """在页面弹窗/对话框中点击指定选项（如「企业」）。

        优先在包含 modal_title 的固定定位弹窗内查找，再退化到任意可见弹窗。
        """
        try:
            # 等待弹窗出现
            if modal_title:
                try:
                    await self.page.wait_for_selector(f"text={modal_title}", timeout=8000)
                except Exception:
                    pass

            # JS：找可见弹窗内 textContent 完全匹配 option_text 的元素
            el = await self.page.evaluate_handle(
                """(args) => {
                    const [optionText, modalTitle] = args;
                    const isVisible = e => {
                        const rect = e.getBoundingClientRect();
                        return !!(rect.width && rect.height && e.offsetParent !== null);
                    };
                    const isModal = e => {
                        const style = window.getComputedStyle(e);
                        const cls = (e.className || '').toLowerCase();
                        return style.position === 'fixed' || style.position === 'absolute' ||
                               cls.includes('dialog') || cls.includes('modal') ||
                               cls.includes('layer') || cls.includes('popup') ||
                               cls.includes('el-dialog') || cls.includes('ivu-modal');
                    };
                    // 先定位弹窗：包含标题的大元素
                    let modals = [];
                    if (modalTitle) {
                        const all = document.querySelectorAll('*');
                        for (const e of all) {
                            if ((e.textContent || '').includes(modalTitle) && isModal(e)) {
                                modals.push(e);
                            }
                        }
                    }
                    if (modals.length === 0) {
                        const all = document.querySelectorAll('*');
                        for (const e of all) {
                            if (isModal(e) && e.getBoundingClientRect().width > 200 && e.getBoundingClientRect().height > 150) {
                                modals.push(e);
                            }
                        }
                    }
                    // 在弹窗内找选项
                    for (const modal of modals) {
                        const candidates = modal.querySelectorAll('*');
                        for (const c of candidates) {
                            const txt = (c.textContent || '').trim();
                            if (txt === optionText && isVisible(c)) {
                                // 优先返回可点击祖先
                                let p = c;
                                for (let i = 0; i < 6 && p; i++) {
                                    const tag = p.tagName.toLowerCase();
                                    if (tag === 'a' || tag === 'button' || p.onclick ||
                                        p.getAttribute('role') === 'button') {
                                        return p;
                                    }
                                    p = p.parentElement;
                                }
                                return c;
                            }
                        }
                    }
                    return null;
                }""",
                [option_text, modal_title]
            )
            el = el.as_element() if el else None
            if el:
                await el.click(timeout=3000)
                await self.page.wait_for_timeout(600)
                return True
        except Exception:
            pass
        return False

    async def _click_enterprise_and_follow_popup(self) -> bool:
        """在「开办」弹出的 iframe 中选择「企业」，并切换到登录页。

        简化策略（时序 + 二次校验，不依赖 on_popup 事件、不做长轮询）：
          1. 点「企业」（Playwright 优先，JS 兜底）
          2. 等页面落定（timing：固定等待，让 window.open 新标签 / 同页跳转完成）
          3. 二次校验：扫描所有已打开页，把 self.page 切到登录页
        """
        try:
            # 定位开办 iframe（URL 通常包含 kbLst），找不到就用当前页
            kb_frame = next((f for f in self.page.frames if f.url and "kbLst" in f.url), None)
            target_frame = kb_frame if kb_frame is not None else self.page

            # 点击「企业」（优先 Playwright，再 JS 兜底）
            async def _do_click():
                for sel in [".progress-item:has-text('企业')", "a:has-text('企业')", "[onclick*='openNewReg']:has-text('企业')"]:
                    try:
                        loc = target_frame.locator(sel).first
                        if await loc.count() > 0:
                            await loc.click(timeout=3000)
                            return True
                    except Exception:
                        continue
                try:
                    await target_frame.evaluate(
                        """() => {
                            const isVisible = e => {
                                const r = e.getBoundingClientRect();
                                return !!(r.width && r.height && e.offsetParent !== null);
                            };
                            for (const e of document.querySelectorAll('*')) {
                                if (e.textContent.trim() === '企业' && isVisible(e)) {
                                    let p = e;
                                    for (let i = 0; i < 8 && p; i++) {
                                        const t = p.tagName.toLowerCase();
                                        if (t === 'a' || t === 'button' || p.onclick ||
                                            p.getAttribute('role') === 'button' ||
                                            p.getAttribute('onclick')) {
                                            p.click();
                                            return;
                                        }
                                        p = p.parentElement;
                                    }
                                    e.click();
                                    return;
                                }
                            }
                        }"""
                    )
                    return True
                except Exception:
                    return False

            # 用 expect_page 捕获 window.open 新标签；同时 JS 兜底点击，不阻塞
            try:
                async with self.context.expect_page() as new_page_info:
                    await _do_click()
                    try:
                        popup = await asyncio.wait_for(new_page_info.value, timeout=3)
                        if popup is not None:
                            self.page = popup
                    except Exception:
                        pass
            except Exception:
                await _do_click()

            # 2. 等页面落定（timing）：给新标签 / 同页跳转足够时间完成渲染
            await self.page.wait_for_timeout(3000)

            # 3. 二次校验：确认并切换到登录页（含兜底扫描）
            return await self._ensure_login_page()
        except Exception:
            # 任何意外都兜底确认登录页
            return await self._ensure_login_page()

    async def _ensure_login_page(self) -> bool:
        """轮询当前页 + 其它已打开页，找到 zwdtuser/loginCountry/jumpEtregNew 登录页并切换 self.page。"""
        pred = lambda u: ("zwdtuser" in u) or ("loginCountry" in u) or ("jumpEtregNew" in u)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                cur = self.page.url or "" if self.page else ""
                if pred(cur):
                    await self._wait_load()
                    return True
            except Exception:
                pass
            for p in self.context.pages:
                try:
                    if p is self.page:
                        continue
                    if pred(p.url or ""):
                        self.page = p
                        await self._wait_load()
                        return True
                except Exception:
                    continue
            await asyncio.sleep(0.8)
        return False

    async def _wait_load(self):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

    async def _click_portal_login(self) -> bool:
        """上海企业登记在线门户的「登录」按钮位于顶部一网通办通用头部 iframe 内。

        在该 iframe 中点击「登录」后，主页面会跳转到 zwdtuser.sh.gov.cn 登录页。
        """
        try:
            await self.page.wait_for_selector("iframe.customHeader_iframe", timeout=10000)
            head_frame = next((f for f in self.page.frames if "head.jsp" in (f.url or "")), None)
            if head_frame is None:
                return False
            loc = head_frame.locator("text=登录").first
            if await loc.count() == 0:
                return False
            await loc.click(timeout=3000)
            await self.page.wait_for_timeout(800)
            # 主页面会跳转，等待 URL 变化
            await self._wait_for_url_change(lambda u: "zwdtuser" in u or "loginCountry" in u, timeout=25)
            await self.page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except Exception:
            return False

    async def _click_qr_login_tab(self) -> bool:
        """尝试点击「扫码登录」tab。"""
        # 优先精确文本，再放宽到包含匹配（政务网 tab 文本常带图标/后缀）
        for txt in ["扫码登录", "扫码", "二维码登录", "QR 登录"]:
            try:
                el = await self.page.evaluate_handle(
                    """(txt) => {
                        const all = document.querySelectorAll('a, button, div, span, li, [role="tab"]');
                        for (const e of all) {
                            const t = (e.textContent || '').trim();
                            if (t === txt || t.includes(txt)) return e;
                        }
                        return null;
                    }""",
                    txt
                )
                el = el.as_element() if el else None
                if el:
                    await el.click(timeout=2000)
                    await self.page.wait_for_timeout(1200)
                    return True
            except Exception:
                continue
        # 兜底：使用 qr_capture 里的逻辑
        from .qr_capture import _click_qr_tab
        return await _click_qr_tab(self.page)

    async def _wait_for_url_change(self, predicate, timeout: int = 20) -> None:
        """轮询等待 URL 满足条件。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            url = self.page.url or ""
            if predicate(url):
                return
            await asyncio.sleep(0.5)
        raise asyncio.TimeoutError(f"URL 未在 {timeout}s 内变化，当前: {self.page.url}")

    async def _wait_for_qr(self, timeout: int = 30) -> Optional[str]:
        """轮询等待 QR 出现。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                qr = await capture_qr_base64(self.page)
                if qr:
                    return qr
            except Exception:
                pass
            await asyncio.sleep(1.0)
        return None


# 浏览器进程级单例（可选，保留给需要复用进程的场景）
_playwright: Optional[Playwright] = None
_browser: Optional[Browser] = None
_browser_lock = asyncio.Lock()


async def get_shared_browser(headless: bool = True) -> Browser:
    global _playwright, _browser
    async with _browser_lock:
        if _playwright is None:
            _playwright = await async_playwright().start()
        if _browser is None:
            _browser = await _playwright.chromium.launch(
                **_chromium_launch_kwargs(headless)
            )
        return _browser


async def shutdown_shared_browser() -> None:
    global _playwright, _browser
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None
