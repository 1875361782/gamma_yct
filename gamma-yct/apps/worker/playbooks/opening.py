"""企业开办 / 后续办理方式（新增，不改核名/基本信息/出资/人员代码）。

人员信息「已完成进入下一步」之后：
1. 若尚未进入开办页 → 点「下一步」进入企业开办
2. 后续办理方式：全程网办 → 不需要材料辅导
3. 文书产生方式：智慧文书
4. 纸质执照：无需领取纸质营业执照
5. 下一步 → 下一步
6. 自费刻章，设立时(候)暂不刻章
7. 下一步 → 下一步 → 暂存
禁止最终提交/签名。
"""

from __future__ import annotations

import json
import re
from typing import Any

from playwright.sync_api import Frame, Page

from apps.api import statuses, store


def _shot(page: Page, case_id: str, name: str) -> None:
    path = store.case_dir(case_id) / "screenshots" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        pass


def _dump(case_id: str, name: str, data: Any) -> None:
    path = store.case_dir(case_id) / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _visible(loc, timeout: int = 800) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible(timeout=timeout)
    except Exception:
        return False


def _all_scopes(page: Page) -> list[Page | Frame]:
    scopes: list[Page | Frame] = [page]
    for fr in page.frames:
        if fr not in scopes:
            scopes.append(fr)
    return scopes


def _body(scope: Page | Frame):
    try:
        w = scope.locator("wujie-app body")
        if w.count() > 0:
            return w.first
    except Exception:
        pass
    return scope.locator("body").first


def _click_text(scope: Page | Frame, texts: list[str], *, exact: bool = False) -> bool:
    for t in texts:
        try:
            loc = scope.get_by_role("button", name=t).first
            if _visible(loc):
                loc.click(timeout=5000)
                return True
        except Exception:
            pass
        try:
            loc = scope.get_by_text(t, exact=exact).first
            if _visible(loc):
                loc.click(timeout=5000)
                return True
        except Exception:
            pass
    return False


def _click_text_any(page: Page, texts: list[str], *, exact: bool = False) -> bool:
    for scope in _all_scopes(page):
        if _click_text(scope, texts, exact=exact):
            return True
    return False


def _page_has(page: Page, marker: str) -> bool:
    for scope in _all_scopes(page):
        try:
            if scope.get_by_text(marker, exact=False).count() > 0:
                loc = scope.get_by_text(marker, exact=False).first
                if _visible(loc, timeout=400):
                    return True
        except Exception:
            continue
        try:
            t = _body(scope).inner_text(timeout=800) or ""
            if marker in t:
                return True
        except Exception:
            continue
    return False


def _click_radio(page: Page, needles: list[str]) -> bool:
    """点 el-radio / label，按文案包含匹配（优先最短可见项）。"""
    for scope in _all_scopes(page):
        for needle in needles:
            try:
                labels = scope.locator("label.el-radio, label").filter(
                    has_text=re.compile(re.escape(needle))
                )
                n = min(labels.count(), 12)
                best = None
                best_len = 10**9
                for i in range(n):
                    lab = labels.nth(i)
                    if not _visible(lab, timeout=300):
                        continue
                    txt = " ".join((lab.inner_text(timeout=400) or "").split())
                    if needle not in txt:
                        continue
                    if len(txt) < best_len:
                        best = lab
                        best_len = len(txt)
                if best is not None:
                    try:
                        inp = best.locator("input[type='radio']").first
                        if inp.count() > 0:
                            inp.check(force=True, timeout=2000)
                        else:
                            best.click(timeout=3000)
                    except Exception:
                        best.click(force=True, timeout=3000)
                    return True
            except Exception:
                continue
            # 兜底：直接点文案
            try:
                loc = scope.get_by_text(needle, exact=False).first
                if _visible(loc, timeout=400):
                    lab = loc.locator("xpath=ancestor::label[1]").first
                    (lab if _visible(lab, timeout=200) else loc).click(timeout=3000)
                    return True
            except Exception:
                continue
    return False


def _radio_checked(page: Page, needle: str) -> bool:
    for scope in _all_scopes(page):
        try:
            lab = scope.locator("label.el-radio, label").filter(
                has_text=re.compile(re.escape(needle))
            ).first
            if not _visible(lab, timeout=400):
                continue
            cls = lab.get_attribute("class") or ""
            if "is-checked" in cls:
                return True
            inp = lab.locator("input[type='radio']").first
            if inp.count() > 0 and inp.is_checked():
                return True
        except Exception:
            continue
    return False


def _click_footer_next(page: Page) -> bool:
    """点页脚「下一步」（避开弹层里的下一步）。"""
    for scope in _all_scopes(page):
        try:
            for sel in (
                ".footer button",
                ".el-footer button",
                ".page-footer button",
                ".yd-footer button",
                "button",
            ):
                btns = scope.locator(sel).filter(has_text=re.compile(r"^\s*下一步\s*$"))
                n = min(btns.count(), 8)
                for i in range(n - 1, -1, -1):
                    btn = btns.nth(i)
                    if _visible(btn, timeout=300):
                        btn.click(timeout=4000)
                        return True
        except Exception:
            continue
    return _click_text_any(page, ["下一步"], exact=True)


def _click_save_draft(page: Page) -> bool:
    # 等页脚按钮出现（白屏后才渲染）
    _wait_any(page, ["暂存"], timeout_sec=12)
    for scope in _all_scopes(page):
        try:
            btn = scope.get_by_role("button", name=re.compile(r"^\s*暂存\s*$")).first
            if _visible(btn, timeout=600):
                btn.click(timeout=4000)
                return True
        except Exception:
            pass
        try:
            btns = scope.locator("button").filter(has_text=re.compile(r"暂存"))
            n = min(btns.count(), 8)
            for i in range(n):
                btn = btns.nth(i)
                if not _visible(btn, timeout=300):
                    continue
                txt = " ".join((btn.inner_text(timeout=400) or "").split())
                if txt == "暂存" or txt.startswith("暂存"):
                    btn.click(force=True, timeout=4000)
                    return True
        except Exception:
            continue
    return _click_text_any(page, ["暂存"], exact=True)


def _wait_any(page: Page, markers: list[str], timeout_sec: float = 20) -> str | None:
    """等任一文案出现在可见内容里（度过 wujie 白屏）。"""
    import time

    end = time.time() + timeout_sec
    while time.time() < end:
        for m in markers:
            if _page_has(page, m):
                return m
        page.wait_for_timeout(400)
    return None


def _on_process_page(page: Page) -> bool:
    return _page_has(page, "后续办理方式") or (
        _page_has(page, "全程网办") and _page_has(page, "文书产生方式")
    )


def _on_social_page(page: Page) -> bool:
    # 勿只用侧栏「五险一金」判页
    return _page_has(page, "本次是否办理五险一金")


def _on_seal_page(page: Page) -> bool:
    # 侧栏一直有「公章刻制」，必须看到刻章表单选项
    return (
        _page_has(page, "自费刻章")
        or _page_has(page, "设立时候暂不刻章")
        or _page_has(page, "设立时暂不刻章")
        or _page_has(page, "暂不刻章")
        or (_page_has(page, "刻制公章") and _page_has(page, "下一步"))
    )


def _click_next_and_wait(
    page: Page,
    markers: list[str],
    *,
    timeout_sec: float = 22,
    leave_markers: list[str] | None = None,
) -> bool:
    """点下一步并等到目标文案；若仍停在 leave_markers 页则视为失败。"""
    before = ""
    try:
        before = page.url or ""
    except Exception:
        pass
    if not (_click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)):
        return False
    page.wait_for_timeout(1000)
    hit = _wait_any(page, markers, timeout_sec=timeout_sec)
    if leave_markers:
        # 仍停在旧页关键文案 → 未真正翻页
        if any(_page_has(page, m) for m in leave_markers) and not hit:
            # 再试点一次下一步（白屏后按钮才出来）
            page.wait_for_timeout(1200)
            _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
            page.wait_for_timeout(1000)
            hit = _wait_any(page, markers, timeout_sec=12)
        if any(_page_has(page, m) for m in leave_markers) and not (
            hit and any(_page_has(page, m) for m in markers)
        ):
            return False
    if hit:
        return True
    try:
        if (page.url or "") != before:
            hit = _wait_any(page, markers, timeout_sec=8)
            return bool(hit)
    except Exception:
        pass
    return bool(_wait_any(page, markers, timeout_sec=5))


def _enter_opening(page: Page, case_id: str, result: dict[str, Any]) -> Page:
    """人员完成后进入办理方式；已在办理方式页则跳过。"""
    if _on_process_page(page):
        result["enterSkipped"] = "already-on-opening"
        return page
    for i in range(4):
        hit = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
        result[f"enterNext{i}"] = hit
        page.wait_for_timeout(1200)
        _wait_any(page, ["后续办理方式", "全程网办", "文书产生方式"], timeout_sec=10)
        if _on_process_page(page):
            result["enteredOpening"] = True
            _shot(page, case_id, "opening-page")
            return page
        _click_text_any(page, ["已完成，进入下一步", "确定", "确认"], exact=False)
        page.wait_for_timeout(800)
        if _on_process_page(page):
            result["enteredOpening"] = True
            _shot(page, case_id, "opening-page")
            return page
    result["enteredOpening"] = _on_process_page(page)
    _shot(page, case_id, "opening-page" if result["enteredOpening"] else "opening-enter-miss")
    return page


def run_opening_flow(case_id: str, page: Page, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = payload
    store.set_status(
        case_id,
        statuses.CHOOSING_PROCESS,
        customer_message="正在填写企业开办 / 后续办理方式…",
    )
    result: dict[str, Any] = {"ok": False}
    page = _enter_opening(page, case_id, result)
    if not _on_process_page(page):
        result["error"] = "未进入企业开办 / 后续办理方式页"
        result["missing"] = ["进入企业开办"]
        _dump(case_id, "fill-opening-result.json", result)
        return result

    # 全程网办
    result["fullOnline"] = _click_radio(page, ["全程网办"]) or _radio_checked(page, "全程网办")
    page.wait_for_timeout(500)
    result["noMaterialGuide"] = _click_radio(
        page, ["不需要材料辅导", "无需登记机关对登记材料进行辅导"]
    ) or _radio_checked(page, "不需要材料辅导")
    page.wait_for_timeout(400)
    result["smartDoc"] = _click_radio(page, ["智慧文书"]) or _radio_checked(page, "智慧文书")
    page.wait_for_timeout(300)
    result["noPaperLicense"] = _click_radio(
        page, ["无需领取纸质营业执照"]
    ) or _radio_checked(page, "无需领取纸质营业执照")
    page.wait_for_timeout(400)
    _shot(page, case_id, "opening-process-selected")

    # 下一步：离开办理方式 → 五险一金（企业开办）
    result["next1"] = _click_next_and_wait(
        page,
        ["本次是否办理五险一金", "自费刻章", "设立时暂不刻章", "设立时候暂不刻章"],
        timeout_sec=25,
        leave_markers=["后续办理方式", "文书产生方式"],
    )
    _shot(page, case_id, "opening-after-next1")
    result["afterNext1"] = (
        "social"
        if _on_social_page(page)
        else ("seal" if _on_seal_page(page) else "other")
    )

    # 下一步：离开五险一金 → 公章刻制（不勾选办理五险一金）
    if not _on_seal_page(page):
        result["next2"] = _click_next_and_wait(
            page,
            ["自费刻章", "设立时候暂不刻章", "设立时暂不刻章", "暂不刻章"],
            timeout_sec=25,
            leave_markers=["本次是否办理五险一金"],
        )
    else:
        result["next2"] = True
        result["next2Skipped"] = "already-on-seal"
    _shot(page, case_id, "opening-after-next2")
    result["afterNext2Url"] = getattr(page, "url", "") or ""

    # 自费刻章，设立时(候)暂不刻章
    seal_needles = [
        "自费刻章，设立时暂不刻章（设立后自行办理）",
        "自费刻章，设立时候暂不刻章",
        "自费刻章，设立时暂不刻章",
        "设立时候暂不刻章",
        "设立时暂不刻章",
        "自费刻章",
    ]
    if not _on_seal_page(page):
        _wait_any(page, seal_needles, timeout_sec=15)
    result["selfPayNoSeal"] = _click_radio(page, seal_needles)
    if not result["selfPayNoSeal"]:
        for n in seal_needles:
            if _radio_checked(page, n):
                result["selfPayNoSeal"] = True
                break
    page.wait_for_timeout(400)
    _shot(page, case_id, "opening-seal-selected")

    # 未选到刻章选项则不要继续乱点暂存
    if not result["selfPayNoSeal"]:
        result["error"] = "未找到自费刻章/暂不刻章选项"
        result["missing"] = ["自费刻章暂不刻章"]
        result["currentUrl"] = getattr(page, "url", "") or ""
        _dump(case_id, "fill-opening-result.json", result)
        return result

    result["next3"] = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
    page.wait_for_timeout(1500)
    _wait_any(page, ["涉税", "开户", "暂存", "材料预览", "下一步"], timeout_sec=15)
    _shot(page, case_id, "opening-after-next3")

    result["next4"] = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
    page.wait_for_timeout(1500)
    # 第二下后常见停在开户预约
    _wait_any(
        page,
        ["本次同步办理预约银行开户", "开户预约", "暂存", "材料预览"],
        timeout_sec=18,
    )
    _shot(page, case_id, "opening-after-next4")

    # 暂存（不提交）；失败则重试一次
    result["saved"] = _click_save_draft(page)
    page.wait_for_timeout(1200)
    if not result["saved"]:
        page.wait_for_timeout(1000)
        result["saved"] = _click_save_draft(page)
        page.wait_for_timeout(1200)
    if _page_has(page, "暂存成功") or _page_has(page, "数据暂存成功"):
        result["saved"] = True
        result["savedAck"] = True
    _shot(page, case_id, "opening-after-save")

    missing = []
    if not result.get("fullOnline"):
        missing.append("全程网办")
    if not result.get("noMaterialGuide"):
        missing.append("不需要材料辅导")
    if not result.get("smartDoc"):
        missing.append("智慧文书")
    if not result.get("noPaperLicense"):
        missing.append("无需领取纸质营业执照")
    if not result.get("next1") or not result.get("next2"):
        missing.append("办理方式下一步")
    if not result.get("selfPayNoSeal"):
        missing.append("自费刻章暂不刻章")
    if not result.get("next3") or not result.get("next4"):
        missing.append("刻章后下一步")
    if not result.get("saved"):
        missing.append("暂存")
    result["missing"] = missing
    result["ok"] = not missing
    result["currentUrl"] = getattr(page, "url", "") or ""
    _dump(case_id, "fill-opening-result.json", result)
    return result