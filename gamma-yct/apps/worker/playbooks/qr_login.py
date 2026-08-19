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
    """Try element screenshot for QR; fallback full page."""
    from apps.api import store

    qr_path = store.case_dir(case_id) / "qr.png"
    selectors = [s.strip() for s in config.QR_SELECTOR.split(",") if s.strip()]
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=2000):
                continue
            box = loc.bounding_box()
            if box and box["width"] >= 80 and box["height"] >= 80:
                loc.screenshot(path=str(qr_path))
                return True
        except Exception:
            continue
    page.screenshot(path=str(qr_path))
    return qr_path.exists()


def _looks_logged_in(page: Page, login_url: str) -> bool:
    """与 portal.looks_logged_in 一致：必须看到「退出」才算登录。"""
    return looks_logged_in(page)


def run_qr_login(case_id: str, browser: Browser) -> Page:
    """新开上下文：门户 → 强制扫码登录 → 返回已登录页。"""
    context = browser.new_context(
        locale="zh-CN",
        viewport={"width": 1360, "height": 900},
        accept_downloads=False,
    )
    page = context.new_page()
    # 禁止跳过扫码
    return ensure_portal_logged_in(case_id, page, allow_qr=True)


def open_browser() -> Any:
    return sync_playwright().start()
