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


def capture_qr_data_uri(page: Page, case_id: str | None = None) -> str:
    """截取登录二维码，返回 data:image/png;base64,...；失败返回空串。"""
    import base64

    from apps.api import store
    from apps.worker import config

    png: bytes | None = None
    selectors = [s.strip() for s in config.QR_SELECTOR.split(",") if s.strip()]
    # 优先更具体的二维码选择器，再退回通用
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
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=800):
                continue
            box = loc.bounding_box()
            if box and box["width"] >= 80 and box["height"] >= 80:
                png = loc.screenshot(type="png")
                if png:
                    break
        except Exception:
            continue
    if png is None:
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
    """新开上下文：门户 → 强制扫码登录 → 返回已登录页。"""
    context = browser.new_context(
        locale="zh-CN",
        viewport={"width": 1360, "height": 900},
        accept_downloads=False,
    )
    page = context.new_page()
    return ensure_portal_logged_in(
        case_id, page, allow_qr=True, on_qr=on_qr, on_login=on_login
    )


def open_browser() -> Any:
    return sync_playwright().start()
