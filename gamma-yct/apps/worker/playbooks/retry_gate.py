"""Stage-level retry for fill pipeline.

失败立即重试；同一阶段自动 3 次仍失败则置 need_human 并结束，
由前端提示「系统异常，请点击重试或返回上一页」。

出现「系统繁忙」弹窗时：点确定关掉，不占用普通失败次数，并重试当前阶段。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from playwright.sync_api import Frame, Page

from apps.api import statuses, store

DEFAULT_AUTO_RETRIES = 3
# 「系统繁忙」额外重试预算（不占用普通 3 次）
BUSY_BONUS_RETRIES = 5
# 失败后立即再试，仅短暂让页面喘息
RETRY_PAUSE_MS = 400
BUSY_PAUSE_MS = 1500
SYSTEM_FAIL_TIP = "系统异常，请点击重试或返回上一页。"


def _result_mentions_busy(result: Optional[dict[str, Any]]) -> bool:
    if not result:
        return False
    blob = " ".join(
        [
            str(result.get("error") or ""),
            str(result.get("reason") or ""),
            "、".join(str(x) for x in (result.get("missing") or [])),
        ]
    )
    return "系统繁忙" in blob or "稍后重试" in blob


def _shot(page: Page, case_id: str, name: str) -> None:
    path = store.case_dir(case_id) / "screenshots" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        try:
            page.screenshot(path=str(path), full_page=False)
        except Exception:
            pass


def _visible(loc: Any) -> bool:
    try:
        return bool(loc.count() > 0 and loc.is_visible())
    except Exception:
        return False


def _all_scopes(page: Page) -> list[Any]:
    scopes: list[Any] = [page]
    try:
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
    except Exception:
        pass
    return scopes


def dismiss_system_busy(page: Page) -> bool:
    """关闭「系统繁忙，请稍后重试」类弹窗（点确定）。返回是否关掉过。

    绝不能误关「比对结果 / 告知书」等核名流程弹窗。
    关窗后由调用方重试当前步骤/阶段。
    """
    dismissed = False
    protected = ("比对结果", "已知晓该名称", "告知书", "承诺书", "温馨提示", "返回修改")
    busy_re = re.compile(r"系统繁忙|请稍后重试")
    ok_re = re.compile(r"确\s*定")

    def _click_ok(box) -> bool:
        for sel in (
            ".el-message-box__btns button.el-button--primary",
            ".el-dialog__footer button.el-button--primary",
            "button.el-button--primary",
            "button, .el-button",
        ):
            try:
                btn = box.locator(sel).filter(has_text=ok_re).last
                if not _visible(btn):
                    continue
                try:
                    btn.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass
                try:
                    box_bb = btn.bounding_box()
                    if box_bb and page is not None:
                        page.mouse.click(
                            box_bb["x"] + box_bb["width"] / 2,
                            box_bb["y"] + box_bb["height"] / 2,
                        )
                        return True
                except Exception:
                    pass
                btn.click(force=True, timeout=3000)
                return True
            except Exception:
                continue
        return False

    # 可能叠多层繁忙弹窗，最多清 4 次
    for _ in range(4):
        hit = False
        for scope in _all_scopes(page):
            candidates = []
            for sel in (
                ".el-message-box__wrapper",
                ".el-message-box",
                ".el-overlay-message-box",
                ".el-dialog__wrapper",
                ".el-dialog",
                "[role='dialog']",
                ".el-message-box__content",
            ):
                try:
                    loc = scope.locator(sel)
                    n = min(loc.count(), 8)
                except Exception:
                    continue
                for i in range(n):
                    candidates.append(loc.nth(i))

            # 文案锚点 → 上溯弹窗容器
            try:
                tloc = scope.get_by_text(re.compile(r"系统繁忙"), exact=False)
                tn = min(tloc.count(), 4)
                for i in range(tn):
                    node = tloc.nth(i)
                    if not _visible(node):
                        continue
                    for xp in (
                        "xpath=ancestor::div[contains(@class,'el-message-box')][1]",
                        "xpath=ancestor::div[contains(@class,'el-dialog')][1]",
                        "xpath=ancestor::*[@role='dialog'][1]",
                        "xpath=ancestor::div[contains(@class,'el-message-box__wrapper')][1]",
                    ):
                        try:
                            candidates.append(node.locator(xp).first)
                        except Exception:
                            pass
            except Exception:
                pass

            for box in candidates:
                try:
                    if not _visible(box):
                        continue
                    text = box.inner_text(timeout=500) or ""
                except Exception:
                    continue
                if any(p in text for p in protected):
                    continue
                if not busy_re.search(text):
                    continue
                if _click_ok(box):
                    hit = True
                    dismissed = True
                    break
            if hit:
                break

        if not hit:
            break
        try:
            page.wait_for_timeout(350)
        except Exception:
            break

    if dismissed:
        try:
            page.wait_for_timeout(400)
        except Exception:
            pass
    return dismissed


def recover_alive_page(page: Page) -> Page:
    try:
        if not page.is_closed():
            return page
    except Exception:
        pass
    try:
        for p in list(page.context.pages):
            if p.is_closed():
                continue
            url = p.url or ""
            if "web-yct-master" in url or "chief-executive" in url or "web-reg" in url:
                try:
                    p.bring_to_front()
                except Exception:
                    pass
                return p
        for p in list(page.context.pages):
            if not p.is_closed():
                return p
    except Exception:
        pass
    return page


def mark_system_fail(
    case_id: str,
    *,
    reason: str,
    result: Optional[dict[str, Any]] = None,
) -> None:
    store.set_status(
        case_id,
        statuses.NEED_HUMAN,
        reason=reason,
        result=result,
        customer_message=SYSTEM_FAIL_TIP,
    )


def run_stage_with_retry(
    case_id: str,
    page: Page,
    *,
    stage_key: str,
    stage_label: str,
    runner: Callable[[Page], dict[str, Any]],
    base_result: Optional[dict[str, Any]] = None,
    auto_retries: int = DEFAULT_AUTO_RETRIES,
    filling_status: str = statuses.FILLING_ESTABLISHMENT,
    dump_name: Optional[str] = None,
) -> tuple[dict[str, Any], Page, str]:
    """失败立即重试；``auto_retries`` 次仍失败则返回 ``failed``。

    「系统繁忙」关掉后重试当前阶段，不占用普通失败次数（另有 bonus 上限）。

    Returns ``(result, page, outcome)`` where outcome is ``ok`` | ``cancelled`` | ``failed``.
    """
    last: dict[str, Any] = {"ok": False, "error": f"{stage_label}未开始"}
    attempt = 0
    busy_used = 0

    while attempt < auto_retries:
        if store.is_cancelled(case_id) or store.consume_cancel(case_id):
            store.set_status(
                case_id,
                statuses.CANCELLED,
                customer_message="已取消填报",
                reason="用户取消",
            )
            return {**last, "ok": False, "error": "用户取消"}, page, "cancelled"

        # 开跑前清掉残留繁忙弹窗
        if dismiss_system_busy(page):
            try:
                page.wait_for_timeout(BUSY_PAUSE_MS)
            except Exception:
                page = recover_alive_page(page)

        display_n = attempt + 1
        store.set_status(
            case_id,
            filling_status,
            result=base_result,
            customer_message=f"正在填写{stage_label}…（自动第 {display_n}/{auto_retries} 次）",
        )
        busy_hit = False
        try:
            page = recover_alive_page(page)
            last = runner(page) or {"ok": False, "error": "无返回"}
            if dump_name:
                path = store.case_dir(case_id) / dump_name
                path.write_text(
                    json.dumps(last, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if last.get("ok"):
                # 成功前仍清掉可能挡下一步的繁忙弹窗
                if dismiss_system_busy(page):
                    try:
                        page.wait_for_timeout(BUSY_PAUSE_MS)
                    except Exception:
                        page = recover_alive_page(page)
                return last, page, "ok"

            # 核名业务失败：不占用阶段重试，直接结束
            if last.get("nameFailed") or str(
                store.get_case(case_id).get("status") or ""
            ) == statuses.NAME_FAILED:
                return {**last, "ok": False}, page, "failed"

            # 失败后先关繁忙弹窗，再决定是否占用失败次数
            busy_hit = dismiss_system_busy(page) or _result_mentions_busy(last)
            missing = last.get("missing") or []
            detail = (
                "、".join(missing)
                if missing
                else (last.get("error") or last.get("reason") or "未知")
            )
            if busy_hit:
                store.set_status(
                    case_id,
                    filling_status,
                    result={**(base_result or {}), stage_key: last},
                    customer_message=f"{stage_label}遇到系统繁忙，已关闭弹窗并重试…",
                )
            else:
                store.set_status(
                    case_id,
                    filling_status,
                    result={**(base_result or {}), stage_key: last},
                    customer_message=(
                        f"{stage_label}未完成（{detail}），立即重试 {display_n}/{auto_retries}"
                    ),
                )
        except Exception as e:
            last = {"ok": False, "error": str(e)}
            msg = str(e)
            busy_hit = dismiss_system_busy(page) or ("系统繁忙" in msg) or ("稍后重试" in msg)
            if "closed" in msg.lower() or "Target page" in msg or "browser has been" in msg:
                page = recover_alive_page(page)
                _shot(page, case_id, f"{stage_key}-recovered-{display_n}")
            else:
                _shot(page, case_id, f"{stage_key}-error-{display_n}")
            if busy_hit:
                store.set_status(
                    case_id,
                    filling_status,
                    result={**(base_result or {}), stage_key: last},
                    customer_message=f"{stage_label}遇到系统繁忙，已关闭弹窗并重试…",
                )
            else:
                store.set_status(
                    case_id,
                    filling_status,
                    result={**(base_result or {}), stage_key: last},
                    customer_message=(
                        f"{stage_label}异常（{e}），立即重试 {display_n}/{auto_retries}"
                    ),
                )

        if busy_hit and busy_used < BUSY_BONUS_RETRIES:
            busy_used += 1
            try:
                page.wait_for_timeout(BUSY_PAUSE_MS)
            except Exception:
                page = recover_alive_page(page)
            continue

        attempt += 1
        if attempt < auto_retries:
            try:
                page.wait_for_timeout(RETRY_PAUSE_MS if not busy_hit else BUSY_PAUSE_MS)
            except Exception:
                page = recover_alive_page(page)

    missing = last.get("missing") or []
    reason = (
        f"{stage_label}未完成："
        + (
            "、".join(missing)
            if missing
            else (last.get("error") or last.get("reason") or "请查看截图")
        )
    )
    _shot(page, case_id, f"{stage_key}-need-human")
    mark_system_fail(
        case_id,
        reason=reason,
        result={**(base_result or {}), stage_key: last, "currentUrl": getattr(page, "url", "")},
    )
    return {**last, "ok": False}, page, "failed"
