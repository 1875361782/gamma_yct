"""可编程填报会话：无头 Playwright + 可选 QR/登录回调。

流程与 scripts/run_name_flow.py 一致：
  run_qr_login → open_name_declaration_from_portal → run_fill_pipeline
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]


def _prepare_env(*, headless: bool) -> None:
    os.environ["AUTOMATION_MODE"] = "playwright"
    os.environ["SKIP_QR_LOGIN"] = "false"
    os.environ["PLAYWRIGHT_USE_CDP"] = "false"
    os.environ["DESKTOP_RPA_FALLBACK"] = "false"
    os.environ["ALLOW_FINAL_SUBMIT"] = "false"
    os.environ["PLAYWRIGHT_HEADLESS"] = "true" if headless else "false"
    os.environ["YCT_CAPTURE_QR"] = "true"
    os.environ.setdefault("YCT_LOGIN_TIMEOUT_SEC", "300")
    os.environ.setdefault("YCT_PORTAL_URL", "https://yct.sh.gov.cn/portal_yct/")
    # 本机 Chrome headless，避免依赖未安装的 chromium_headless_shell
    os.environ.setdefault("PLAYWRIGHT_CHANNEL", "chrome")


def run_fill_session(
    case_id: str,
    *,
    headless: bool = True,
    on_qr: Optional[Callable[[str], None]] = None,
    on_login: Optional[Callable[[], None]] = None,
    max_rounds: int = 2,
) -> dict[str, Any]:
    """跑完整填报到暂存。返回 {ok, status, reason, caseId}。"""
    _prepare_env(headless=headless)

    if str(ROOT) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(ROOT))

    from apps.api import statuses, store
    from apps.worker import config
    from apps.worker.playbooks.fill_pipeline import run_fill_pipeline
    from apps.worker.playbooks.portal import open_name_declaration_from_portal
    from apps.worker.playbooks.qr_login import run_qr_login
    from playwright.sync_api import sync_playwright

    case = store.get_case(case_id)
    store.set_status(
        case_id,
        statuses.READY_TO_SUBMIT,
        customer_message="准备启动门户扫码流程（后台 Chrome 窗口）",
        reason="",
    )
    meta_path = store.case_dir(case_id) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["flow"] = "name_declaration_v1"
    meta["workerLocked"] = True
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    result: dict[str, Any] = {
        "ok": False,
        "caseId": case_id,
        "status": None,
        "reason": None,
        "headless": headless,
        "tradeName": ((case.get("payload") or {}).get("name") or {}).get("tradeName"),
    }

    playwright = None
    browser = None
    try:
        for round_i in range(1, max_rounds + 1):
            if playwright is None:
                playwright = sync_playwright().start()
            # 强制本机 Chrome 无头，避免走未安装的 chromium_headless_shell
            launch_kwargs: dict[str, Any] = {
                "headless": headless,
                "channel": (config.PLAYWRIGHT_CHANNEL or "chrome"),
            }
            if browser is None:
                try:
                    browser = playwright.chromium.launch(**launch_kwargs)
                except Exception:
                    # 回退：无 channel 的 chromium（需已 playwright install）
                    launch_kwargs.pop("channel", None)
                    browser = playwright.chromium.launch(**launch_kwargs)
            try:
                page = run_qr_login(case_id, browser, on_qr=on_qr, on_login=on_login)
                page = open_name_declaration_from_portal(page, case_id)
                run_fill_pipeline(case_id, page)
            except Exception as exc:
                store.set_status(case_id, statuses.NEED_HUMAN, reason=str(exc))
                result["reason"] = str(exc)
                try:
                    browser.close()
                except Exception:
                    pass
                browser = None
                if round_i < max_rounds:
                    time.sleep(2)
                    continue
                break

            fresh = store.get_case(case_id)
            result["status"] = fresh.get("status")
            result["reason"] = fresh.get("reason") or ""
            if fresh.get("status") in (
                statuses.READY_FOR_USER_SUBMIT,
                statuses.NAME_NEXT_REACHED,
            ):
                result["ok"] = True
                break
            break
    finally:
        store.release_worker(case_id)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        try:
            fresh = store.get_case(case_id)
            result["status"] = fresh.get("status")
        except Exception:
            pass

    return result
