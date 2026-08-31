"""门户登录：以 portal_yct 为入口，扫码登录后回到门户。"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright

from apps.worker import config
from apps.worker.playbooks.portal import ensure_portal_logged_in, looks_logged_in


def _screenshot(page: Page, case_id: str, name: str) -> None:
    from pathlib import Path

    from apps.api import store

    path = store.case_dir(case_id) / "screenshots" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=True)


def _capture_qr(page: Page, case_id: str) -> bool:
    """Try element screenshot for QR; fallback full page. Writes qr.png."""
    uri = capture_qr_data_uri(page, case_id=case_id)
    return bool(uri)


def capture_qr_data_uri(
    page: Page,
    case_id: str | None = None,
    *,
    allow_fullpage: bool = True,
) -> str:
    """截取登录二维码，返回 data:image/png;base64,...；失败返回空串。

    allow_fullpage=False 时不回退整页截图（避免把非二维码页面推给演示端）。
    """
    import base64

    from apps.api import store
    from apps.worker import config

    def _try_capture(p: Page) -> bytes | None:
        png: bytes | None = None
        selectors = [s.strip() for s in config.QR_SELECTOR.split(",") if s.strip()]
        preferred = [
            "img.qrcode-img",
            ".qrcode img",
            "img[src*='qrcode']",
            "img[src*='qr-code']",
            "img[src^='data:image']",
            ".login-qrcode img",
            "#qrcode img",
            "#qrcode",
            "canvas.qrcode",
            "canvas",
        ]
        for sel in preferred + selectors:
            loc = p.locator(sel).first
            try:
                if loc.count() == 0:
                    continue
                if not loc.is_visible(timeout=800):
                    continue
                box = loc.bounding_box()
                if box and box["width"] >= 80 and box["height"] >= 80:
                    png = loc.screenshot(type="png")
                    if png:
                        return png
            except Exception:
                continue
        return None

    png: bytes | None = None
    # 当前页 + 同上下文其它页（登录常新开标签）
    pages = [page]
    try:
        for p in page.context.pages:
            if p not in pages:
                pages.append(p)
    except Exception:
        pass
    for p in pages:
        try:
            png = _try_capture(p)
        except Exception:
            png = None
        if png:
            break
        # iframe 内二维码
        try:
            for fr in p.frames:
                for sel in (
                    "img.qrcode-img",
                    ".qrcode img",
                    "img[src*='qrcode']",
                    "img[src^='data:image']",
                    "canvas",
                ):
                    loc = fr.locator(sel).first
                    try:
                        if loc.count() == 0 or not loc.is_visible(timeout=500):
                            continue
                        box = loc.bounding_box()
                        if box and box["width"] >= 80 and box["height"] >= 80:
                            png = loc.screenshot(type="png")
                            if png:
                                break
                    except Exception:
                        continue
                if png:
                    break
        except Exception:
            pass
        if png:
            break

    if png is None and allow_fullpage:
        try:
            png = page.screenshot(type="png", full_page=False)
        except Exception:
            png = None
    if not png:
        return ""
    if case_id:
        qr_path = store.case_dir(case_id) / "qr.png"
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        qr_path.write_bytes(png)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def wait_capture_qr_data_uri(
    page: Page,
    case_id: str | None = None,
    *,
    timeout_sec: float = 20,
) -> str:
    """等待二维码节点渲染完成再截取；首轮失败会重试，避免第一次推空。"""
    import time

    end = time.time() + timeout_sec
    last = ""
    while time.time() < end:
        uri = capture_qr_data_uri(page, case_id=case_id, allow_fullpage=False)
        if uri:
            return uri
        last = uri
        time.sleep(0.7)
    # 超时仍无独立二维码节点时，不再用整页冒充（演示页会无法扫）
    return last or capture_qr_data_uri(page, case_id=case_id, allow_fullpage=False)


def _looks_logged_in(page: Page, login_url: str) -> bool:
    """与 portal.looks_logged_in 一致：必须看到「退出」才算登录。"""
    return looks_logged_in(page)


def run_qr_login(
    case_id: str,
    browser: Browser,
    *,
    on_qr=None,
    on_login=None,
) -> Page:
    """门户 → 强制扫码登录 → 返回已登录页。

    ``browser`` 可为 Browser，或 launch_persistent_context 返回的 BrowserContext。
    """
    # persistent context 已有页面/上下文，勿再 new_context
    if hasattr(browser, "new_context"):
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1360, "height": 900},
            accept_downloads=False,
        )
        page = context.new_page()
    else:
        context = browser
        page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
    return ensure_portal_logged_in(
        case_id, page, allow_qr=True, on_qr=on_qr, on_login=on_login
    )


def open_browser() -> Any:
    return sync_playwright().start()
