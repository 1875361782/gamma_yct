"""可编程填报会话：无头 Playwright + 可选 QR/登录回调。

流程与 scripts/run_name_flow.py 一致：
  run_qr_login → open_name_declaration_from_portal → run_fill_pipeline
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]

# case_id -> browser|context / driver pid / chrome profile（取消时强制关窗）
_ACTIVE_BROWSERS: dict[str, Any] = {}
_ACTIVE_BROWSER_PIDS: dict[str, int] = {}
_ACTIVE_BROWSER_PROFILES: dict[str, str] = {}

# 填报线程挂起等待签名（Playwright 同步 API 必须在同一线程内使用浏览器）
# case_id -> {event, request, result}
_SIGN_PARKS: dict[str, dict[str, Any]] = {}
# 签名扫码等待中：前端点「重试」仅刷新二维码（case_id -> kind: alipay|face|*）
_SIGN_QR_REFRESH: dict[str, str] = {}


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
    park = _SIGN_PARKS.get(case_id)
    if park and isinstance(park.get("event"), threading.Event):
        try:
            park["event"].set()
        except Exception:
            pass
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


def wipe_chrome_profile(case_id: str) -> None:
    """关掉本案件浏览器并清空持久化 profile，强制下次重新扫码。"""
    abort_fill_browser(case_id, kill_all=False)
    time.sleep(0.5)
    profile_dir = _chrome_profile_dir(case_id)
    marker = _profile_marker(case_id)
    for _ in range(4):
        try:
            if profile_dir.exists():
                shutil.rmtree(profile_dir)
            break
        except Exception:
            _force_kill_by_marker(marker)
            time.sleep(0.6)
    profile_dir.mkdir(parents=True, exist_ok=True)


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
    # 先清取消信号，再杀窗+清空 profile，避免重用已登录态导致找不到「扫码登录」
    store.consume_cancel(case_id)
    wipe_chrome_profile(case_id)
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
            # 暂存成功：立刻登记签名挂起槽，供「继续完成签名」同线程唤醒
            try:
                st_now = str((store.get_case(case_id) or {}).get("status") or "")
            except Exception:
                st_now = ""
            if result.get("ok") or st_now in (
                statuses.READY_FOR_USER_SUBMIT,
                statuses.NAME_NEXT_REACHED,
            ):
                prepare_sign_park(case_id)
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


def get_active_browser(case_id: str):
    """返回填报结束后仍保留的浏览器上下文（若有）。"""
    return _ACTIVE_BROWSERS.get(case_id)


def prepare_sign_park(case_id: str) -> bool:
    """填报保留浏览器后立刻登记挂起槽，避免前端点签名时尚未 park。"""
    if case_id not in _ACTIVE_BROWSERS:
        return False
    if case_id not in _SIGN_PARKS:
        _SIGN_PARKS[case_id] = {
            "event": threading.Event(),
            "request": None,
            "result": None,
            "waiter_alive": False,
            "thread": None,
        }
    return True


def has_parked_sign_session(case_id: str) -> bool:
    """填报线程是否正在 park_await_sign 等待（仅登记槽位不算就绪）。"""
    park = _SIGN_PARKS.get(case_id)
    if not park or case_id not in _ACTIVE_BROWSERS:
        return False
    if not park.get("waiter_alive"):
        return False
    th = park.get("thread")
    if th is not None and hasattr(th, "is_alive") and not th.is_alive():
        return False
    return True


def request_parked_sign(
    case_id: str,
    *,
    on_qr: Optional[Callable[[str, str], None]] = None,
    on_phase: Optional[Callable[[str], None]] = None,
) -> bool:
    """通知挂起的填报线程在本线程内跑电子签名。"""
    if not has_parked_sign_session(case_id):
        return False
    park = _SIGN_PARKS.get(case_id)
    if not park:
        return False
    park["request"] = {"on_qr": on_qr, "on_phase": on_phase}
    ev = park.get("event")
    if isinstance(ev, threading.Event):
        ev.set()
        return True
    return False


def request_sign_qr_refresh(case_id: str, kind: str = "*") -> bool:
    """签名流程进行中：仅请求重新抓取并推送当前阶段二维码。"""
    k = (kind or "*").strip().lower() or "*"
    if k not in ("alipay", "face", "*"):
        k = "*"
    _SIGN_QR_REFRESH[case_id] = k
    return True


def consume_sign_qr_refresh(case_id: str, kind: str) -> bool:
    """e_sign 等待扫码时调用：若前端请求刷新本 kind，则消费并返回 True。"""
    want = _SIGN_QR_REFRESH.get(case_id)
    if not want:
        return False
    k = (kind or "").strip().lower()
    if want in ("*", k):
        _SIGN_QR_REFRESH.pop(case_id, None)
        return True
    return False


def wait_until_sign_park_ready(case_id: str, *, timeout_sec: float = 15.0) -> bool:
    """填报刚结束到 park_await_sign 入等之间有短暂空窗，短等挂起就绪。"""
    deadline = time.time() + max(1.0, float(timeout_sec))
    while time.time() < deadline:
        if has_parked_sign_session(case_id):
            return True
        if case_id not in _ACTIVE_BROWSERS and case_id not in _SIGN_PARKS:
            return False
        time.sleep(0.25)
    return has_parked_sign_session(case_id)


def park_await_sign(case_id: str, *, timeout_sec: float = 7200) -> Optional[dict[str, Any]]:
    """在填报线程内挂起，直到收到签名请求 / 取消 / 超时。

    必须与创建浏览器的线程相同，否则 Playwright 同步 API 会失败。
    """
    if str(ROOT) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(ROOT))
    from apps.api import store

    if case_id not in _ACTIVE_BROWSERS:
        return None

    prepare_sign_park(case_id)
    park = _SIGN_PARKS.get(case_id)
    if not park:
        return None
    ev = park["event"]
    park["thread"] = threading.current_thread()
    park["waiter_alive"] = True
    try:
        deadline = time.time() + max(60.0, float(timeout_sec))
        while time.time() < deadline:
            if store.is_cancelled(case_id):
                return {"ok": False, "reason": "用户取消", "status": "cancelled"}
            if ev.wait(timeout=1.0):
                if store.is_cancelled(case_id):
                    return {"ok": False, "reason": "用户取消", "status": "cancelled"}
                if case_id not in _ACTIVE_BROWSERS:
                    return {
                        "ok": False,
                        "reason": "浏览器已关闭",
                        "error": "浏览器已关闭",
                    }
                req = park.get("request") or {}
                if not req:
                    # 被 abort 唤醒但无签名请求
                    return {
                        "ok": False,
                        "reason": "用户取消",
                        "status": "cancelled",
                    }
                result = run_sign_session(
                    case_id,
                    on_qr=req.get("on_qr"),
                    on_phase=req.get("on_phase"),
                    close_browser=True,
                )
                park["result"] = result
                return result
            # 浏览器被外部清掉
            if case_id not in _ACTIVE_BROWSERS:
                return {
                    "ok": False,
                    "reason": "浏览器已关闭",
                    "error": "浏览器已关闭",
                }
        return {"ok": False, "reason": "等待签名超时", "error": "等待签名超时"}
    finally:
        _SIGN_PARKS.pop(case_id, None)



def _execute_sign_on_browser(
    case_id: str,
    browser,
    result: dict[str, Any],
    *,
    on_qr: Optional[Callable[[str, str], None]] = None,
    on_phase: Optional[Callable[[str], None]] = None,
    close_browser: bool = True,
    keep_browser_on_incomplete: bool = True,
) -> dict[str, Any]:
    """在已有 browser 上跑签名并统一收尾（含 SIGN_DONE 不被 Target closed 覆盖）。"""
    from apps.api import statuses, store
    from apps.worker.playbooks.e_sign import run_e_sign_flow

    store.consume_cancel(case_id)
    try:
        sign_result = run_e_sign_flow(
            case_id, browser, on_qr=on_qr, on_phase=on_phase
        )
        result.update(sign_result)
        fresh = store.get_case(case_id)
        result["status"] = fresh.get("status")
        result["reason"] = sign_result.get("error") or fresh.get("reason") or ""
        result["ok"] = bool(sign_result.get("ok")) or str(fresh.get("status") or "") == (
            statuses.SIGN_DONE
        )
    except Exception as exc:
        fresh = {}
        try:
            fresh = store.get_case(case_id) or {}
        except Exception:
            fresh = {}
        st_now = str(fresh.get("status") or "")
        # 签名已成功后关窗/标签关闭会抛 Target closed：勿覆盖为失败
        if st_now == statuses.SIGN_DONE:
            result["ok"] = True
            result["status"] = statuses.SIGN_DONE
            result["reason"] = ""
            result["error"] = None
        elif store.is_cancelled(case_id) or "cancelled" in str(exc).lower():
            store.set_status(
                case_id,
                statuses.CANCELLED,
                customer_message="已取消电子签名",
                reason="用户取消",
            )
            result["reason"] = "用户取消"
            result["status"] = statuses.CANCELLED
        else:
            store.set_status(
                case_id,
                statuses.FAILED,
                customer_message="电子签名失败，请重试或联系客服",
                reason=str(exc),
            )
            result["reason"] = str(exc)
            result["status"] = statuses.FAILED
            result["error"] = str(exc)
    finally:
        if close_browser and (
            result.get("ok")
            or str(result.get("status") or "")
            in (statuses.SIGN_DONE, statuses.CANCELLED)
        ):
            try:
                abort_fill_browser(case_id, kill_all=False)
            except Exception:
                pass
        elif keep_browser_on_incomplete and browser is not None:
            _ACTIVE_BROWSERS[case_id] = browser

    return result


def run_sign_session_rebind(
    case_id: str,
    *,
    on_qr: Optional[Callable[[str, str], None]] = None,
    on_phase: Optional[Callable[[str], None]] = None,
    headless: bool = False,
    close_browser: bool = True,
) -> dict[str, Any]:
    """挂起丢失时：关旧窗并用同一 Chrome profile 重开再跑签名（同线程安全）。"""
    if str(ROOT) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(ROOT))

    from apps.api import statuses, store
    from apps.worker import config
    from playwright.sync_api import sync_playwright

    result: dict[str, Any] = {
        "ok": False,
        "caseId": case_id,
        "status": None,
        "reason": None,
        "rebind": True,
    }
    store.consume_cancel(case_id)
    # 先清挂起与旧窗，保留 profile（勿 wipe）
    _SIGN_PARKS.pop(case_id, None)
    abort_fill_browser(case_id, kill_all=False)
    time.sleep(1.2)

    profile_dir = _chrome_profile_dir(case_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = None
    browser = None
    try:
        playwright = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": headless,
            "channel": (config.PLAYWRIGHT_CHANNEL or "chrome"),
            "locale": "zh-CN",
            "viewport": {"width": 1360, "height": 900},
            "accept_downloads": False,
        }
        try:
            browser = playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception:
            launch_kwargs.pop("channel", None)
            browser = playwright.chromium.launch_persistent_context(**launch_kwargs)
        _ACTIVE_BROWSERS[case_id] = browser
        _ACTIVE_BROWSER_PROFILES[case_id] = str(profile_dir)
        pid = _browser_pid(browser)
        if pid:
            _ACTIVE_BROWSER_PIDS[case_id] = pid

        page = None
        try:
            pages = list(browser.pages)
            page = pages[0] if pages else browser.new_page()
        except Exception:
            page = browser.new_page()

        # 先到登记在线/门户，便于点到「进度跟踪」
        for url in (
            getattr(config, "YCT_ONLINE_URL", None)
            or "https://online.yct.sh.gov.cn/web-yct-master/",
            config.YCT_PORTAL_URL,
        ):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(1500)
                break
            except Exception:
                continue

        _execute_sign_on_browser(
            case_id,
            browser,
            result,
            on_qr=on_qr,
            on_phase=on_phase,
            close_browser=close_browser,
            keep_browser_on_incomplete=False,
        )
    except Exception as exc:
        # 启动 rebind / 导航阶段失败（签名执行异常已在 _execute_sign_on_browser 处理）
        if not result.get("status"):
            fresh = {}
            try:
                fresh = store.get_case(case_id) or {}
            except Exception:
                fresh = {}
            st_now = str(fresh.get("status") or "")
            if st_now == statuses.SIGN_DONE:
                result["ok"] = True
                result["status"] = statuses.SIGN_DONE
                result["reason"] = ""
                result["error"] = None
            elif store.is_cancelled(case_id) or "cancelled" in str(exc).lower():
                store.set_status(
                    case_id,
                    statuses.CANCELLED,
                    customer_message="已取消电子签名",
                    reason="用户取消",
                )
                result["reason"] = "用户取消"
                result["status"] = statuses.CANCELLED
            else:
                store.set_status(
                    case_id,
                    statuses.FAILED,
                    customer_message="电子签名失败，请重试或联系客服",
                    reason=str(exc),
                )
                result["reason"] = str(exc)
                result["status"] = statuses.FAILED
                result["error"] = str(exc)
    finally:
        if playwright is not None and (
            result.get("ok")
            or str(result.get("status") or "")
            in (statuses.SIGN_DONE, statuses.CANCELLED, statuses.FAILED)
        ):
            # 失败也尽量停掉 playwright，避免泄漏；成功路径 abort 已关窗
            try:
                if case_id not in _ACTIVE_BROWSERS:
                    playwright.stop()
            except Exception:
                pass

    return result


def run_sign_session(
    case_id: str,
    *,
    on_qr: Optional[Callable[[str, str], None]] = None,
    on_phase: Optional[Callable[[str], None]] = None,
    close_browser: bool = True,
) -> dict[str, Any]:
    """在已保留的浏览器上跑电子签名；成功后默认关窗。"""
    if str(ROOT) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(ROOT))

    from apps.api import statuses, store

    browser = _ACTIVE_BROWSERS.get(case_id)
    result: dict[str, Any] = {
        "ok": False,
        "caseId": case_id,
        "status": None,
        "reason": None,
    }
    if browser is None:
        result["error"] = "无可用浏览器，请先完成填报并保持会话"
        result["reason"] = result["error"]
        store.set_status(
            case_id,
            statuses.FAILED,
            customer_message=result["error"],
            reason=result["error"],
        )
        return result

    return _execute_sign_on_browser(
        case_id,
        browser,
        result,
        on_qr=on_qr,
        on_phase=on_phase,
        close_browser=close_browser,
        keep_browser_on_incomplete=True,
    )
