"""Dispatch dry_run / playwright automation for a case.

标准路径（强制扫码 + DOM，禁用桌面 RPA）：
门户 https://yct.sh.gov.cn/portal_yct/
→ 关弹窗 → 开办 → 企业 → 扫码登录 → 我已知晓
→ 企业设立登记（不涉及前置审批事项）→ 名称 DOM 填报 → 下一步
"""

from __future__ import annotations

from apps.api import statuses, store
from apps.worker import config
from apps.worker.dry_run import run_dry_run


def run_case(case_id: str) -> None:
    mode = (config.AUTOMATION_MODE or "dry_run").strip().lower()
    if mode == "dry_run":
        run_dry_run(case_id)
        return
    if mode == "desktop_rpa":
        store.set_status(
            case_id,
            statuses.FAILED,
            reason="桌面 RPA 已禁用；请使用 AUTOMATION_MODE=playwright 走门户扫码 + DOM 填报",
        )
        store.release_worker(case_id)
        return
    if mode in ("playwright", "auto"):
        _run_playwright(case_id)
        return
    store.set_status(case_id, statuses.FAILED, reason=f"未知 AUTOMATION_MODE={mode}")
    store.release_worker(case_id)


def _run_playwright(case_id: str) -> None:
    from apps.worker.playbooks.fill_pipeline import run_fill_pipeline
    from apps.worker.playbooks.portal import open_name_declaration_from_portal
    from apps.worker.playbooks.qr_login import run_qr_login
    from playwright.sync_api import sync_playwright

    playwright = None
    browser = None
    keep_browser_open = False
    release = True
    try:
        if config.PLAYWRIGHT_USE_CDP:
            store.set_status(
                case_id,
                statuses.FAILED,
                reason="当前流程禁止 CDP；请设 PLAYWRIGHT_USE_CDP=false，使用新开浏览器扫码登录",
            )
            return

        playwright = sync_playwright().start()
        launch_kwargs: dict = {"headless": config.HEADLESS}
        if config.PLAYWRIGHT_CHANNEL:
            launch_kwargs["channel"] = config.PLAYWRIGHT_CHANNEL
        browser = playwright.chromium.launch(**launch_kwargs)

        # 强制扫码：allow_qr 恒为 True
        page = run_qr_login(case_id, browser)
        page = open_name_declaration_from_portal(page, case_id)
        run_fill_pipeline(case_id, page)

        st = store.get_case(case_id).get("status")
        keep_browser_open = st in (
            statuses.NAME_NEXT_REACHED,
            statuses.AWAITING_QR_LOGIN,
            statuses.NEED_HUMAN,
            statuses.HUMAN_CONFIRMATION_REQUIRED,
        )
    except Exception as e:
        msg = str(e)
        if msg == "cancelled":
            return
        status = (
            statuses.NEED_HUMAN
            if ("超时" in msg or "二维码" in msg or "登录" in msg or "扫码" in msg or "closed" in msg.lower())
            else statuses.FAILED
        )
        try:
            store.set_status(case_id, status, reason=msg)
        except Exception:
            pass
        keep_browser_open = True
    finally:
        if browser and not keep_browser_open:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass
        if release:
            store.release_worker(case_id)
