"""可编程填报会话：无头 Playwright + 可选 QR/登录回调。

流程与 scripts/run_name_flow.py 一致：
  run_qr_login → open_name_declaration_from_portal → run_fill_pipeline
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]

# case_id -> browser|context / driver pid / chrome profile（取消时强制关窗）
_ACTIVE_BROWSERS: dict[str, Any] = {}
_ACTIVE_BROWSER_PIDS: dict[str, int] = {}
_ACTIVE_BROWSER_PROFILES: dict[str, str] = {}


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
    os.environ.setdefault("PLAYWRIGHT_CHANNEL", "chrome")


def _safe_case_token(case_id: str) -> str:
    tok = re.sub(r"[^A-Za-z0-9_-]+", "_", (case_id or "case").strip()) or "case"
    return tok[:80]


def _chrome_profile_dir(case_id: str) -> Path:
    """使用纯 ASCII 的 TEMP 目录，便于取消时按命令行标记精确杀掉 Chrome。"""
    d = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / f"gamaz-pw-{_safe_case_token(case_id)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _profile_marker(case_id: str) -> str:
    return f"gamaz-pw-{_safe_case_token(case_id)}"


def _browser_pid(browser: Any) -> Optional[int]:
    """解析 Playwright 驱动进程 PID（channel=chrome 时 Browser.process 常为 None）。"""
    try:
        impl = getattr(browser, "_impl_obj", None)
        proc = getattr(impl, "process", None)
        if proc is not None and getattr(proc, "pid", None):
            return int(proc.pid)
    except Exception:
        pass
    try:
        impl = getattr(browser, "_impl_obj", None)
        br = getattr(impl, "browser", None) or getattr(impl, "_browser", None)
        for obj in (br, impl):
            if obj is None:
                continue
            proc = getattr(obj, "process", None)
            if proc is not None and getattr(proc, "pid", None):
                return int(proc.pid)
            conn = getattr(obj, "_connection", None)
            transport = getattr(conn, "_transport", None)
            proc = getattr(transport, "_proc", None)
            if proc is not None and getattr(proc, "pid", None):
                return int(proc.pid)
    except Exception:
        pass
    return None


def _force_kill_pid(pid: int) -> None:
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        else:
            import signal

            os.kill(int(pid), signal.SIGKILL)
    except Exception:
        pass


def _pids_matching_marker(marker: str) -> list[int]:
    """查找命令行含 marker 的浏览器进程（ASCII marker，避免中文路径匹配失败）。"""
    if not marker or os.name != "nt":
        return []
    pids: list[int] = []
    ps1 = Path(os.environ.get("TEMP") or ".") / f"gamaz-find-{os.getpid()}.ps1"
    try:
        ps1.write_text(
            "$ErrorActionPreference='SilentlyContinue'\n"
            f"$m = '{marker.replace(chr(39), '')}'\n"
            "Get-CimInstance Win32_Process |\n"
            "  Where-Object {\n"
            "    $_.Name -match '^(chrome|msedge|chromium)\\.exe$' -and\n"
            "    $_.CommandLine -and ($_.CommandLine -like ('*'+$m+'*'))\n"
            "  } |\n"
            "  ForEach-Object { $_.ProcessId }\n",
            encoding="utf-8",
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        for line in (r.stdout or "").splitlines():
            s = line.strip()
            if s.isdigit():
                pids.append(int(s))
    except Exception:
        pass
    finally:
        try:
            ps1.unlink()
        except Exception:
            pass
    return pids


def _force_kill_by_marker(marker: str) -> int:
    killed = 0
    for pid in _pids_matching_marker(marker):
        _force_kill_pid(pid)
        killed += 1
    return killed


def kill_all_gamaz_browsers() -> int:
    """兜底：关掉所有本工具拉起的 gamaz-pw-* Chrome。"""
    return _force_kill_by_marker("gamaz-pw-")


def abort_fill_browser(case_id: str, *, kill_all: bool = False) -> bool:
    """强制关闭本案件填报浏览器窗口。

    默认只杀本 case 标记的 Chrome，避免误关其它会话窗口。
    ``kill_all=True`` 时才兜底杀掉所有 gamaz-pw-*（仅显式全局取消使用）。
    """
    browser = _ACTIVE_BROWSERS.pop(case_id, None)
    pid = _ACTIVE_BROWSER_PIDS.pop(case_id, None)
    _ACTIVE_BROWSER_PROFILES.pop(case_id, None)
    marker = _profile_marker(case_id)
    if pid is None and browser is not None:
        pid = _browser_pid(browser)

    # 1) 先按 ASCII 标记杀本案件 Chrome
    _force_kill_by_marker(marker)
    # 2) 再杀 Playwright 驱动进程树
    if pid:
        _force_kill_pid(pid)
    # 3) 再扫一次本案件，防止残留
    _force_kill_by_marker(marker)
    if kill_all:
        kill_all_gamaz_browsers()

    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    return True


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
    store.consume_cancel(case_id)
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
            if store.is_cancelled(case_id) or store.consume_cancel(case_id):
                store.set_status(
                    case_id,
                    statuses.CANCELLED,
                    customer_message="已取消填报",
                    reason="用户取消",
                )
                result["reason"] = "用户取消"
                result["status"] = statuses.CANCELLED
                break
            if playwright is None:
                playwright = sync_playwright().start()
            profile_dir = _chrome_profile_dir(case_id)
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": str(profile_dir),
                "headless": headless,
                "channel": (config.PLAYWRIGHT_CHANNEL or "chrome"),
                "locale": "zh-CN",
                "viewport": {"width": 1360, "height": 900},
                "accept_downloads": False,
            }
            if browser is None:
                try:
                    # TEMP 下 ASCII profile：取消时按 gamaz-pw-{caseId} 精确杀窗
                    browser = playwright.chromium.launch_persistent_context(**launch_kwargs)
                except Exception:
                    launch_kwargs.pop("channel", None)
                    browser = playwright.chromium.launch_persistent_context(**launch_kwargs)
                _ACTIVE_BROWSERS[case_id] = browser
                _ACTIVE_BROWSER_PROFILES[case_id] = str(profile_dir)
                pid = _browser_pid(browser)
                if pid:
                    _ACTIVE_BROWSER_PIDS[case_id] = pid
            logged_in_ok = False
            try:
                page = run_qr_login(case_id, browser, on_qr=on_qr, on_login=on_login)
                if store.is_cancelled(case_id) or store.consume_cancel(case_id):
                    store.set_status(
                        case_id,
                        statuses.CANCELLED,
                        customer_message="已取消填报",
                        reason="用户取消",
                    )
                    result["reason"] = "用户取消"
                    result["status"] = statuses.CANCELLED
                    break
                logged_in_ok = True
                page = open_name_declaration_from_portal(page, case_id)
                run_fill_pipeline(case_id, page)
            except Exception as exc:
                if (
                    store.is_cancelled(case_id)
                    or store.consume_cancel(case_id)
                    or "cancelled" in str(exc).lower()
                ):
                    store.set_status(
                        case_id,
                        statuses.CANCELLED,
                        customer_message="已取消填报",
                        reason="用户取消",
                    )
                    result["reason"] = "用户取消"
                    result["status"] = statuses.CANCELLED
                    break
                # 扫码等待阶段出错：保留 Chrome，便于用户继续扫码/排查（勿自动关窗）
                cur_status = ""
                try:
                    cur_status = str((store.get_case(case_id) or {}).get("status") or "")
                except Exception:
                    pass
                exc_s = str(exc)
                awaiting_qr = (not logged_in_ok) and (
                    cur_status
                    in (
                        statuses.AWAITING_QR_LOGIN,
                        statuses.LOGGING_IN,
                        statuses.READY_TO_SUBMIT,
                    )
                    or any(
                        k in exc_s
                        for k in ("扫码", "二维码", "TargetClosed", "has been closed")
                    )
                )
                if awaiting_qr:
                    store.set_status(
                        case_id,
                        statuses.NEED_HUMAN,
                        reason=exc_s,
                        customer_message=(
                            "扫码登录异常，浏览器窗口已保留。"
                            "请查看后台 Chrome，或点「重试」重新拉取二维码。"
                        ),
                    )
                    result["reason"] = exc_s
                    result["status"] = statuses.NEED_HUMAN
                    result["keepBrowserOpen"] = True
                    break
                # 已登录后的入口异常（设立登记点击失败等）：保留窗口 + 已登录态，便于演示页进进度并排查
                portal_entry_fail = logged_in_ok and any(
                    k in exc_s
                    for k in (
                        "企业设立登记",
                        "我已知晓",
                        "名称申报",
                        "establish",
                        "设立入口",
                    )
                )
                store.set_status(
                    case_id,
                    statuses.NEED_HUMAN,
                    reason=exc_s,
                    customer_message=(
                        "登录成功，但进入企业设立登记失败，浏览器窗口已保留。"
                        "请查看后台 Chrome 或点进度页「重试」。"
                        if portal_entry_fail
                        else "系统异常，请点击重试或返回上一页。"
                    ),
                )
                result["reason"] = exc_s
                result["status"] = statuses.NEED_HUMAN
                result["keepBrowserOpen"] = bool(portal_entry_fail)
                break

            fresh = store.get_case(case_id)
            result["status"] = fresh.get("status")
            result["reason"] = fresh.get("reason") or ""
            if fresh.get("status") == statuses.CANCELLED:
                result["ok"] = False
                result["reason"] = "用户取消"
                break
            if fresh.get("status") in (
                statuses.READY_FOR_USER_SUBMIT,
                statuses.NAME_NEXT_REACHED,
            ):
                result["ok"] = True
                # 暂存成功：保留窗口便于用户本人核对提交
                result["keepBrowserOpen"] = True
                break
            # 满 3 次失败等 need_human / name_failed：不保留窗口，finally 关浏览器
            if fresh.get("status") in (
                statuses.NEED_HUMAN,
                statuses.NAME_FAILED,
            ):
                result["keepBrowserOpen"] = False
            break
    finally:
        was_cancelled = False
        keep_open = bool(result.get("keepBrowserOpen"))
        try:
            from apps.api import store as _store

            was_cancelled = _store.is_cancelled(case_id) or (
                str((_store.get_case(case_id) or {}).get("status") or "") == "cancelled"
            )
            st = str((_store.get_case(case_id) or {}).get("status") or "")
            # 仅扫码等待 / 本人确认 / 暂存成功：强制保留窗口
            # need_human（含阶段自动 3 次失败）走关窗，除非上方显式 keepBrowserOpen
            if not was_cancelled and st in (
                statuses.AWAITING_QR_LOGIN,
                statuses.LOGGING_IN,
                statuses.HUMAN_CONFIRMATION_REQUIRED,
                statuses.READY_FOR_USER_SUBMIT,
                statuses.NAME_NEXT_REACHED,
            ):
                keep_open = True
                result["keepBrowserOpen"] = True
            elif not was_cancelled and st in (
                statuses.NEED_HUMAN,
                statuses.NAME_FAILED,
            ):
                # 满 3 次失败等：关浏览器（扫码异常路径会显式 keepBrowserOpen=True）
                if not result.get("keepBrowserOpen"):
                    keep_open = False
        except Exception:
            pass

        if keep_open and not was_cancelled and browser is not None:
            # 扫码阶段保留窗口，登记以便后续取消能关到
            _ACTIVE_BROWSERS[case_id] = browser
            if case_id not in _ACTIVE_BROWSER_PIDS:
                pid_keep = _browser_pid(browser)
                if pid_keep:
                    _ACTIVE_BROWSER_PIDS[case_id] = pid_keep
            store.release_worker(case_id)
            try:
                fresh = store.get_case(case_id)
                result["status"] = fresh.get("status")
            except Exception:
                pass
            return result

        _ACTIVE_BROWSERS.pop(case_id, None)
        pid = _ACTIVE_BROWSER_PIDS.pop(case_id, None)
        profile = _ACTIVE_BROWSER_PROFILES.pop(case_id, None)
        store.release_worker(case_id)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
            if pid is None:
                pid = _browser_pid(browser)
        if pid:
            _force_kill_pid(pid)
        # 只杀本案件窗口，勿 kill_all 误伤其它会话
        abort_fill_browser(case_id, kill_all=False)
        if profile:
            _force_kill_by_marker(_profile_marker(case_id))
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        try:
            fresh = store.get_case(case_id)
            result["status"] = fresh.get("status")
            if was_cancelled:
                result["reason"] = "用户取消"
        except Exception:
            pass

    return result
