"""Name declaration orchestrator.

Runs name declaration, then hands off to basic_info / capital / personnel /
opening. Does not perform final submit/sign.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

import yaml
from playwright.sync_api import Frame, Page

from apps.api import statuses, store
from apps.worker import config

SELECTORS_PATH = Path(__file__).with_name("selectors.yaml")
_META_ONLY_KEYS = {
    "success_text",
    "fail_text",
    "skip_if_empty",
    "use_heuristics",
    "pages",
    "defaults",
    "placeholders",
    "rare_char_fail",
    "frame_url_part",
    "entry_url_hint",
    "authority_suffix",
    "company_type_one_person",
    "business_term",
    "agent_type",
    "save_button_text",
    "save_success_text",
    "org_type_option",
    "org_form_option",
    "next_button_text",
    "id_upload",
    "business_scope",
}


def _load_selectors() -> dict[str, Any]:
    if not SELECTORS_PATH.exists():
        return {}
    return yaml.safe_load(SELECTORS_PATH.read_text(encoding="utf-8")) or {}


def _configured(block: dict[str, Any] | None) -> bool:
    if not block:
        return False
    if block.get("use_heuristics") is True or block.get("entry_url"):
        return True
    for k, v in block.items():
        if k in _META_ONLY_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            return True
        if isinstance(v, list) and v:
            return True
    return False


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


def _dump_json(case_id: str, name: str, data: Any) -> None:
    path = store.case_dir(case_id) / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _all_frames(page: Page) -> list[Frame]:
    frames = [page.main_frame]
    for fr in page.frames:
        if fr not in frames:
            frames.append(fr)
    return frames


def _frame_by_url(page: Page, part: str, timeout_ms: int = 30000) -> Frame | None:
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        for fr in _all_frames(page):
            if part in (fr.url or ""):
                return fr
        page.wait_for_timeout(500)
    return None


def _visible(locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible(timeout=600)
    except Exception:
        return False


def _dismiss(page: Page, frame: Frame | None = None, selectors: Iterable[str] | None = None) -> None:
    targets: list[Page | Frame] = [page]
    if frame is not None:
        targets.insert(0, frame)
    sels = list(selectors or []) + [
        "text=暂不需要",
        "text=我知道了",
        "text=关闭",
        ".el-dialog__headerbtn",
        ".el-message-box__headerbtn",
        "[aria-label='Close']",
    ]
    for _ in range(4):
        closed = False
        for target in targets:
            for sel in sels:
                try:
                    loc = target.locator(sel).first
                    if _visible(loc):
                        loc.click(timeout=2000)
                        page.wait_for_timeout(350)
                        closed = True
                except Exception:
                    continue
        if not closed:
            break


def _dismiss_system_busy(page: Page) -> bool:
    """关闭「系统繁忙，请稍后重试」类弹窗。"""
    dismissed = False
    markers = ("系统繁忙", "请稍后重试", "请稍后再试")
    for scope in _all_frames(page):
        try:
            hit = False
            for m in markers:
                if scope.get_by_text(m, exact=False).count() > 0:
                    hit = True
                    break
            if not hit:
                continue
            dlg = scope.locator(".el-message-box, .el-dialog, .el-overlay-message-box").filter(
                has_text=re.compile("系统繁忙|稍后")
            ).first
            btn = None
            if _visible(dlg):
                btn = dlg.locator("button, .el-button").filter(has_text="确定").first
            if not _visible(btn):
                btn = scope.get_by_role("button", name="确定").first
            if _visible(btn):
                btn.click(force=True, timeout=3000)
                dismissed = True
            else:
                # 点任何可见确定
                if _click_text(scope, ["确定"], exact=True):
                    dismissed = True
        except Exception:
            continue
    return dismissed


def _click_text(scope: Page | Frame, texts: Iterable[str], exact: bool = False) -> bool:
    for t in texts:
        if not t:
            continue
        try:
            loc = scope.get_by_text(t, exact=exact).first
            if _visible(loc):
                loc.click(timeout=5000)
                return True
        except Exception:
            continue
        try:
            loc = scope.locator(f"text={t}").first
            if _visible(loc):
                loc.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def _fill_placeholder(frame: Frame, placeholder: str, value: str) -> bool:
    if not value:
        return False
    try:
        loc = frame.get_by_placeholder(placeholder, exact=False).first
        if not _visible(loc):
            return False
        loc.click(timeout=3000)
        loc.fill("")
        loc.fill(value, timeout=5000)
        return True
    except Exception:
        return False


def _open_select_by_placeholder(frame: Frame, placeholder: str) -> bool:
    try:
        inp = frame.get_by_placeholder(placeholder, exact=False).first
        if not _visible(inp):
            return False
        # Element Plus：点输入或后缀箭头
        try:
            inp.locator("xpath=ancestor::div[contains(@class,'el-select')][1]").click(timeout=3000)
        except Exception:
            inp.click(timeout=3000)
        return True
    except Exception:
        return False


def _pick_dropdown_option(frame: Frame, option_text: str) -> bool:
    """下拉项可能挂在 iframe 或主文档。"""
    scopes: list[Page | Frame] = [frame]
    try:
        scopes.append(frame.page)
    except Exception:
        pass
    for scope in scopes:
        try:
            opt = scope.locator(".el-select-dropdown:visible .el-select-dropdown__item").filter(
                has_text=option_text
            ).first
            if _visible(opt):
                opt.click(timeout=4000)
                return True
        except Exception:
            pass
        try:
            opt = scope.get_by_text(option_text, exact=True).first
            if _visible(opt):
                opt.click(timeout=4000)
                return True
        except Exception:
            pass
    return False


# ---------- name declaration ----------


def _visible_human_confirmation(page: Page, frame: Frame | None = None) -> str | None:
    """Return a legal/identity confirmation message without clicking it."""
    scopes: list[Any] = [page]
    if frame is not None:
        scopes.insert(0, frame)
    keywords = ("承诺书", "声明", "知情同意", "实名", "我已详细阅读", "法定代表人确认")
    for scope in scopes:
        try:
            dialogs = scope.locator(".el-dialog:visible, .el-message-box:visible")
            for i in range(min(dialogs.count(), 5)):
                dialog = dialogs.nth(i)
                text = (dialog.inner_text(timeout=500) or "").strip()
                if any(key in text for key in keywords):
                    return text[:500]
        except Exception:
            continue
        try:
            body = (scope.locator("body").inner_text(timeout=500) or "").strip()
            if "已知晓该名称" in body or "承诺书" in body or "我已详细阅读" in body:
                return body[:500]
        except Exception:
            pass
    return None


def _dismiss_known_info_dialog(page: Page, frame: Frame | None = None) -> bool:
    """Close only explicitly informational dialogs; never close legal dialogs."""
    scopes: list[Any] = [page]
    if frame is not None:
        scopes.insert(0, frame)
    legal_words = ("承诺", "声明", "知情同意", "实名", "我已详细阅读")
    safe_words = ("比对通过", "操作成功", "提示")
    for scope in scopes:
        try:
            dialogs = scope.locator(".el-dialog:visible, .el-message-box:visible")
            for i in range(min(dialogs.count(), 5)):
                dialog = dialogs.nth(i)
                text = (dialog.inner_text(timeout=500) or "").strip()
                if not any(word in text for word in safe_words) or any(word in text for word in legal_words):
                    continue
                button = dialog.get_by_role("button", name="确定").first
                if not _visible(button):
                    button = dialog.get_by_text("确定", exact=True).first
                if _visible(button):
                    button.click(timeout=2000)
                    page.wait_for_timeout(300)
                    return True
        except Exception:
            continue
    return False


def _select_org_form_two_step(frame: Frame, type_opt: str, form_opt: str) -> bool:
    """组织形式弹层：先点内资「有限公司」，再点组织形式「有限责任公司」。"""
    page = frame.page
    page.wait_for_timeout(500)
    scopes: list[Any] = []
    for sel in (
        ".orgDescPopper:visible",
        ".el-popover:visible",
        ".el-dialog:visible",
        ".el-popper:visible",
    ):
        loc = frame.locator(sel).first
        if _visible(loc):
            scopes.append(loc)
        loc2 = page.locator(sel).first
        if _visible(loc2):
            scopes.append(loc2)
    scopes.extend([frame, page])

    def _click_type(scope) -> bool:
        # 只点「内资企业」下的有限公司，绝不点外资
        try:
            label = scope.get_by_text("内资企业", exact=True).first
            if _visible(label):
                block = label.locator("xpath=following-sibling::*[1]")
                btn = block.get_by_text(type_opt, exact=True).first
                if _visible(btn):
                    btn.click(timeout=3000)
                    return True
                parent = label.locator("xpath=..")
                btn = parent.get_by_text(type_opt, exact=True).first
                if _visible(btn):
                    btn.click(timeout=3000)
                    return True
        except Exception:
            pass
        try:
            domestic = scope.locator("div,section").filter(has_text="内资企业").filter(
                has_not_text="外资企业"
            )
            for i in range(min(domestic.count(), 8)):
                block = domestic.nth(i)
                btn = block.get_by_text(type_opt, exact=True).first
                if _visible(btn):
                    btn.click(timeout=3000)
                    return True
        except Exception:
            pass
        return False

    def _click_form(scope) -> bool:
        # 在「2. 请选择组织形式」下点「有限责任公司」
        try:
            heading = scope.get_by_text("请选择组织形式", exact=False).first
            if _visible(heading):
                container = heading.locator("xpath=following-sibling::*[1]")
                btn = container.get_by_text(form_opt, exact=True).first
                if _visible(btn):
                    btn.click(timeout=3000)
                    return True
                parent = heading.locator("xpath=..")
                btn = parent.get_by_text(form_opt, exact=True).first
                if _visible(btn):
                    btn.click(timeout=3000)
                    return True
        except Exception:
            pass
        try:
            btn = scope.get_by_text(form_opt, exact=True).first
            if _visible(btn):
                btn.click(timeout=3000)
                return True
        except Exception:
            pass
        return False

    typed = formed = False
    for scope in scopes:
        if not typed:
            typed = _click_type(scope)
            if typed:
                page.wait_for_timeout(600)
        if typed and not formed:
            formed = _click_form(scope)
            if formed:
                page.wait_for_timeout(400)
                break
    if not (typed and formed):
        return False

    # 确认弹窗
    _click_text(frame, ["确 定", "确定"], exact=False)
    _click_text(page, ["确 定", "确定"], exact=False)
    page.wait_for_timeout(500)
    return True


def _choose_first_industry_suggestion(frame: Frame, industry: str) -> tuple[bool, str]:
    """点击第一条包含行业表述的自动完成项（如第一个「科技」）。必须点选，不能只靠手输。"""
    if not industry:
        return False, "industry_empty"
    page = frame.page
    try:
        scopes: list[Any] = [frame, page]
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
        list_selectors = (
            ".el-autocomplete-suggestion:visible li",
            ".el-autocomplete-suggestion li",
            "[id*='el-autocomplete'] li",
            ".el-select-dropdown:visible .el-select-dropdown__item",
            ".el-popper:visible li",
            ".el-popper:visible .el-select-dropdown__item",
            "ul[role='listbox'] li",
            ".el-scrollbar__view li",
            ".el-popper li",
        )
        # 多等一会儿让建议出来
        for _wait in range(8):
            for scope in scopes:
                prefer = scope.get_by_text("主营行业", exact=False)
                for i in range(min(prefer.count(), 12)):
                    item = prefer.nth(i)
                    if not _visible(item):
                        continue
                    text = " ".join((item.inner_text(timeout=400) or "").split())
                    if industry in text:
                        try:
                            item.click(timeout=3000, force=True)
                        except Exception:
                            box = item.bounding_box()
                            if box:
                                page.mouse.click(box["x"] + 20, box["y"] + 10)
                        page.wait_for_timeout(400)
                        return True, text
                for sel in list_selectors:
                    items = scope.locator(sel)
                    for i in range(min(items.count(), 20)):
                        item = items.nth(i)
                        if not _visible(item):
                            continue
                        text = " ".join((item.inner_text(timeout=400) or "").split())
                        if industry in text:
                            try:
                                item.click(timeout=3000, force=True)
                            except Exception:
                                box = item.bounding_box()
                                if box:
                                    page.mouse.click(box["x"] + 20, box["y"] + 10)
                            page.wait_for_timeout(400)
                            return True, text
            # 回退：点「科技 (」前缀
            for scope in scopes:
                loc = scope.get_by_text(f"{industry} (", exact=False).first
                if _visible(loc):
                    loc.click(timeout=3000, force=True)
                    page.wait_for_timeout(400)
                    return True, industry
            page.wait_for_timeout(350)
        return False, "industry_suggestion_missing"
    except Exception as exc:
        return False, f"industry_suggestion_error:{exc}"


def _dismiss_name_validation_alert(page: Page, frame: Frame | None = None) -> str | None:
    """关闭「请选择正确的行业表述/企业名称」等校验弹窗，返回命中文案。"""
    markers = ("请选择正确的行业表述", "请选择正确的企业名称", "请选择正确的")
    scopes: list[Any] = [page]
    if frame is not None:
        scopes.insert(0, frame)
    for scope in scopes:
        try:
            for m in markers:
                dlg = scope.locator(".el-message-box:visible, .el-dialog:visible").filter(has_text=m).first
                if not _visible(dlg):
                    continue
                btn = dlg.get_by_role("button", name="确定").first
                if not _visible(btn):
                    btn = dlg.get_by_text("确定", exact=True).first
                if _visible(btn):
                    btn.click(force=True, timeout=3000)
                    page.wait_for_timeout(400)
                    return m
                if _click_text(scope, ["确定"], exact=True):
                    page.wait_for_timeout(400)
                    return m
        except Exception:
            continue
    return None


def _wait_name_transition(page: Page, frame: Frame, timeout_sec: float = 20.0) -> tuple[bool, str]:
    """Wait for the single Next click to leave the name-declaration stage."""
    before_url = page.url
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        legal = _visible_human_confirmation(page, frame)
        if legal:
            return False, "human_confirmation_required: " + legal
        _dismiss_known_info_dialog(page, frame)
        urls = [(fr.url or "") for fr in _all_frames(page)]
        if any("web-reg-apply" in url or "enterprise-info" in url for url in urls):
            return True, "establishment_frame"
        current_url = page.url or ""
        if current_url != before_url and "name-declaration" not in current_url:
            return True, "url_changed"
        try:
            body = page.locator("body").inner_text(timeout=500)
        except Exception:
            body = ""
        if "企业设立登记信息" in body or "设立登记信息" in body:
            return True, "next_stage_text"
        page.wait_for_timeout(250)
    return False, "page_did_not_transition"


def _run_name_declaration(case_id: str, page: Page, name_sel: dict[str, Any], payload: dict[str, Any]) -> str:
    """返回 approved name；失败则抛错或写状态后返回空串。"""
    from apps.worker.playbooks.portal import (
        dismiss_portal_overlays,
        looks_logged_in,
        open_name_declaration_from_portal,
    )

    name = payload.get("name") or {}
    # 未登录时不要深链名称申报页；从门户「我已知晓 → 设立登记」进入
    url_now = page.url or ""
    on_name = any(k in url_now for k in ("name-declaration", "web-name-apply"))
    if not on_name:
        if "portal_yct" in url_now or looks_logged_in(page) or "我已知晓" in url_now:
            page = open_name_declaration_from_portal(page, case_id) or page
        else:
            page.goto(config.YCT_PORTAL_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(1500)
            page = open_name_declaration_from_portal(page, case_id) or page

    dismiss_portal_overlays(page)
    _dismiss(page, selectors=name_sel.get("dismiss_selectors"))
    page.wait_for_timeout(800)
    _dismiss(page, selectors=name_sel.get("dismiss_selectors"))

    frame_part = name_sel.get("frame_url_part") or "web-name-apply"
    frame = _frame_by_url(page, frame_part, timeout_ms=45000)
    if frame is None:
        _shot(page, case_id, "name-frame-missing")
        store.set_status(case_id, statuses.NEED_HUMAN, reason="未找到 web-name-apply iframe")
        return ""

    _shot(page, case_id, "name-page")
    ph = name_sel.get("placeholders") or {}
    defaults = name_sel.get("defaults") or {}

    division = name.get("adminDivision") or defaults.get("division") or "上海"
    trade = (name.get("tradeName") or "").strip()
    industry = (name.get("industry") or "").strip()
    org = name.get("orgForm") or defaults.get("org_form") or "有限公司"

    # 区划（页面需要时补选；用户主路径以字号/行业/组织形式为准）
    filled_div = False
    division_opened = _open_select_by_placeholder(frame, ph.get("division") or "请选择名称区划")
    if division_opened:
        page.wait_for_timeout(400)
        filled_div = _pick_dropdown_option(frame, division) or _pick_dropdown_option(frame, "上海")
        if not filled_div:
            _shot(page, case_id, "division-selection-failed")
            store.set_status(case_id, statuses.NEED_HUMAN, reason="名称区划没有唯一匹配的上海市选项")
            return ""

    # 名称字号（如 盛表玛）
    filled_trade = _fill_placeholder(frame, ph.get("trade_name") or "请输入名称字号", trade)
    # 行业表述（如 科技）：逐字输入触发建议，必须点选建议项（不能只手输）
    filled_industry = False
    industry_match = ""
    industry_ok = False
    ind_ph = ph.get("industry") or "请输入行业表述"
    for ind_try in range(3):
        try:
            ind_input = frame.get_by_placeholder(ind_ph, exact=False).first
            if _visible(ind_input):
                ind_input.click(timeout=3000)
                ind_input.fill("")
                page.wait_for_timeout(200)
                ind_input.type(industry, delay=120)
                filled_industry = True
                page.wait_for_timeout(1200)
            else:
                filled_industry = _fill_placeholder(frame, ind_ph, industry)
                page.wait_for_timeout(900)
        except Exception:
            filled_industry = _fill_placeholder(frame, ind_ph, industry)
            page.wait_for_timeout(900)
        if not filled_industry:
            continue
        industry_ok, industry_match = _choose_first_industry_suggestion(frame, industry)
        if not industry_ok:
            try:
                box = frame.get_by_placeholder(ind_ph, exact=False).first
                box.press("ArrowDown")
                page.wait_for_timeout(250)
                box.press("Enter")
                industry_ok, industry_match = True, industry + "(keyboard)"
            except Exception:
                pass
        if industry_ok:
            break
        page.wait_for_timeout(500)
    if not industry_ok:
        _shot(page, case_id, "industry-suggestion-missing")
        store.set_status(case_id, statuses.NEED_HUMAN, reason=industry_match or "行业建议未点选")
        return ""

    # 组织形式：内资企业「有限公司」→ 组织形式「有限公司」
    filled_org = False
    org_readback = ""
    type_opt = name_sel.get("org_type_option") or "有限公司"
    form_opt = name_sel.get("org_form_option") or "有限责任公司"
    if _open_select_by_placeholder(frame, ph.get("org_form") or "请选择组织形式"):
        filled_org = _select_org_form_two_step(frame, type_opt, form_opt)
        if not filled_org:
            filled_org = _pick_dropdown_option(frame, org)
    page.wait_for_timeout(800)
    try:
        org_control = frame.get_by_placeholder(ph.get("org_form") or "请选择组织形式", exact=False).first
        if org_control.count() and _visible(org_control):
            org_readback = (org_control.input_value(timeout=800) or "").strip()
            if not org_readback:
                org_readback = (org_control.locator("xpath=..").inner_text(timeout=800) or "").strip()
    except Exception:
        org_readback = ""
    if not org_readback:
        try:
            org_item = frame.locator(".el-form-item").filter(has_text="组织形式").first
            org_readback = (org_item.inner_text(timeout=800) or "").strip()
        except Exception:
            pass
    if not filled_org or (org and org not in org_readback and "有限" not in org_readback):
        _shot(page, case_id, "organization-selection-failed")
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason=f"未能完成企业类型/组织形式选择（回读={org_readback or '空'}）",
        )
        return ""

    # 生僻字拼音
    try:
        py = frame.locator("label.el-radio").filter(has_text=re.compile(r"sheng\d", re.I)).first
        if _visible(py):
            py.click(timeout=3000)
            page.wait_for_timeout(300)
        else:
            # 隐藏 popper 内也可点
            py2 = frame.locator(".nameFontsizePopper label.el-radio").first
            if py2.count() > 0:
                py2.click(timeout=3000, force=True)
    except Exception:
        pass

    # 选企业名称：等待组合名出现后点选（兼容有限公司/有限责任公司回显）
    preview = (
        name.get("fullNamePreview")
        or f"上海{trade}{industry}有限公司"
    ).strip()
    selected_name = False
    try:
        candidates: list[tuple[Any, str]] = []
        for _ in range(30):
            radios = frame.locator(
                "label.el-radio, .el-radio, [class*='radio'], [class*='name']"
            ).filter(has_text=re.compile("有限公司|有限责任公司"))
            candidates = []
            for i in range(min(radios.count(), 40)):
                radio = radios.nth(i)
                if not _visible(radio):
                    continue
                text = " ".join((radio.inner_text(timeout=500) or "").split())
                if trade in text and industry in text and ("有限公司" in text or "有限责任" in text):
                    candidates.append((radio, text))
            if candidates:
                break
            exact = frame.get_by_text(preview, exact=True)
            if exact.count() == 0:
                exact = frame.get_by_text(preview, exact=False)
            if exact.count() > 0 and _visible(exact.first):
                try:
                    exact.first.click(timeout=4000)
                except Exception:
                    pass
                selected_name = True
                break
            # 已渲染出预览名称则视为可选中
            try:
                body_now = frame.locator("body").inner_text(timeout=500)
            except Exception:
                body_now = ""
            if preview in body_now and "请选择您的企业名称" in body_now:
                selected_name = True
                break
            page.wait_for_timeout(400)
        if candidates:
            chosen = next(((r, t) for r, t in candidates if preview in t or t in preview), candidates[0])
            try:
                chosen[0].click(timeout=4000)
            except Exception:
                pass
            selected_name = True
            if chosen[1] and len(chosen[1]) < 80:
                preview = chosen[1]
        elif not selected_name:
            _shot(page, case_id, "name-candidate-missing")
            store.set_status(case_id, statuses.NEED_HUMAN, reason="未找到匹配的企业名称候选")
            return ""
    except Exception as exc:
        _shot(page, case_id, "name-candidate-error")
        store.set_status(case_id, statuses.NEED_HUMAN, reason=f"企业名称候选读取失败: {exc}")
        return ""

    page.wait_for_timeout(500)
    next_text = name_sel.get("next_button_text") or "下一步"
    if not _click_text(frame, [next_text], exact=True):
        _shot(page, case_id, "next-button-missing")
        store.set_status(case_id, statuses.NEED_HUMAN, reason="未找到下一步按钮")
        return ""
    # 官方偶发「系统繁忙」/「请选择正确的行业表述」：关弹窗后补选再点下一步
    for busy_try in range(5):
        page.wait_for_timeout(800)
        alert = _dismiss_name_validation_alert(page, frame)
        busy = _dismiss_system_busy(page)
        if not alert and not busy:
            break
        _shot(page, case_id, f"name-alert-{busy_try}")
        if alert and "行业" in alert:
            try:
                ind_input = frame.get_by_placeholder(ind_ph, exact=False).first
                if _visible(ind_input):
                    ind_input.click(timeout=3000)
                    ind_input.fill("")
                    ind_input.type(industry, delay=120)
                    page.wait_for_timeout(1000)
                _choose_first_industry_suggestion(frame, industry)
            except Exception:
                pass
            # 行业重选后名称列表可能刷新，再点第一个候选
            try:
                radios = frame.locator("label.el-radio, .el-radio").filter(
                    has_text=re.compile("有限公司|有限责任公司")
                )
                for i in range(min(radios.count(), 10)):
                    r = radios.nth(i)
                    if not _visible(r):
                        continue
                    t = r.inner_text(timeout=400) or ""
                    if trade in t and industry in t:
                        r.click(timeout=3000)
                        break
            except Exception:
                pass
        page.wait_for_timeout(800 + busy_try * 500)
        _click_text(frame, [next_text], exact=True)
    transitioned, transition_reason = _wait_name_transition(page, frame, timeout_sec=25)
    if not transitioned:
        alert = _dismiss_name_validation_alert(page, frame)
        if alert or _dismiss_system_busy(page):
            page.wait_for_timeout(1500)
            if alert and "行业" in (alert or ""):
                try:
                    ind_input = frame.get_by_placeholder(ind_ph, exact=False).first
                    if _visible(ind_input):
                        ind_input.click(timeout=3000)
                        ind_input.fill("")
                        ind_input.type(industry, delay=120)
                        page.wait_for_timeout(1000)
                    _choose_first_industry_suggestion(frame, industry)
                except Exception:
                    pass
            _click_text(frame, [next_text], exact=True)
            transitioned, transition_reason = _wait_name_transition(page, frame, timeout_sec=25)
    _shot(page, case_id, "name-after-next")

    body = ""
    try:
        body = frame.locator("body").inner_text(timeout=3000)
    except Exception:
        try:
            body = page.locator("body").inner_text(timeout=3000)
        except Exception:
            body = ""

    if not filled_trade:
        store.set_status(case_id, statuses.NEED_HUMAN, reason="未能填写名称字号")
        return ""

    rare = name_sel.get("rare_char_fail") or "请修改字号"
    if rare in body or "生僻字" in body:
        _shot(page, case_id, "name-failed")
        store.set_status(case_id, statuses.NAME_FAILED, reason="字号包含生僻字，请修改字号")
        return ""
    if (name_sel.get("fail_text") or "不予") in body and (name_sel.get("success_text") or "比对通过") not in body:
        # 可能仍弹出继续路径；若明确「比对未通过」且无继续，则失败
        if "比对未通过" in body and "继续申报" not in body and "比对通过" not in body:
            _shot(page, case_id, "name-failed")
            store.set_status(case_id, statuses.NAME_FAILED, reason="计算机比对未通过")
            return ""

    if not transitioned:
        status = (
            statuses.HUMAN_CONFIRMATION_REQUIRED
            if transition_reason.startswith("human_confirmation_required")
            else statuses.NEED_HUMAN
        )
        store.set_status(case_id, status, reason=transition_reason)
        return ""

    legal = _visible_human_confirmation(page)
    if legal:
        store.set_status(
            case_id,
            statuses.HUMAN_CONFIRMATION_REQUIRED,
            reason="请本人完成法律确认后继续: " + legal,
        )
        return ""

    _dump_json(
        case_id,
        "fill-name-result.json",
        {
            "division": filled_div,
            "tradeName": filled_trade,
            "industry": filled_industry,
            "industryMatch": industry_match,
            "orgForm": filled_org,
            "orgReadback": org_readback,
            "selectedName": selected_name,
            "approvedName": preview,
            "nextClickedOnce": True,
            "transition": transition_reason,
        },
    )
    return preview


def run_fill_pipeline(case_id: str, page: Page) -> None:
    if config.ALLOW_FINAL_SUBMIT:
        raise RuntimeError("一期禁止 ALLOW_FINAL_SUBMIT=true")

    sel = _load_selectors()
    name_sel = sel.get("name_self_declaration") or {}
    case = store.get_case(case_id)
    payload = case["payload"]

    if not _configured(name_sel):
        page.goto(config.YCT_PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        _shot(page, case_id, "portal-after-login")
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason="selectors.yaml 未配置名称申报入口",
        )
        return

    store.set_status(case_id, statuses.NAME_CHECKING)
    try:
        approved = _run_name_declaration(case_id, page, name_sel, payload)
    except Exception as e:
        _shot(page, case_id, "name-error")
        store.set_status(case_id, statuses.NEED_HUMAN, reason=f"名称申报异常: {e}")
        return

    name = payload.get("name") or {}
    preview = (
        approved
        or name.get("fullNamePreview")
        or f"上海{name.get('tradeName') or ''}{name.get('industry') or ''}有限公司"
    )

    # 核名已点下一步但停在「比对结果」等弹窗时，仍进入基本信息板块
    if not approved:
        reason = (store.get_case(case_id).get("reason") or "")
        if any(k in reason for k in ("比对结果", "已知晓该名称", "计算机比对", "承诺书", "告知书")):
            approved = preview
        else:
            return

    # 新增：核名后弹窗 + 基本信息 / 住所 / 经营范围 / 下一步（不改动核名函数）
    from apps.worker.playbooks.basic_info import run_basic_info_flow

    store.set_status(
        case_id,
        statuses.FILLING_ESTABLISHMENT,
        result={
            "approvedName": approved,
            "tradeName": name.get("tradeName", ""),
            "industry": name.get("industry", ""),
            "enterpriseType": "有限公司",
            "organizationForm": name.get("orgForm", ""),
            "currentUrl": page.url,
        },
    )
    try:
        filled = run_basic_info_flow(case_id, page, payload)
        _dump_json(case_id, "fill-basic-info-result.json", filled)
    except Exception as e:
        _shot(page, case_id, "basic-info-error")
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason=f"基本信息填写异常: {e}",
            result={"approvedName": approved},
        )
        return

    base_result = {
        "approvedName": approved,
        "tradeName": name.get("tradeName", ""),
        "industry": name.get("industry", ""),
        "enterpriseType": "有限公司",
        "organizationForm": name.get("orgForm", ""),
        "currentUrl": page.url,
        "dialogs": (filled or {}).get("dialogs"),
        "fill": filled,
    }
    if not filled.get("ok"):
        missing = filled.get("missing") or []
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason="基本信息未完成：" + ("、".join(missing) if missing else (filled.get("reason") or "请查看截图")),
            result=base_result,
        )
        return

    # 新增：出资情况（不改动核名/基本信息函数）
    from apps.worker.playbooks.capital import run_capital_flow

    store.set_status(
        case_id,
        statuses.FILLING_ESTABLISHMENT,
        result=base_result,
        customer_message="正在填写出资情况…",
    )
    try:
        capital = run_capital_flow(case_id, page, payload)
        _dump_json(case_id, "fill-capital-result.json", capital)
    except Exception as e:
        _shot(page, case_id, "capital-error")
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason=f"出资情况填写异常: {e}",
            result={**base_result, "currentUrl": page.url},
        )
        return

    base_result = {
        **base_result,
        "currentUrl": page.url,
        "capital": capital,
    }
    if capital.get("ok"):
        # 新增：人员信息 + 受益所有人（不改动核名/基本信息/出资情况）
        from apps.worker.playbooks.personnel import run_personnel_flow

        store.set_status(
            case_id,
            statuses.FILLING_ESTABLISHMENT,
            result=base_result,
            customer_message="正在填写人员信息…",
        )
        try:
            personnel = None
            last_err = None
            for attempt in range(1, 4):
                store.set_status(
                    case_id,
                    statuses.FILLING_ESTABLISHMENT,
                    result=base_result,
                    customer_message=f"正在填写人员信息…（第 {attempt}/3 次）",
                )
                try:
                    personnel = run_personnel_flow(case_id, page, payload)
                    _dump_json(case_id, "fill-personnel-result.json", personnel)
                    last_err = None
                    if personnel.get("ok"):
                        break
                    missing_try = personnel.get("missing") or []
                    store.set_status(
                        case_id,
                        statuses.FILLING_ESTABLISHMENT,
                        result={**base_result, "personnel": personnel},
                        customer_message="人员信息未完成（"
                        + ("、".join(missing_try) if missing_try else (personnel.get("error") or "未知"))
                        + f"），自动重试 {attempt}/3",
                    )
                    page.wait_for_timeout(1500)
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    # 备案页关掉时，page 句柄会失效；切到仍存活的登记页再试，勿整轮判死
                    if "closed" in msg.lower() or "Target page" in msg or "browser has been" in msg:
                        recovered = None
                        try:
                            for p in list(page.context.pages):
                                if not p.is_closed():
                                    recovered = p
                                    if "web-yct-master" in (p.url or "") or "chief-executive" in (
                                        p.url or ""
                                    ):
                                        break
                        except Exception:
                            recovered = None
                        if recovered is not None:
                            page = recovered
                            try:
                                page.bring_to_front()
                            except Exception:
                                pass
                            _shot(page, case_id, f"personnel-recovered-{attempt}")
                            if attempt < 3:
                                page.wait_for_timeout(1000)
                                continue
                        store.set_status(
                            case_id,
                            statuses.NEED_HUMAN,
                            reason=f"人员信息填写异常: {e}",
                            result={**base_result, "currentUrl": getattr(page, "url", "")},
                        )
                        return
                    _shot(page, case_id, f"personnel-error-{attempt}")
                    if attempt == 3:
                        store.set_status(
                            case_id,
                            statuses.NEED_HUMAN,
                            reason=f"人员信息填写异常: {e}",
                            result={**base_result, "currentUrl": page.url},
                        )
                        return
                    page.wait_for_timeout(1500)
            if last_err is not None and personnel is None:
                store.set_status(
                    case_id,
                    statuses.NEED_HUMAN,
                    reason=f"人员信息填写异常: {last_err}",
                    result={**base_result, "currentUrl": page.url},
                )
                return
        except Exception as e:
            _shot(page, case_id, "personnel-error")
            store.set_status(
                case_id,
                statuses.NEED_HUMAN,
                reason=f"人员信息填写异常: {e}",
                result={**base_result, "currentUrl": page.url},
            )
            return

        base_result = {
            **base_result,
            "currentUrl": page.url,
            "personnel": personnel,
        }
        if personnel and personnel.get("ok"):
            # 新增：企业开办 / 后续办理方式（不改动人员信息及之前步骤）
            from apps.worker.playbooks.opening import run_opening_flow

            store.set_status(
                case_id,
                statuses.CHOOSING_PROCESS,
                result=base_result,
                customer_message="正在填写企业开办 / 后续办理方式…",
            )
            try:
                opening = run_opening_flow(case_id, page, payload)
                _dump_json(case_id, "fill-opening-result.json", opening)
            except Exception as e:
                _shot(page, case_id, "opening-error")
                store.set_status(
                    case_id,
                    statuses.NEED_HUMAN,
                    reason=f"企业开办填写异常: {e}",
                    result=base_result,
                )
                return

            base_result = {
                **base_result,
                "currentUrl": page.url,
                "opening": opening,
            }
            if opening.get("ok"):
                store.set_status(
                    case_id,
                    statuses.READY_FOR_USER_SUBMIT,
                    result=base_result,
                    customer_message="企业开办信息已暂存，请本人继续核对后续环节（不代提交/签名）",
                )
                return

            missing_o = opening.get("missing") or []
            store.set_status(
                case_id,
                statuses.NEED_HUMAN,
                reason="企业开办未完成："
                + ("、".join(missing_o) if missing_o else (opening.get("error") or "请查看截图")),
                result=base_result,
            )
            return

        missing_p = personnel.get("missing") or []
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason="人员信息未完成："
            + ("、".join(missing_p) if missing_p else (personnel.get("error") or "请查看截图")),
            result=base_result,
        )
        return

    missing = capital.get("missing") or []
    store.set_status(
        case_id,
        statuses.NEED_HUMAN,
        reason="出资情况未完成：" + ("、".join(missing) if missing else (capital.get("error") or "请查看截图")),
        result=base_result,
    )
