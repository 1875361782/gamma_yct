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
    """关闭「系统繁忙，请稍后重试」类弹窗（统一走 retry_gate）。"""
    from apps.worker.playbooks.retry_gate import dismiss_system_busy

    return dismiss_system_busy(page)


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


def _select_first_pinyin_option(page: Page, frame: Frame | None = None) -> bool:
    """字号含多音字时的「名称拼音选择」弹层：默认点选第一项。

    官网常见文案：名称拼音选择(必须选择一项拼音)，选项如 ga1 ma3 chuang4 zao4。
    """
    scopes: list[Any] = []
    if frame is not None:
        scopes.append(frame)
    scopes.append(page)
    try:
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
    except Exception:
        pass

    pinyin_label_re = re.compile(
        r"(名称拼音选择|必须选择一项拼音|拼音选择|请选择.*拼音)",
        re.I,
    )
    # 声调拼音：ga1 / jia1 ma3 ...
    tone_re = re.compile(r"[a-zA-Z]+\d")

    clicked = False
    for scope in scopes:
        try:
            # 先看是否出现拼音选择层
            has_dlg = False
            try:
                txt = scope.locator("body").inner_text(timeout=400) or ""
                if pinyin_label_re.search(txt):
                    has_dlg = True
            except Exception:
                pass
            if not has_dlg:
                try:
                    if scope.get_by_text(pinyin_label_re).count() > 0:
                        has_dlg = True
                except Exception:
                    pass
            if not has_dlg:
                # 仍尝试点 nameFontsizePopper / 含声调的 radio（兼容旧生僻字层）
                pass

            # 1) 优先：弹层内第一个 radio
            for box_sel in (
                ".nameFontsizePopper",
                ".el-popover",
                ".el-popper",
                ".el-dialog",
                ".el-message-box",
                "[class*='pinyin']",
                "[class*='Pinyin']",
                "[class*='Fontsize']",
            ):
                try:
                    box = scope.locator(box_sel).filter(has_text=pinyin_label_re).first
                    if box.count() == 0:
                        box = scope.locator(box_sel).first
                    if box.count() == 0:
                        continue
                    radios = box.locator("label.el-radio, .el-radio")
                    n = min(radios.count(), 8)
                    if n <= 0:
                        continue
                    # 有声调文案的优先；否则点第一项
                    pick = None
                    for i in range(n):
                        r = radios.nth(i)
                        try:
                            t = (r.inner_text(timeout=300) or "").strip()
                        except Exception:
                            t = ""
                        if tone_re.search(t):
                            pick = r
                            break
                    if pick is None:
                        pick = radios.first
                    try:
                        pick.click(timeout=2500)
                    except Exception:
                        pick.click(timeout=2000, force=True)
                    clicked = True
                    break
                except Exception:
                    continue
            if clicked:
                break

            # 2) 全局：第一个带声调数字的 radio
            try:
                radios = scope.locator("label.el-radio, .el-radio")
                n = min(radios.count(), 20)
                for i in range(n):
                    r = radios.nth(i)
                    try:
                        if not r.is_visible(timeout=200):
                            continue
                        t = (r.inner_text(timeout=300) or "").strip()
                    except Exception:
                        continue
                    if tone_re.search(t) or (has_dlg and i == 0):
                        try:
                            r.click(timeout=2500)
                        except Exception:
                            r.click(timeout=2000, force=True)
                        clicked = True
                        break
            except Exception:
                pass
            if clicked:
                break
        except Exception:
            continue

    if clicked:
        try:
            page.wait_for_timeout(350)
        except Exception:
            pass
        # 若有「确定」一并点掉（部分版本需确认）
        for scope in scopes:
            try:
                ok = scope.locator("button, .el-button").filter(
                    has_text=re.compile(r"^\s*确\s*定\s*$")
                ).first
                if ok.count() > 0 and ok.is_visible(timeout=300):
                    # 只在拼音层附近点，避免误关其它框
                    parent_txt = ""
                    try:
                        parent_txt = ok.evaluate(
                            "el => (el.closest('.el-dialog,.el-popover,.el-popper,.el-message-box')||el.parentElement||el).innerText||''"
                        )
                    except Exception:
                        parent_txt = ""
                    if "拼音" in str(parent_txt) or pinyin_label_re.search(str(parent_txt)):
                        ok.click(timeout=2000)
                        break
            except Exception:
                continue
    return clicked


def _click_name_next(frame: Frame, page: Page, next_text: str = "下一步") -> bool:
    """点核名页「下一步」（iframe 优先，主文档兜底；force / JS 兜底）。"""
    # 多音字弹层未关时先默认选第一项，否则下一步无效
    for _ in range(3):
        if not _select_first_pinyin_option(page, frame):
            break
        page.wait_for_timeout(250)
    scopes: list[Any] = [frame, page]
    try:
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
    except Exception:
        pass
    btn_re = re.compile(rf"^\s*{re.escape(next_text)}\s*$")
    for scope in scopes:
        try:
            btn = scope.locator("button, .el-button, a").filter(has_text=btn_re).first
            if btn.count() == 0:
                btn = scope.get_by_role("button", name=btn_re).first
            if btn.count() == 0:
                continue
            try:
                btn.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                btn.click(timeout=4000)
                return True
            except Exception:
                try:
                    btn.click(timeout=3000, force=True)
                    return True
                except Exception:
                    pass
            try:
                btn.evaluate("el => el.click()")
                return True
            except Exception:
                pass
        except Exception:
            continue
    if _click_text(frame, [next_text], exact=True):
        return True
    if _click_text(page, [next_text], exact=True):
        return True
    return False


def _name_next_retry_on_busy(
    page: Page,
    frame: Frame,
    *,
    case_id: str,
    next_text: str = "下一步",
    max_rounds: int = 6,
) -> None:
    """核名点下一步后：出现「系统繁忙，请稍后重试」→ 点确定关掉 → 再点下一步。"""
    for round_i in range(max_rounds):
        # 繁忙弹窗可能略晚于点击出现，短轮询
        busy = False
        for _ in range(10):
            if _page_or_frames_has(
                page, "比对结果", "已知晓该名称存在", "计算机比对通过", "返回修改", "备用字号"
            ):
                return
            if _dismiss_system_busy(page):
                busy = True
                break
            page.wait_for_timeout(300)
        if not busy:
            # 首轮多等一下，避免繁忙弹窗稍晚弹出就放弃
            if round_i == 0:
                page.wait_for_timeout(700)
                if _dismiss_system_busy(page):
                    busy = True
                else:
                    return
            else:
                return
        _shot(page, case_id, f"name-busy-{round_i}")
        page.wait_for_timeout(900 + round_i * 400)
        _click_name_next(frame, page, next_text)


def _name_fail_exhausted(reason: str) -> bool:
    """内层已穷尽全部字号（或仅有一个）时才终止，勿把通用「请返回修改字号」当穷尽。"""
    r = reason or ""
    return ("均不可用" in r) or ("仅有一个字号" in r)


def _advance_trade_candidate(
    name_block: dict[str, Any],
    trade_candidates: list[str],
    trade_idx: int,
) -> tuple[int, str, str]:
    """切到下一优先字号，返回 (新 idx, 失败字号, 下一字号)。"""
    failed = str(name_block.get("tradeName") or "").strip() or trade_candidates[trade_idx]
    trade_idx += 1
    next_trade = trade_candidates[trade_idx]
    name_block["tradeName"] = next_trade
    name_block["fullNamePreview"] = (
        f"上海{next_trade}"
        f"{name_block.get('industry') or ''}"
        f"{name_block.get('orgForm') or '有限公司'}"
    )
    name_block["alternatives"] = [
        {
            "tradeName": t,
            "industry": name_block.get("industry") or "",
            "orgForm": name_block.get("orgForm") or "有限公司",
        }
        for t in trade_candidates[trade_idx + 1 :]
    ]
    return trade_idx, failed, next_trade


def _scopes_for_dialogs(page: Page) -> list[Any]:
    scopes: list[Any] = [page]
    try:
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
    except Exception:
        pass
    return scopes


def _any_visible_text(page: Page, *texts: str) -> bool:
    """任一可见文案（含弹层按钮），比纯 DOM 文本更可靠。"""
    for scope in _scopes_for_dialogs(page):
        for t in texts:
            if not t:
                continue
            try:
                loc = scope.get_by_text(t, exact=False)
                n = min(loc.count(), 12)
            except Exception:
                continue
            for i in range(n):
                try:
                    el = loc.nth(i)
                    if el.is_visible(timeout=400):
                        return True
                except Exception:
                    continue
            try:
                btn = scope.locator("button, .el-button, a").filter(
                    has_text=re.compile(rf"^\s*{re.escape(t)}\s*$")
                )
                if btn.count() and btn.first.is_visible(timeout=400):
                    return True
            except Exception:
                continue
    return False


def _visible_fail_or_pass_dialog(page: Page) -> str:
    """fail=备用字号/比对未通过；pass=比对通过后续；none=尚未出结果。"""
    # 通过优先：避免 DOM 残留「备用字号」把「计算机比对通过」判成 fail
    if _any_visible_text(page, "计算机比对通过"):
        return "pass"
    if _any_visible_text(page, "比对结果") and _any_visible_text(page, "已知晓该名称存在"):
        if not _any_visible_text(page, "继续申请", "备用字号1", "可进行备用字号"):
            return "pass"
    if _beiyong_dialog_open(page):
        return "fail"
    # 备用字号弹窗特征：可见「备用字号」或「返回修改」+「继续申请」
    if _any_visible_text(page, "备用字号1", "备用字号2", "可进行备用字号", "备用拟设名称"):
        return "fail"
    if _any_visible_text(page, "返回修改") and _any_visible_text(page, "继续申请"):
        return "fail"
    if _any_visible_text(page, "计算机比对未通过", "比对未通过") and not _any_visible_text(
        page, "计算机比对通过"
    ):
        return "fail"
    if _any_visible_text(
        page,
        "告知书",
        "承诺书",
        "温馨提示",
        "经办人信息",
        "请上传身份证人像面",
    ):
        return "pass"
    # 可见 dialog 节点兜底
    for scope in _scopes_for_dialogs(page):
        try:
            dialogs = scope.locator(".el-dialog, .el-message-box, [role='dialog']")
            n = min(dialogs.count(), 8)
        except Exception:
            continue
        for i in range(n):
            try:
                dlg = dialogs.nth(i)
                if not dlg.is_visible(timeout=300):
                    continue
                text = " ".join((dlg.inner_text(timeout=500) or "").split())
            except Exception:
                continue
            if not text:
                continue
            if "计算机比对通过" in text or (
                "已知晓该名称" in text and "下一步" in text and "继续申请" not in text
            ):
                return "pass"
            if any(k in text for k in ("备用字号", "备用拟设名称", "可进行备用字号", "继续申请")):
                return "fail"
            if "比对未通过" in text and "比对通过" not in text:
                return "fail"
            if any(k in text for k in ("告知书", "温馨提示")):
                return "pass"
    return "none"


def _name_needs_return_modify(page: Page) -> bool:
    """出现备用字号/比对未通过，需点返回修改换字号。"""
    return _visible_fail_or_pass_dialog(page) == "fail"


def _wait_beiyong_or_pass(
    page: Page,
    frame: Frame,
    *,
    next_text: str = "下一步",
    timeout_sec: float = 45.0,
    case_id: str = "",
    shot_prefix: str = "",
) -> str:
    """点下一步之后：一直等到「备用字号」失败弹窗或通过态，绝不提前换字号。"""
    deadline = time.time() + timeout_sec
    saw_busy = 0
    while time.time() < deadline:
        kind = _visible_fail_or_pass_dialog(page)
        if kind != "none":
            if case_id and shot_prefix:
                _shot(page, case_id, f"{shot_prefix}-{kind}")
            return kind
        if _dismiss_system_busy(page):
            saw_busy += 1
            page.wait_for_timeout(900)
            _click_name_next(frame, page, next_text)
            page.wait_for_timeout(500)
            continue
        legal = _visible_human_confirmation(page, frame)
        if legal:
            return "pass"
        page.wait_for_timeout(400)
    kind = _visible_fail_or_pass_dialog(page)
    if case_id and shot_prefix:
        _shot(page, case_id, f"{shot_prefix}-timeout-{kind or 'none'}")
    return kind


def _beiyong_dialog_open(page: Page) -> bool:
    """备用字号弹窗是否仍打开（勿用「请输入名称字号」判断，弹窗内也有同占位符）。"""
    if _any_visible_text(page, "备用字号1", "备用字号2", "可进行备用字号", "备用拟设名称"):
        return True
    if _any_visible_text(page, "继续申请") and _any_visible_text(page, "返回修改"):
        return True
    if _any_visible_text(page, "备用字号") and _any_visible_text(page, "非必填"):
        return True
    return False


def _find_beiyong_dialog(scope: Any):
    """定位备用字号可见弹窗。"""
    for has in (
        ("备用字号", "返回修改"),
        ("继续申请", "返回修改"),
        ("备用拟设名称", "返回修改"),
        ("可进行备用字号", "返回修改"),
    ):
        try:
            dlg = scope.locator(".el-dialog, .el-message-box, [role='dialog']").filter(
                has_text=has[0]
            ).filter(has_text=has[1]).first
            if dlg.count() and dlg.is_visible(timeout=500):
                return dlg
        except Exception:
            continue
    return None


def _click_dialog_close_x(page: Page, frame: Frame | None = None) -> bool:
    """点弹窗右上角 X。"""
    scopes: list[Any] = []
    if frame is not None:
        scopes.append(frame)
    scopes.extend(_scopes_for_dialogs(page))
    close_sels = (
        ".el-dialog__headerbtn",
        "button.el-dialog__headerbtn",
        ".el-dialog__close",
        ".el-message-box__headerbtn",
        "[aria-label='Close']",
        ".el-dialog .el-icon-close",
    )
    for scope in scopes:
        dlg = _find_beiyong_dialog(scope)
        if dlg is not None:
            for sel in close_sels:
                try:
                    btn = dlg.locator(sel).first
                    if btn.count() == 0:
                        continue
                    try:
                        btn.click(timeout=2500)
                    except Exception:
                        btn.click(timeout=2000, force=True)
                    page.wait_for_timeout(500)
                    if not _beiyong_dialog_open(page):
                        return True
                except Exception:
                    continue
            # JS 点关闭
            try:
                dlg.evaluate(
                    """(root) => {
                      const b = root.querySelector('.el-dialog__headerbtn, .el-dialog__close, .el-message-box__headerbtn');
                      if (b) b.click();
                    }"""
                )
                page.wait_for_timeout(500)
                if not _beiyong_dialog_open(page):
                    return True
            except Exception:
                pass
        for sel in close_sels:
            try:
                btn = scope.locator(sel).first
                if btn.count() == 0 or not btn.is_visible(timeout=300):
                    continue
                btn.click(timeout=2000, force=True)
                page.wait_for_timeout(400)
                if not _beiyong_dialog_open(page):
                    return True
            except Exception:
                continue
    # 不用 Escape：易误伤告知书/比对通过弹窗并弄丢灰色遮罩
    return not _beiyong_dialog_open(page)


def _click_return_modify(page: Page, frame: Frame | None = None) -> bool:
    """备用字号弹窗：优先点「返回修改」；失败则点右上角 X 关闭。

    比对通过 / 告知书弹窗期间禁止调用（避免点错或 Esc 弄白背景）。
    """
    if _any_visible_text(
        page,
        "我已详细阅读并知晓",
        "企业名称登记告知书",
        "计算机比对通过",
        "已知晓该名称存在",
    ) and not _beiyong_dialog_open(page):
        return False
    scopes: list[Any] = []
    if frame is not None:
        scopes.append(frame)
    scopes.extend(_scopes_for_dialogs(page))

    def _try_click_return(scope) -> bool:
        # 1) 弹窗内按钮
        dlg = _find_beiyong_dialog(scope)
        targets = []
        if dlg is not None:
            targets.append(dlg)
        targets.append(scope)
        for root in targets:
            try:
                btn = root.locator("button, .el-button, a").filter(
                    has_text=re.compile(r"^\s*返回修改\s*$")
                ).first
                if btn.count() == 0:
                    btn = root.get_by_role("button", name=re.compile(r"返回修改")).first
                if btn.count() == 0:
                    btn = root.get_by_text("返回修改", exact=True).first
                if btn.count() == 0:
                    continue
                try:
                    btn.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass
                try:
                    btn.click(timeout=4000)
                except Exception:
                    try:
                        btn.click(timeout=3000, force=True)
                    except Exception:
                        try:
                            btn.evaluate("el => el.click()")
                        except Exception:
                            continue
                page.wait_for_timeout(700)
                return True
            except Exception:
                continue
        return False

    for round_i in range(4):
        if not _beiyong_dialog_open(page) and not _any_visible_text(page, "返回修改"):
            # 比对结果等其它带返回修改的弹窗
            pass
        hit = False
        for scope in scopes:
            if _try_click_return(scope):
                hit = True
                break
        if hit:
            page.wait_for_timeout(500)
            if not _beiyong_dialog_open(page):
                return True
            # 点了但弹窗还在：再用 X
            if _click_dialog_close_x(page, frame):
                return True
        else:
            # 返回修改点不到 → 直接 X
            if _click_dialog_close_x(page, frame):
                return True
        page.wait_for_timeout(400)
    return not _beiyong_dialog_open(page)


def _wait_back_to_name_form(
    page: Page,
    frame: Frame,
    *,
    trade_ph: str = "请输入名称字号",
    timeout_sec: float = 25.0,
) -> bool:
    """点「返回修改」/关窗后：备用字号弹窗必须消失，且主表「请选择您的企业名称」可见。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _beiyong_dialog_open(page):
            _click_return_modify(page, frame)
            page.wait_for_timeout(500)
            continue
        _dismiss_residual_masks(page, frame)
        # 主表特征（弹窗里没有「请选择您的企业名称」）
        if _any_visible_text(page, "请选择您的企业名称", "名称区划"):
            page.wait_for_timeout(400)
            if not _beiyong_dialog_open(page) and _main_trade_input(frame, trade_ph) is not None:
                return True
        page.wait_for_timeout(350)
    _dismiss_residual_masks(page, frame)
    return (not _beiyong_dialog_open(page)) and _any_visible_text(
        page, "请选择您的企业名称", "名称区划"
    )


def _name_candidate_scopes(frame: Frame, page: Page) -> list[Any]:
    scopes: list[Any] = [frame, page]
    try:
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
    except Exception:
        pass
    return scopes


def _select_name_radio_for_trade(
    frame: Frame,
    page: Page,
    *,
    trade: str,
    industry: str,
    name_suffixes: tuple[str, ...],
    preview: str,
) -> tuple[bool, str]:
    """点选含字号+行业+组织形式后缀的企业名称候选（多 frame，已勾选也算成功）。"""
    suffix_pat = "|".join(re.escape(s) for s in name_suffixes)
    selected = False
    chosen_preview = preview
    try:
        candidates: list[tuple[Any, str]] = []
        for _ in range(45):
            _confirm_org_type_tip(page, frame)
            candidates = []
            for scope in _name_candidate_scopes(frame, page):
                try:
                    radios = scope.locator(
                        "label.el-radio, .el-radio, [class*='radio']"
                    ).filter(has_text=re.compile(suffix_pat))
                    for i in range(min(radios.count(), 40)):
                        radio = radios.nth(i)
                        try:
                            text = " ".join((radio.inner_text(timeout=500) or "").split())
                        except Exception:
                            continue
                        if not text or trade not in text:
                            continue
                        if industry and industry not in text:
                            continue
                        if not any(s in text for s in name_suffixes):
                            continue
                        candidates.append((radio, text))
                        # 已勾选：直接视为已选
                        try:
                            cls = radio.get_attribute("class") or ""
                            if "is-checked" in cls or radio.locator(
                                "input:checked, .el-radio__input.is-checked"
                            ).count():
                                selected = True
                                if len(text) < 80:
                                    chosen_preview = text
                                return True, chosen_preview
                        except Exception:
                            pass
                except Exception:
                    continue
                try:
                    exact = scope.get_by_text(preview, exact=True)
                    if exact.count() == 0:
                        exact = scope.get_by_text(preview, exact=False)
                    if exact.count() > 0:
                        try:
                            exact.first.click(timeout=3000, force=True)
                        except Exception:
                            try:
                                exact.first.click(timeout=3000)
                            except Exception:
                                pass
                        selected = True
                        return True, chosen_preview
                except Exception:
                    pass
            if candidates:
                break
            page.wait_for_timeout(400)
        if candidates:
            chosen = next(
                ((r, t) for r, t in candidates if preview in t or t in preview),
                candidates[0],
            )
            try:
                chosen[0].click(timeout=4000)
            except Exception:
                try:
                    chosen[0].click(timeout=3000, force=True)
                except Exception:
                    pass
            selected = True
            if chosen[1] and len(chosen[1]) < 80:
                chosen_preview = chosen[1]
        # 页面已展示完整名但定位器漏检：正文含 preview 也放行
        if not selected and preview and _page_or_frames_has(page, preview):
            selected = True
    except Exception:
        return False, chosen_preview
    return selected, chosen_preview


def _refill_trade_on_name_page(
    frame: Frame,
    page: Page,
    *,
    case_id: str,
    trade: str,
    industry: str,
    org: str,
    name_sel: dict[str, Any],
) -> tuple[bool, str]:
    """在名称填写页改字号并点下一步（只负责提交，结果由调用方等待弹窗）。"""
    ph = name_sel.get("placeholders") or {}
    name_suffixes = _org_name_suffixes(org)
    preview = f"上海{trade}{industry}{name_suffixes[0]}"
    trade_ph = ph.get("trade_name") or "请输入名称字号"
    filled = _fill_trade_name_complete(frame, page, trade=trade, trade_ph=trade_ph)
    if not filled:
        _shot(page, case_id, "name-alt-trade-fill-fail")
        return False, preview
    page.wait_for_timeout(500)
    try:
        ind_ph = ph.get("industry") or "请输入行业表述"
        ind_input = frame.get_by_placeholder(ind_ph, exact=False).first
        if _visible(ind_input):
            cur = (ind_input.input_value(timeout=400) or "").strip()
            if industry and industry not in cur:
                ind_input.click(timeout=2000)
                ind_input.fill("")
                ind_input.type(industry, delay=80)
                page.wait_for_timeout(700)
                _choose_first_industry_suggestion(frame, industry)
    except Exception:
        pass
    if not _wait_candidates_contain_trade(
        frame, page, trade=trade, industry=industry, name_suffixes=name_suffixes, timeout_sec=12
    ):
        _fill_trade_name_complete(frame, page, trade=trade, trade_ph=trade_ph)
        page.wait_for_timeout(800)
    selected, preview = _select_name_radio_for_trade(
        frame,
        page,
        trade=trade,
        industry=industry,
        name_suffixes=name_suffixes,
        preview=preview,
    )
    if not selected:
        _shot(page, case_id, "name-alt-candidate-missing")
        return False, preview
    next_text = name_sel.get("next_button_text") or "下一步"
    if not _click_name_next(frame, page, next_text):
        return False, preview
    _name_next_retry_on_busy(page, frame, case_id=case_id, next_text=next_text)
    return True, preview


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


def _set_input_value_vue(locator, value: str) -> bool:
    """用原生 value setter 写入，兼容 ElementUI/Vue 受控输入。"""
    try:
        locator.evaluate(
            """(el, val) => {
              const proto = window.HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              el.focus();
              if (desc && desc.set) desc.set.call(el, val);
              else el.value = val;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            }""",
            value,
        )
        return True
    except Exception:
        return False


def _read_trade_input(frame: Frame, trade_ph: str) -> str:
    try:
        box = _main_trade_input(frame, trade_ph)
        if box is None:
            return ""
        return (box.input_value(timeout=800) or "").strip()
    except Exception:
        return ""


def _main_trade_input(frame: Frame, trade_ph: str = "请输入名称字号"):
    """主表「名称字号」输入框（排除备用字号弹窗内同名占位符）。"""
    try:
        items = frame.locator(".el-form-item").filter(has_text=re.compile(r"名称字号"))
        for i in range(min(items.count(), 6)):
            item = items.nth(i)
            try:
                in_dlg = item.evaluate(
                    """el => !!el.closest('.el-dialog, .el-message-box, [role="dialog"], .el-dialog__wrapper')"""
                )
            except Exception:
                in_dlg = False
            if in_dlg:
                continue
            inp = item.locator(
                "input.el-input__inner, input[type='text'], input:not([type='hidden'])"
            ).first
            if inp.count() and _visible(inp):
                return inp
    except Exception:
        pass
    try:
        boxes = frame.get_by_placeholder(trade_ph, exact=False)
        for i in range(min(boxes.count(), 8)):
            box = boxes.nth(i)
            try:
                in_dlg = box.evaluate(
                    """el => !!el.closest('.el-dialog, .el-message-box, [role="dialog"], .el-dialog__wrapper')"""
                )
            except Exception:
                in_dlg = False
            if not in_dlg and _visible(box):
                return box
    except Exception:
        pass
    return None


def _dismiss_residual_masks(page: Page, frame: Frame | None = None) -> None:
    """关掉备用字号后，清掉残留遮罩，避免点不到主表字号框。

    严禁在比对通过/告知书/承诺书弹窗期间调用副作用（尤其 Escape），
    否则会弄丢灰色遮罩，只剩白底弹窗且点不到确定。
    """
    # 通过/告知书弹窗在时绝对不要动遮罩、不要按 Esc
    if _any_visible_text(
        page,
        "我已详细阅读并知晓",
        "企业名称登记告知书",
        "企业名称登记承诺书",
        "计算机比对通过",
        "已知晓该名称存在",
    ):
        return
    scopes: list[Any] = []
    if frame is not None:
        scopes.append(frame)
    scopes.extend(_scopes_for_dialogs(page))
    for scope in scopes:
        try:
            scope.evaluate(
                """() => {
                  document.querySelectorAll('.v-modal, .el-dialog__wrapper').forEach(el => {
                    const st = getComputedStyle(el);
                    const dlg = el.querySelector?.('.el-dialog') || el;
                    const text = (dlg.innerText || '');
                    // 绝不隐藏告知书/比对通过相关层
                    if (/告知书|承诺书|我已详细阅读|计算机比对通过|已知晓该名称/.test(text)) return;
                    const isBeiyong = /备用字号|继续申请|备用拟设/.test(text);
                    const hidden = st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0;
                    if (isBeiyong || hidden) {
                      el.style.display = 'none';
                      el.style.pointerEvents = 'none';
                    }
                  });
                }"""
            )
        except Exception:
            continue
    # 不再按 Escape：易误关通过弹窗/弄丢 v-modal 导致白底


def _fill_trade_name_complete(
    frame: Frame,
    page: Page,
    *,
    trade: str,
    trade_ph: str = "请输入名称字号",
) -> bool:
    """完整写入字号并触发官网重新生成；避免只敲首字就刷出残缺候选。"""
    trade = (trade or "").strip()
    if not trade:
        return False
    # 弹窗未关时不要往备用字号框里填
    if _beiyong_dialog_open(page):
        _click_return_modify(page, frame)
        page.wait_for_timeout(600)
    _dismiss_residual_masks(page, frame)
    page.wait_for_timeout(200)

    for attempt in range(4):
        box = _main_trade_input(frame, trade_ph)
        if box is None:
            if _fill_placeholder(frame, trade_ph, trade):
                cur = _read_trade_input(frame, trade_ph)
                if trade in cur or cur == trade:
                    return True
            page.wait_for_timeout(300)
            continue
        try:
            try:
                box.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            box.click(timeout=3000, force=True)
            page.wait_for_timeout(100)
            try:
                box.press("Control+A")
                page.wait_for_timeout(60)
                box.press("Backspace")
            except Exception:
                pass
            try:
                box.fill("")
            except Exception:
                _set_input_value_vue(box, "")
            page.wait_for_timeout(150)
            # 优先 Vue 原生写入 + fill；回读不对再清空后慢速 type
            _set_input_value_vue(box, trade)
            try:
                box.fill(trade, timeout=4000)
            except Exception:
                pass
            page.wait_for_timeout(200)
            cur = _read_trade_input(frame, trade_ph)
            if trade not in cur and cur != trade:
                _set_input_value_vue(box, "")
                try:
                    box.fill("")
                except Exception:
                    pass
                page.wait_for_timeout(80)
                box.type(trade, delay=80 + attempt * 30)
            try:
                box.press("Tab")
            except Exception:
                try:
                    box.blur()
                except Exception:
                    pass
            page.wait_for_timeout(450 + attempt * 250)
            # 多音字：默认选第一项拼音
            _select_first_pinyin_option(page, frame)
            cur = _read_trade_input(frame, trade_ph)
            if trade in cur or cur == trade:
                _select_first_pinyin_option(page, frame)
                return True
            # 再清一次后只慢速 type
            box = _main_trade_input(frame, trade_ph) or box
            box.click(timeout=2000, force=True)
            _set_input_value_vue(box, "")
            try:
                box.fill("")
            except Exception:
                pass
            page.wait_for_timeout(120)
            box.type(trade, delay=140 + attempt * 40)
            page.wait_for_timeout(500)
            try:
                box.press("Tab")
            except Exception:
                pass
            page.wait_for_timeout(400)
            _select_first_pinyin_option(page, frame)
            cur = _read_trade_input(frame, trade_ph)
            if trade in cur or cur == trade:
                _select_first_pinyin_option(page, frame)
                return True
        except Exception:
            _dismiss_residual_masks(page, frame)
            page.wait_for_timeout(300)
            continue
    ok = trade in _read_trade_input(frame, trade_ph)
    if ok:
        _select_first_pinyin_option(page, frame)
    return ok


def _wait_candidates_contain_trade(
    frame: Frame,
    page: Page,
    *,
    trade: str,
    industry: str,
    name_suffixes: tuple[str, ...],
    timeout_sec: float = 18.0,
) -> bool:
    """等到名称候选里出现完整字号（不是只含首字的残缺项）。"""
    trade = (trade or "").strip()
    if not trade:
        return False
    suffix_pat = "|".join(re.escape(s) for s in name_suffixes) if name_suffixes else ""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        _confirm_org_type_tip(page, frame)
        if _page_or_frames_has(page, f"上海{trade}", trade):
            # 进一步确认候选 radio 文案含完整字号
            for scope in _name_candidate_scopes(frame, page):
                try:
                    loc = scope.locator("label.el-radio, .el-radio")
                    if suffix_pat:
                        loc = loc.filter(has_text=re.compile(suffix_pat))
                    for i in range(min(loc.count(), 20)):
                        t = " ".join((loc.nth(i).inner_text(timeout=400) or "").split())
                        if trade in t and (not industry or industry in t):
                            return True
                except Exception:
                    continue
            # 正文已有完整组合名
            if any(
                _page_or_frames_has(page, f"上海{trade}{industry}{s}")
                for s in (name_suffixes or ("有限公司",))
            ):
                return True
        page.wait_for_timeout(400)
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
    """Return a legal/identity confirmation message without clicking it.

    注意：「已知晓该名称…」是核名比对结果勾选项，不是需人工介入的法律确认。
    """
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
                # 比对结果弹窗含「已知晓该名称」，交给 basic_info 自动勾选
                if "比对结果" in text or "已知晓该名称" in text:
                    continue
                if any(key in text for key in keywords):
                    return text[:500]
        except Exception:
            continue
        try:
            body = (scope.locator("body").inner_text(timeout=500) or "").strip()
            if "比对结果" in body or "已知晓该名称" in body:
                continue
            if "承诺书" in body or "我已详细阅读" in body:
                return body[:500]
        except Exception:
            pass
    return None


def _dismiss_known_info_dialog(page: Page, frame: Frame | None = None) -> bool:
    """Close only explicitly informational dialogs; never close 核名比对/告知/法律确认."""
    scopes: list[Any] = [page]
    if frame is not None:
        scopes.insert(0, frame)
    # 这些弹窗必须留给专门逻辑处理，绝不能点 X / 确定误关
    protected = (
        "比对结果",
        "已知晓该名称",
        "计算机比对",
        "返回修改",
        "告知书",
        "承诺书",
        "温馨提示",
        "我已知晓上述内容",
        "一经申报无法修改",
    )
    # 仅允许关掉无关键结果提示（不含核名流程）
    safe_words = ("操作成功",)
    for scope in scopes:
        try:
            dialogs = scope.locator(".el-dialog:visible, .el-message-box:visible")
            for i in range(min(dialogs.count(), 5)):
                dialog = dialogs.nth(i)
                text = (dialog.inner_text(timeout=500) or "").strip()
                if any(p in text for p in protected):
                    continue
                if not any(word in text for word in safe_words):
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


def _confirm_org_type_tip(page: Page, frame: Frame | None = None) -> bool:
    """组织形式选定后的「提示」：合伙等类型需点确定才能继续生成名称。"""
    scopes: list[Any] = []
    if frame is not None:
        scopes.append(frame)
    scopes.append(page)
    markers = ("一经申报无法修改", "请仔细核对投资人信息", "您选择申报的为")
    for scope in scopes:
        try:
            dlg = scope.locator(".el-message-box:visible, .el-dialog:visible").filter(
                has_text=re.compile("|".join(re.escape(m) for m in markers))
            ).first
            if not _visible(dlg):
                continue
            btn = dlg.locator("button, .el-button").filter(has_text=re.compile(r"^确定$")).first
            if not _visible(btn):
                btn = dlg.get_by_role("button", name="确定").first
            if _visible(btn):
                btn.click(timeout=3000)
                page.wait_for_timeout(600)
                return True
        except Exception:
            continue
    return False


def _org_form_select_plan(org: str) -> tuple[str, tuple[str, ...]]:
    """核名页组织形式弹层：内资企业类型 → 第二级选项别名。

    原型 orgForm（「股份有限公司」）需映射到弹层第 1 步正确类型，不能写死「有限公司」。
    合伙：第2步只用「合伙企业（普通/有限合伙）」等真实芯片文案，勿把第1步类型名当第2步别名
    （否则会误点类型按钮并当成已选组织形式）。
    """
    org = (org or "").strip()
    if org in ("股份公司", "股份有限公司"):
        # 原型与填报统一用「股份有限公司」；弹层第1步仍可能显示「股份公司」
        return "股份公司", ("股份有限公司", "股份公司")
    if org == "有限合伙企业":
        # 第1步点「有限合伙企业」；第2步点「合伙企业（有限合伙）」
        return "有限合伙企业", (
            "合伙企业（有限合伙）",
            "合伙企业(有限合伙)",
        )
    if org == "普通合伙企业":
        # 第1步点「普通合伙企业」；第2步点「合伙企业（普通合伙）」
        return "普通合伙企业", (
            "合伙企业（普通合伙）",
            "合伙企业(普通合伙)",
        )
    if org == "个人独资企业":
        # 政务网：个人独资企业 → 经营部（个人独资）
        return "个人独资企业", (
            "经营部（个人独资）",
            "经营部(个人独资)",
            "经营部",
        )
    if org == "个体工商户":
        # 政务网无「个体工商户」一级项：按有限公司 / 有限公司 选择
        return "有限公司", ("有限公司", "有限责任公司")
    if org in ("有限公司", "有限责任公司", ""):
        return "有限公司", ("有限公司", "有限责任公司")
    # 未知：用原文作两级尝试
    return org or "有限公司", (org,) if org else ("有限公司", "有限责任公司")


def _ordered_trade_names(name: dict[str, Any] | None) -> list[str]:
    """字号填报优先级：tradeName 首选，其后按 alternatives 顺序（前端点击选中顺序）。"""
    name = name or {}
    out: list[str] = []
    seen: set[str] = set()
    primary = str(name.get("tradeName") or "").strip()
    if primary:
        out.append(primary)
        seen.add(primary)
    for alt in name.get("alternatives") or []:
        if isinstance(alt, dict):
            t = str(alt.get("tradeName") or "").strip()
        else:
            t = str(alt or "").strip()
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return out


def _org_name_suffixes(org: str) -> tuple[str, ...]:
    """企业名称候选后缀（与组织形式一致）。"""
    _, form_opts = _org_form_select_plan(org)
    # 股份：页面常回显「股份有限公司」
    if "股份" in (org or "") or any("股份" in x for x in form_opts):
        return ("股份有限公司", "股份公司")
    if org == "普通合伙企业" or any("普通合伙" in x for x in form_opts):
        return (
            "合伙企业（普通合伙）",
            "合伙企业(普通合伙)",
            "普通合伙企业",
        )
    if org == "有限合伙企业" or any("有限合伙" in x for x in form_opts):
        return (
            "合伙企业（有限合伙）",
            "合伙企业(有限合伙)",
            "有限合伙企业",
        )
    if any("合伙" in x for x in form_opts):
        return form_opts
    if "独资" in (org or "") or any("经营部" in x for x in form_opts):
        return ("经营部（个人独资）", "经营部(个人独资)", "经营部", "个人独资企业")
    # 个体工商户：与弹层一致，按有限公司后缀匹配名称
    if "个体" in (org or ""):
        return ("有限公司", "有限责任公司")
    return ("有限公司", "有限责任公司")


def _org_matched_in_text(org: str, text: str, form_opts: Iterable[str] | None = None) -> bool:
    text = (text or "").replace(" ", "")
    if not text:
        return False
    # 未选中时回读常是整段弹层目录，不能当成功
    if (
        "请选择企业类型" in text
        or "请选择组织形式" in text
        or ("内资企业" in text and "外资企业" in text)
        or "特殊普通合伙企业" in text
    ):
        return False
    opts = tuple(form_opts or ()) or _org_form_select_plan(org)[1]
    # 合伙 / 个体：只认第2步真实组织形式，避免「只点了企业类型」被判成功
    if org in ("普通合伙企业", "有限合伙企业", "个体工商户", "个人独资企业"):
        needles = opts
    else:
        needles = ((org,) if org else ()) + opts
    return any(o and o.replace(" ", "") in text for o in needles if o)


def _select_org_form_two_step(
    frame: Frame, type_opt: str, form_opts: str | Iterable[str]
) -> bool:
    """组织形式弹层：先点内资企业类型，再点组织形式（支持别名，如股份公司/股份有限公司）。"""
    if isinstance(form_opts, str):
        form_list = (form_opts,) if form_opts else ()
    else:
        form_list = tuple(x for x in form_opts if x)
    if not form_list:
        form_list = ("有限公司",)

    page = frame.page
    page.wait_for_timeout(500)
    popper_sels = (
        ".orgDescPopper:visible",
        ".el-popover:visible",
        ".el-dialog:visible",
        ".el-popper:visible",
    )

    def _scopes() -> list[Any]:
        out: list[Any] = []
        for sel in popper_sels:
            try:
                loc = frame.locator(sel).first
                if _visible(loc):
                    out.append(loc)
            except Exception:
                pass
            try:
                loc2 = page.locator(sel).first
                if _visible(loc2):
                    out.append(loc2)
            except Exception:
                pass
        out.extend([frame, page])
        return out

    def _chip_text_ok(btn, expect: str) -> bool:
        try:
            raw = " ".join((btn.inner_text(timeout=250) or "").split())
        except Exception:
            return False
        return raw.replace(" ", "") == expect.replace(" ", "")

    def _force_click(btn) -> bool:
        try:
            btn.click(timeout=3000, force=True)
            return True
        except Exception:
            try:
                box = btn.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    return True
            except Exception:
                pass
        return False

    def _click_type(scope) -> bool:
        # 只点「内资企业」区的类型芯片（y 在「外资企业」标题之上），绝不点外资
        type_aliases = (type_opt,)
        if type_opt == "股份公司":
            type_aliases = ("股份公司", "股份有限公司")
        elif type_opt == "普通合伙企业":
            type_aliases = ("普通合伙企业",)
        elif type_opt == "有限合伙企业":
            type_aliases = ("有限合伙企业",)
        elif type_opt == "合伙企业":
            type_aliases = ("合伙企业", "普通合伙企业", "有限合伙企业")

        foreign_y: float | None = None
        try:
            foreign = scope.get_by_text("外资企业", exact=True).first
            if _visible(foreign):
                fbox = foreign.bounding_box()
                if fbox:
                    foreign_y = float(fbox["y"])
        except Exception:
            foreign_y = None

        for alias in type_aliases:
            try:
                cands = scope.get_by_text(alias, exact=True)
                for i in range(min(cands.count(), 12)):
                    btn = cands.nth(i)
                    if not _visible(btn) or not _chip_text_ok(btn, alias):
                        continue
                    box = btn.bounding_box()
                    if not box:
                        continue
                    if foreign_y is not None and float(box["y"]) >= foreign_y - 2:
                        continue
                    if _force_click(btn):
                        return True
            except Exception:
                pass
            # 兜底：内资标题后的兄弟块
            try:
                label = scope.get_by_text("内资企业", exact=True).first
                if _visible(label):
                    block = label.locator("xpath=following-sibling::*[1]")
                    btn = block.get_by_text(alias, exact=True).first
                    if _visible(btn) and _chip_text_ok(btn, alias) and _force_click(btn):
                        return True
            except Exception:
                pass
        return False

    def _click_form(scope) -> bool:
        # 只点「2. 请选择组织形式」标题下方的芯片，避免误点第1步类型名
        heading_y: float | None = None
        try:
            heading = scope.get_by_text(re.compile(r"请选择组织形式")).first
            if _visible(heading):
                hbox = heading.bounding_box()
                if hbox:
                    heading_y = float(hbox["y"])
        except Exception:
            heading = None
            heading_y = None
        if heading_y is None:
            return False

        for form_opt in form_list:
            try:
                cands = scope.get_by_text(form_opt, exact=True)
                for i in range(min(cands.count(), 12)):
                    btn = cands.nth(i)
                    if not _visible(btn) or not _chip_text_ok(btn, form_opt):
                        continue
                    box = btn.bounding_box()
                    if not box or float(box["y"]) < heading_y:
                        continue
                    if _force_click(btn):
                        return True
            except Exception:
                pass
            # 再试：标题后的兄弟容器
            try:
                if heading is not None:
                    for sib_i in range(1, 4):
                        container = heading.locator(f"xpath=following-sibling::*[{sib_i}]")
                        btn = container.get_by_text(form_opt, exact=True).first
                        if _visible(btn) and _chip_text_ok(btn, form_opt) and _force_click(btn):
                            return True
            except Exception:
                pass
        return False

    def _wait_form_ready(timeout_ms: int = 4500) -> bool:
        steps = max(1, timeout_ms // 250)
        for _ in range(steps):
            for scope in _scopes():
                for form_opt in form_list:
                    try:
                        btn = scope.get_by_text(form_opt, exact=True).first
                        if _visible(btn) and _chip_text_ok(btn, form_opt):
                            return True
                    except Exception:
                        pass
            page.wait_for_timeout(250)
        return False

    def _popper_open() -> bool:
        for sel in popper_sels:
            try:
                if _visible(frame.locator(sel).first) or _visible(page.locator(sel).first):
                    return True
            except Exception:
                pass
        return False

    typed = formed = False
    for scope in _scopes():
        if not typed:
            typed = _click_type(scope)
            if typed:
                page.wait_for_timeout(500)
                _wait_form_ready()
                break
    if typed and not formed:
        for scope in _scopes():
            formed = _click_form(scope)
            if formed:
                page.wait_for_timeout(400)
                break
        if not formed:
            # 二级偶发未渲染：再等一轮后重试
            _wait_form_ready(3000)
            for scope in _scopes():
                formed = _click_form(scope)
                if formed:
                    page.wait_for_timeout(400)
                    break
    if not (typed and formed):
        return False

    # 确认弹窗（含合伙「一经申报无法修改企业类型」提示）+ 等弹层关闭
    for _ in range(8):
        _confirm_org_type_tip(page, frame)
        _click_text(frame, ["确 定", "确定"], exact=False)
        _click_text(page, ["确 定", "确定"], exact=False)
        page.wait_for_timeout(350)
        if not _popper_open():
            break
    page.wait_for_timeout(400)
    return True


def _choose_first_industry_suggestion(frame: Frame, industry: str) -> tuple[bool, str]:
    """点选行业下拉/自动完成的第一项（前端手输后，填报时固定选首项）。"""
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
        for _wait in range(10):
            for scope in scopes:
                for sel in list_selectors:
                    items = scope.locator(sel)
                    n = min(items.count(), 20)
                    for i in range(n):
                        item = items.nth(i)
                        if not _visible(item):
                            continue
                        text = " ".join((item.inner_text(timeout=400) or "").split())
                        if not text or "请选择" in text:
                            continue
                        try:
                            item.click(timeout=3000, force=True)
                        except Exception:
                            box = item.bounding_box()
                            if box:
                                page.mouse.click(box["x"] + 20, box["y"] + 10)
                            else:
                                continue
                        page.wait_for_timeout(400)
                        return True, text or (industry or "first")
            page.wait_for_timeout(350)
        # 键盘兜底：下箭头 + 回车选第一项
        try:
            box = frame.get_by_placeholder("请输入行业表述", exact=False).first
            if _visible(box):
                box.press("ArrowDown")
                page.wait_for_timeout(250)
                box.press("Enter")
                page.wait_for_timeout(400)
                return True, (industry or "") + "(keyboard-first)"
        except Exception:
            pass
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


def _page_or_frames_has(page: Page, *needles: str) -> bool:
    """主文档或任一 iframe 是否出现关键词（核名弹窗常在 iframe / teleport 层）。"""
    for n in needles:
        if not n:
            continue
        try:
            if page.get_by_text(n, exact=False).count() > 0:
                return True
        except Exception:
            pass
        try:
            if page.locator(".el-dialog, .el-message-box, [role='dialog']").filter(
                has_text=n
            ).count() > 0:
                return True
        except Exception:
            pass
        for fr in _all_frames(page):
            try:
                if fr.get_by_text(n, exact=False).count() > 0:
                    return True
            except Exception:
                pass
            try:
                if fr.locator(".el-dialog, .el-message-box, [role='dialog']").filter(
                    has_text=n
                ).count() > 0:
                    return True
            except Exception:
                pass
            try:
                t = fr.locator("body").inner_text(timeout=300) or ""
                if n in t:
                    return True
            except Exception:
                continue
        try:
            t = page.locator("body").inner_text(timeout=300) or ""
            if n in t:
                return True
        except Exception:
            pass
    return False


def _wait_name_transition(
    page: Page,
    frame: Frame,
    timeout_sec: float = 20.0,
    next_text: str = "下一步",
) -> tuple[bool, str]:
    """Wait for the single Next click to leave the name-declaration stage.

    与其它组织形式相同：出现「比对结果」/「返回修改」/「备用字号」即视为核名下一步有结果，
    后续比对勾选 / 告知书 / 温馨提示由 basic_info；备用字号则点返回修改换字号。
    """
    before_url = page.url
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        # 比对结果弹窗常在 iframe / body 传送门，必须多种方式探测
        if _page_or_frames_has(
            page,
            "比对结果",
            "已知晓该名称存在",
            "计算机比对通过",
            "返回修改",
            "备用字号",
            "备用拟设名称",
        ):
            return True, "compare_dialog"
        legal = _visible_human_confirmation(page, frame)
        if legal:
            return False, "human_confirmation_required: " + legal
        _dismiss_known_info_dialog(page, frame)
        # 等待跳转期间又弹出系统繁忙：关掉后重新点下一步
        if _dismiss_system_busy(page):
            page.wait_for_timeout(900)
            _click_name_next(frame, page, next_text)
            page.wait_for_timeout(500)
            continue
        # wujie 壳页常驻空的 web-reg-apply iframe，必须有实质正文才算进入设立
        for fr in _all_frames(page):
            url = (fr.url or "")
            if "web-reg-apply" not in url and "enterprise-info" not in url:
                continue
            try:
                t = fr.locator("body").inner_text(timeout=400) or ""
            except Exception:
                t = ""
            if any(
                k in t
                for k in ("企业设立", "经办人信息", "基本信息", "设立登记信息", "身份证")
            ):
                return True, "establishment_frame"
        current_url = page.url or ""
        if current_url != before_url and "name-declaration" not in current_url:
            return True, "url_changed"
        try:
            body_probe = page.locator("body").inner_text(timeout=400) or ""
        except Exception:
            body_probe = ""
        if "企业设立登记信息" in body_probe or "设立登记信息" in body_probe:
            return True, "next_stage_text"
        page.wait_for_timeout(250)
    # 超时前再扫一次（截图时常已有弹窗但正文探测失败）
    if _page_or_frames_has(
        page, "比对结果", "已知晓该名称存在", "计算机比对通过", "返回修改", "备用字号"
    ):
        return True, "compare_dialog"
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

    # 名称字号：必须写入完整字号并等候选刷新（避免只生成首字）
    trade_ph = ph.get("trade_name") or "请输入名称字号"
    filled_trade = _fill_trade_name_complete(frame, page, trade=trade, trade_ph=trade_ph)
    _shot(page, case_id, f"name-trade-filled-{trade}")
    if filled_trade:
        rb = _read_trade_input(frame, trade_ph)
        if trade and trade not in rb:
            filled_trade = _fill_trade_name_complete(frame, page, trade=trade, trade_ph=trade_ph)
            _shot(page, case_id, f"name-trade-refilled-{trade}")
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

    # 组织形式：按 payload.name.orgForm 映射内资类型 + 第二级（股份公司≠写死有限公司）
    filled_org = False
    org_readback = ""
    type_opt, form_opts = _org_form_select_plan(org)
    # selectors.yaml 仅作缺省；有表格 orgForm 时以映射为准
    if not org:
        type_opt = name_sel.get("org_type_option") or type_opt
        form_opts = (name_sel.get("org_form_option") or form_opts[0],)
    if _open_select_by_placeholder(frame, ph.get("org_form") or "请选择组织形式"):
        filled_org = _select_org_form_two_step(frame, type_opt, form_opts)
        if not filled_org:
            # 兜底只点第2步真实文案，绝不点「普通合伙企业」等类型名（会误点第1步）
            type_like = {type_opt, "普通合伙企业", "有限合伙企业", "特殊普通合伙企业", "合伙企业"}
            for opt in form_opts:
                if not opt or opt in type_like:
                    continue
                if _pick_dropdown_option(frame, opt):
                    filled_org = True
                    break
    page.wait_for_timeout(800)
    try:
        org_control = frame.get_by_placeholder(ph.get("org_form") or "请选择组织形式", exact=False).first
        if org_control.count() and _visible(org_control):
            org_readback = (org_control.input_value(timeout=800) or "").strip()
            if not org_readback:
                # 优先读 select 展示值，避免祖先 form-item 把整段弹层目录拼进来
                try:
                    org_readback = (
                        org_control.locator(
                            "xpath=ancestor::div[contains(@class,'el-select')][1]"
                            "//*[contains(@class,'el-input__inner') or contains(@class,'el-select__selected-item')]"
                        ).first.inner_text(timeout=600)
                        or ""
                    ).strip()
                except Exception:
                    org_readback = ""
            if not org_readback:
                org_readback = (org_control.locator("xpath=..").inner_text(timeout=800) or "").strip()
    except Exception:
        org_readback = ""
    if not org_readback or "请选择企业类型" in org_readback or "请选择组织形式" in org_readback:
        try:
            org_item = frame.locator(".el-form-item").filter(has_text="组织形式").first
            # 只取已选中的展示节点，不要整项 inner_text（弹层开着时会带目录）
            show = org_item.locator(
                ".el-select .el-input__inner, .el-select__selected-item, input"
            ).first
            if show.count():
                try:
                    org_readback = (show.input_value(timeout=500) or "").strip()
                except Exception:
                    org_readback = ""
                if not org_readback:
                    org_readback = (show.inner_text(timeout=500) or "").strip()
            if not org_readback:
                org_readback = (org_item.inner_text(timeout=800) or "").strip()
        except Exception:
            pass
    # 必须回读到表格指定的组织形式（含股份公司/股份有限公司等别名）
    org_matched = _org_matched_in_text(org, org_readback, form_opts)
    if not filled_org or not org_matched:
        _shot(page, case_id, "organization-selection-failed")
        store.set_status(
            case_id,
            statuses.NEED_HUMAN,
            reason=f"组织形式未按表格填写（期望={org or '空'}，回读={org_readback or '空'}）",
        )
        return ""

    # 合伙等类型选定后可能还有「提示」确认框，挡住名称候选
    for _ in range(3):
        if not _confirm_org_type_tip(page, frame):
            break
        page.wait_for_timeout(400)

    # 多音字 / 生僻字拼音：默认选第一项（如 ga1 ma3… / jia1…）
    for _ in range(4):
        if not _select_first_pinyin_option(page, frame):
            break
        page.wait_for_timeout(300)

    # 选企业名称：先确认候选含完整字号（官网易在首字后刷出残缺项）
    name_suffixes = _org_name_suffixes(org)
    suffix_pat = "|".join(re.escape(s) for s in name_suffixes)
    preview = (
        name.get("fullNamePreview")
        or f"上海{trade}{industry}{name_suffixes[0]}"
    ).strip()
    if not _wait_candidates_contain_trade(
        frame,
        page,
        trade=trade,
        industry=industry,
        name_suffixes=name_suffixes,
        timeout_sec=8,
    ):
        # 残缺候选（如字号启明却只有「启」）：再强制写一遍字号并等待
        _fill_trade_name_complete(frame, page, trade=trade, trade_ph=trade_ph)
        page.wait_for_timeout(800)
        _wait_candidates_contain_trade(
            frame,
            page,
            trade=trade,
            industry=industry,
            name_suffixes=name_suffixes,
            timeout_sec=14,
        )
    selected_name, preview = _select_name_radio_for_trade(
        frame,
        page,
        trade=trade,
        industry=industry,
        name_suffixes=name_suffixes,
        preview=preview,
    )
    if not selected_name:
        # 最后再刷一次字号后重选
        _fill_trade_name_complete(frame, page, trade=trade, trade_ph=trade_ph)
        page.wait_for_timeout(1000)
        selected_name, preview = _select_name_radio_for_trade(
            frame,
            page,
            trade=trade,
            industry=industry,
            name_suffixes=name_suffixes,
            preview=preview or f"上海{trade}{industry}{name_suffixes[0]}",
        )
    if not selected_name:
        _shot(page, case_id, "name-candidate-missing")
        store.set_status(case_id, statuses.NEED_HUMAN, reason="未找到匹配的企业名称候选")
        return ""

    page.wait_for_timeout(500)
    next_text = name_sel.get("next_button_text") or "下一步"
    if not _click_name_next(frame, page, next_text):
        _shot(page, case_id, "next-button-missing")
        store.set_status(case_id, statuses.NEED_HUMAN, reason="未找到下一步按钮")
        return ""
    # 点下一步后若「系统繁忙」：点确定关掉，再点下一步（可多轮）
    _name_next_retry_on_busy(page, frame, case_id=case_id, next_text=next_text)
    # 官方偶发「请选择正确的行业表述」：关弹窗后补选再点下一步
    for busy_try in range(5):
        page.wait_for_timeout(800)
        if _visible_fail_or_pass_dialog(page) != "none":
            break
        if _page_or_frames_has(page, "比对结果", "已知晓该名称存在", "返回修改", "备用字号"):
            break
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
            try:
                radios = frame.locator("label.el-radio, .el-radio").filter(
                    has_text=re.compile(suffix_pat)
                )
                for i in range(min(radios.count(), 10)):
                    r = radios.nth(i)
                    if not _visible(r):
                        continue
                    t = r.inner_text(timeout=400) or ""
                    if trade in t and industry in t and any(s in t for s in name_suffixes):
                        r.click(timeout=3000)
                        break
            except Exception:
                pass
        page.wait_for_timeout(800 + busy_try * 500)
        _click_name_next(frame, page, next_text)

    # 首字号提交后：必须等到备用字号/通过，再决定是否换字号
    first_kind = _wait_beiyong_or_pass(
        page,
        frame,
        next_text=next_text,
        timeout_sec=45,
        case_id=case_id,
        shot_prefix="name-first-result",
    )
    transitioned = first_kind != "none"
    transition_reason = (
        "compare_dialog"
        if first_kind == "pass"
        else ("beiyong_or_fail" if first_kind == "fail" else "page_did_not_transition")
    )
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
        store.set_status(
            case_id,
            statuses.NAME_FAILED,
            reason="字号包含生僻字，请修改字号",
            customer_message="核名不通过，请返回修改字号并重试",
        )
        return ""

    # 严格循环：等备用字号 → 返回修改 → 填新字号 → 下一步 → 再等备用字号 …
    trade_list = _ordered_trade_names(name)
    if first_kind == "pass":
        passed_name = True
    elif first_kind == "fail":
        passed_name = False
        tried = [trade]
        remaining = [t for t in trade_list if t != trade]
        if not remaining:
            _shot(page, case_id, "name-failed")
            store.set_status(
                case_id,
                statuses.NAME_FAILED,
                reason="核名不通过：仅有一个字号且比对未通过，请返回修改字号并重试",
                customer_message="核名不通过，请返回修改字号并重试",
            )
            return ""
        trade_ph = (name_sel.get("placeholders") or {}).get("trade_name") or "请输入名称字号"
        for idx, alt_trade in enumerate(remaining):
            fail_open = (
                _beiyong_dialog_open(page) or _visible_fail_or_pass_dialog(page) == "fail"
            )
            on_name_form = (not fail_open) and _any_visible_text(
                page, "请选择您的企业名称", "名称区划"
            )

            # 前置：有备用字号弹窗则关；已在填写页（刚关掉/上次提交失败）则直接换字号
            if not fail_open and not on_name_form:
                kind_gate = _wait_beiyong_or_pass(
                    page,
                    frame,
                    next_text=next_text,
                    timeout_sec=20,
                    case_id=case_id,
                    shot_prefix=f"name-alt-gate-{idx}",
                )
                if kind_gate == "pass":
                    passed_name = True
                    trade = tried[-1] if tried else trade
                    break
                if kind_gate != "fail":
                    _shot(page, case_id, f"name-alt-no-beiyong-{idx}")
                    break
                fail_open = True

            store.set_status(
                case_id,
                statuses.NAME_CHECKING,
                customer_message=(
                    f"返回修改后填写字号「{alt_trade}」"
                    f"（{idx + 2}/{len(trade_list)}）…"
                ),
            )

            if fail_open:
                _shot(page, case_id, f"name-beiyong-before-return-{idx}")
                # 1) 点返回修改 / X，等到弹窗关闭、回到字号填写页
                _click_return_modify(page, frame)
                if not _wait_back_to_name_form(page, frame, trade_ph=trade_ph, timeout_sec=25):
                    _click_return_modify(page, frame)
                    _wait_back_to_name_form(page, frame, trade_ph=trade_ph, timeout_sec=12)
                _shot(page, case_id, f"name-after-return-modify-{idx}")
            else:
                _dismiss_residual_masks(page, frame)
                _shot(page, case_id, f"name-form-ready-for-alt-{idx}")

            # 重新定位 iframe，避免关窗后 frame 句柄失效
            frame_now = _frame_by_url(page, frame_part, timeout_ms=8000) or frame
            frame = frame_now

            # 2) 必须清掉旧字号，写入新字号并点下一步
            old_val = _read_trade_input(frame, trade_ph)
            ok_submit, preview = _refill_trade_on_name_page(
                frame,
                page,
                case_id=case_id,
                trade=alt_trade,
                industry=industry,
                org=org,
                name_sel=name_sel,
            )
            new_val = _read_trade_input(frame, trade_ph)
            if alt_trade not in new_val:
                # 强制再写一次
                _fill_trade_name_complete(frame, page, trade=alt_trade, trade_ph=trade_ph)
                new_val = _read_trade_input(frame, trade_ph)
                if alt_trade in new_val and not ok_submit:
                    ok_submit, preview = _refill_trade_on_name_page(
                        frame,
                        page,
                        case_id=case_id,
                        trade=alt_trade,
                        industry=industry,
                        org=org,
                        name_sel=name_sel,
                    )
            _shot(page, case_id, f"name-alt-filled-{idx}")
            if old_val == new_val and alt_trade not in new_val:
                _shot(page, case_id, f"name-alt-trade-unchanged-{idx}")

            tried.append(alt_trade)
            trade = alt_trade
            if not ok_submit or alt_trade not in _read_trade_input(frame, trade_ph):
                _shot(page, case_id, f"name-alt-submit-fail-{idx}")
                # 仍在填写页：下一轮直接换下一个字号，勿再空等备用字号弹窗
                continue

            # 3) 必须再等到备用字号（或通过），禁止未出窗就换字号
            kind = _wait_beiyong_or_pass(
                page,
                frame,
                next_text=next_text,
                timeout_sec=45,
                case_id=case_id,
                shot_prefix=f"name-alt-result-{idx}",
            )
            _shot(page, case_id, f"name-alt-after-next-{idx}")
            if kind == "pass":
                passed_name = True
                transitioned = True
                transition_reason = "compare_dialog"
                selected_name = True
                filled_trade = True
                break
            if kind == "fail":
                # 仍有下一字号：下一轮再「返回修改 → 填字号」
                continue
            # 超时仍无弹窗：不跳字号，再点一次下一步并多等一轮
            _click_name_next(frame, page, next_text)
            kind2 = _wait_beiyong_or_pass(
                page,
                frame,
                next_text=next_text,
                timeout_sec=25,
                case_id=case_id,
                shot_prefix=f"name-alt-result-retry-{idx}",
            )
            if kind2 == "pass":
                passed_name = True
                transitioned = True
                transition_reason = "compare_dialog"
                selected_name = True
                filled_trade = True
                break
            if kind2 == "fail":
                continue
            _shot(page, case_id, f"name-alt-still-no-dialog-{idx}")
            # 仍无弹窗则无法按规则换字号，结束尝试
            break

        if not passed_name:
            _shot(page, case_id, "name-failed-all-alts")
            store.set_status(
                case_id,
                statuses.NAME_FAILED,
                reason="核名不通过：已尝试字号 "
                + "、".join(tried)
                + " 均不可用，请返回修改字号并重试",
                customer_message="核名不通过，请返回修改字号并重试",
            )
            return ""
        transitioned = True
        transition_reason = "compare_dialog"
        selected_name = True
        filled_trade = True
    else:
        # 首字号提交后既无备用字号也无通过
        if not transitioned:
            store.set_status(
                case_id,
                statuses.NEED_HUMAN,
                reason=transition_reason or "page_did_not_transition",
            )
            return ""

    # 兜底：若仍停在备用字号且已无下一字号
    if _name_needs_return_modify(page):
        _shot(page, case_id, "name-still-need-return-modify")
        store.set_status(
            case_id,
            statuses.NAME_FAILED,
            reason="核名不通过：已尝试字号 "
            + "、".join(_ordered_trade_names(name) or [trade])
            + " 均不可用，请返回修改字号并重试",
            customer_message="核名不通过，请返回修改字号并重试",
        )
        return ""

    if not transitioned:
        store.set_status(case_id, statuses.NEED_HUMAN, reason=transition_reason)
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
            "finalTrade": trade,
            "triedTrades": _ordered_trade_names(name),
            "nextClickedOnce": True,
            "transition": transition_reason,
            "firstResult": first_kind,
        },
    )
    return preview


def run_fill_pipeline(case_id: str, page: Page) -> None:
    if config.ALLOW_FINAL_SUBMIT:
        raise RuntimeError("一期禁止 ALLOW_FINAL_SUBMIT=true")

    # 提前导入：语法错误在核名前暴露，避免「点下一步后才崩并重拉扫码」
    from apps.worker.playbooks.basic_info import run_basic_info_flow
    from apps.worker.playbooks.capital import run_capital_flow
    from apps.worker.playbooks.opening import run_opening_flow
    from apps.worker.playbooks.personnel import run_personnel_flow
    from apps.worker.playbooks.retry_gate import mark_system_fail, run_stage_with_retry

    sel = _load_selectors()
    name_sel = sel.get("name_self_declaration") or {}
    case = store.get_case(case_id)
    payload = case["payload"]

    if not _configured(name_sel):
        page.goto(config.YCT_PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
        _shot(page, case_id, "portal-after-login")
        mark_system_fail(case_id, reason="selectors.yaml 未配置名称申报入口")
        return

    approved = ""
    name_attempt = 0
    name_busy_used = 0
    name_block = payload.setdefault("name", {}) if isinstance(payload, dict) else {}
    if not isinstance(name_block, dict):
        name_block = {}
        payload["name"] = name_block
    trade_candidates = _ordered_trade_names(name_block)
    trade_idx = 0
    if trade_candidates:
        name_block["tradeName"] = trade_candidates[0]

    while name_attempt < 3:
        if store.is_cancelled(case_id) or store.consume_cancel(case_id):
            store.set_status(
                case_id,
                statuses.CANCELLED,
                customer_message="已取消填报",
                reason="用户取消",
            )
            return
        if _dismiss_system_busy(page):
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
        display_n = name_attempt + 1
        cur_trade = str(name_block.get("tradeName") or "").strip()
        prio_tip = ""
        if trade_candidates and cur_trade:
            prio_tip = f"（优先字号 {trade_idx + 1}/{len(trade_candidates)}：{cur_trade}）"
        store.set_status(
            case_id,
            statuses.NAME_CHECKING,
            customer_message=f"正在填写名称信息…（自动第 {display_n}/3 次）{prio_tip}",
        )
        busy_hit = False
        try:
            approved = _run_name_declaration(case_id, page, name_sel, payload)
        except Exception as e:
            _shot(page, case_id, f"name-error-{display_n}")
            busy_hit = _dismiss_system_busy(page) or (
                "系统繁忙" in str(e) or "稍后重试" in str(e)
            )
            if busy_hit and name_busy_used < 5:
                name_busy_used += 1
                store.set_status(
                    case_id,
                    statuses.NAME_CHECKING,
                    customer_message="名称申报遇到系统繁忙，已关闭弹窗并重试…",
                )
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
                continue
            if name_attempt >= 2:
                mark_system_fail(case_id, reason=f"名称申报异常: {e}")
                return
            name_attempt += 1
            try:
                page.wait_for_timeout(400)
            except Exception:
                pass
            continue

        st = str(store.get_case(case_id).get("status") or "")
        # 核名业务失败 / 需本人确认 / 明确 need_human：不自动重试整段核名
        if st in (
            statuses.NAME_FAILED,
            statuses.HUMAN_CONFIRMATION_REQUIRED,
            statuses.NEED_HUMAN,
        ):
            # 比对结果已出时不应停在 need_human；交由 basic 弹窗链
            if approved:
                break
            reason = (store.get_case(case_id).get("reason") or "")
            if st == statuses.NEED_HUMAN and "比对" not in reason and "已知晓" not in reason:
                return
            # 核名未通过：仅内层已穷尽全部字号才结束；否则改试下一优先字号
            if st == statuses.NAME_FAILED and trade_idx + 1 < len(trade_candidates):
                reason_l = reason or ""
                if _name_fail_exhausted(reason_l):
                    return
                trade_idx, failed_trade, next_trade = _advance_trade_candidate(
                    name_block, trade_candidates, trade_idx
                )
                # 先点返回修改再整段重填
                try:
                    _click_return_modify(page, None)
                except Exception:
                    pass
                store.set_status(
                    case_id,
                    statuses.NAME_CHECKING,
                    customer_message=(
                        f"字号「{failed_trade}」未通过，改试下一优先字号「{next_trade}」"
                        f"（{trade_idx + 1}/{len(trade_candidates)}）…"
                    ),
                    reason=reason,
                )
                name_attempt = 0
                try:
                    page.wait_for_timeout(600)
                except Exception:
                    pass
                continue
            if st in (statuses.NAME_FAILED, statuses.HUMAN_CONFIRMATION_REQUIRED):
                return
        if approved:
            break
        reason = (store.get_case(case_id).get("reason") or "")
        if any(k in reason for k in ("比对结果", "已知晓该名称", "计算机比对", "承诺书", "告知书")):
            break
        busy_hit = _dismiss_system_busy(page)
        if busy_hit and name_busy_used < 5:
            name_busy_used += 1
            store.set_status(
                case_id,
                statuses.NAME_CHECKING,
                customer_message="名称申报遇到系统繁忙，已关闭弹窗并重试…",
            )
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            continue
        name_attempt += 1
        if name_attempt < 3:
            try:
                page.wait_for_timeout(400)
            except Exception:
                pass

    name = payload.get("name") or {}
    org_form = (name.get("orgForm") or "有限公司").strip()
    name_suffixes = _org_name_suffixes(org_form)
    preview = (
        approved
        or name.get("fullNamePreview")
        or f"上海{name.get('tradeName') or ''}{name.get('industry') or ''}{name_suffixes[0]}"
    )

    # 核名已点下一步但停在「比对结果」等弹窗时，仍进入基本信息板块
    if not approved:
        reason = (store.get_case(case_id).get("reason") or "")
        st = str(store.get_case(case_id).get("status") or "")
        if st in (statuses.NAME_FAILED, statuses.HUMAN_CONFIRMATION_REQUIRED):
            return
        if any(k in reason for k in ("比对结果", "已知晓该名称", "计算机比对", "承诺书", "告知书")):
            approved = preview
        else:
            mark_system_fail(
                case_id,
                reason=reason or "名称信息未完成",
                result={"currentUrl": getattr(page, "url", "")},
            )
            return

    # 核名后：基本信息 → 出资 → 人员 → 开办；失败立即重试，3 次仍失败则提示前端重试/客服
    # 基本信息阶段若因「备用字号/比对未通过」失败，点返回修改并改试下一优先字号后重跑核名+基本信息
    while True:
        base_result: dict[str, Any] = {
            "approvedName": approved,
            "tradeName": name.get("tradeName", ""),
            "industry": name.get("industry", ""),
            "enterpriseType": org_form or "有限公司",
            "organizationForm": name.get("orgForm", ""),
            "currentUrl": page.url,
        }

        filled, page, outcome = run_stage_with_retry(
            case_id,
            page,
            stage_key="basic",
            stage_label="基本信息",
            runner=lambda p: run_basic_info_flow(case_id, p, payload),
            base_result=base_result,
            dump_name="fill-basic-info-result.json",
        )
        if outcome == "ok":
            break

        name_failed = bool((filled or {}).get("nameFailed")) or (
            str(store.get_case(case_id).get("status") or "") == statuses.NAME_FAILED
        )
        if name_failed and trade_idx + 1 < len(trade_candidates):
            reason = (store.get_case(case_id).get("reason") or "") or (
                (filled or {}).get("reason") or ""
            )
            if _name_fail_exhausted(reason):
                return
            trade_idx, failed_trade, next_trade = _advance_trade_candidate(
                name_block, trade_candidates, trade_idx
            )
            try:
                _click_return_modify(page, None)
            except Exception:
                pass
            page.wait_for_timeout(800)
            store.set_status(
                case_id,
                statuses.NAME_CHECKING,
                customer_message=(
                    f"字号「{failed_trade}」未通过，返回修改后改试「{next_trade}」"
                    f"（{trade_idx + 1}/{len(trade_candidates)}）…"
                ),
            )
            # 用下一字号重跑核名
            approved = ""
            name_attempt = 0
            name_busy_used = 0
            while name_attempt < 3:
                if store.is_cancelled(case_id) or store.consume_cancel(case_id):
                    store.set_status(
                        case_id,
                        statuses.CANCELLED,
                        customer_message="已取消填报",
                        reason="用户取消",
                    )
                    return
                store.set_status(
                    case_id,
                    statuses.NAME_CHECKING,
                    customer_message=(
                        f"正在填写名称信息…（字号 {trade_idx + 1}/{len(trade_candidates)}："
                        f"{next_trade}）"
                    ),
                )
                try:
                    approved = _run_name_declaration(case_id, page, name_sel, payload)
                except Exception as e:
                    if _dismiss_system_busy(page) and name_busy_used < 5:
                        name_busy_used += 1
                        page.wait_for_timeout(1500)
                        continue
                    name_attempt += 1
                    if name_attempt >= 3:
                        mark_system_fail(case_id, reason=f"名称申报异常: {e}")
                        return
                    continue
                st2 = str(store.get_case(case_id).get("status") or "")
                if st2 == statuses.NAME_FAILED:
                    reason2 = store.get_case(case_id).get("reason") or ""
                    if _name_fail_exhausted(reason2):
                        return
                    if trade_idx + 1 < len(trade_candidates):
                        trade_idx, failed_trade, next_trade = _advance_trade_candidate(
                            name_block, trade_candidates, trade_idx
                        )
                        try:
                            _click_return_modify(page, None)
                        except Exception:
                            pass
                        name_attempt = 0
                        store.set_status(
                            case_id,
                            statuses.NAME_CHECKING,
                            customer_message=(
                                f"字号「{failed_trade}」未通过，改试「{next_trade}」"
                                f"（{trade_idx + 1}/{len(trade_candidates)}）…"
                            ),
                        )
                        continue
                    return
                if approved:
                    name = payload.get("name") or name
                    preview = approved
                    break
                name_attempt += 1
            if not approved:
                return
            continue
        return

    base_result = {
        **base_result,
        "currentUrl": page.url,
        "dialogs": (filled or {}).get("dialogs"),
        "fill": filled,
    }

    capital, page, outcome = run_stage_with_retry(
        case_id,
        page,
        stage_key="capital",
        stage_label="出资情况",
        runner=lambda p: run_capital_flow(case_id, p, payload),
        base_result=base_result,
        dump_name="fill-capital-result.json",
    )
    if outcome != "ok":
        return

    base_result = {
        **base_result,
        "currentUrl": page.url,
        "capital": capital,
    }

    personnel, page, outcome = run_stage_with_retry(
        case_id,
        page,
        stage_key="personnel",
        stage_label="人员信息",
        runner=lambda p: run_personnel_flow(case_id, p, payload),
        base_result=base_result,
        dump_name="fill-personnel-result.json",
    )
    if outcome != "ok":
        return

    base_result = {
        **base_result,
        "currentUrl": page.url,
        "personnel": personnel,
    }

    opening, page, outcome = run_stage_with_retry(
        case_id,
        page,
        stage_key="opening",
        stage_label="企业开办",
        runner=lambda p: run_opening_flow(case_id, p, payload),
        base_result=base_result,
        filling_status=statuses.CHOOSING_PROCESS,
        dump_name="fill-opening-result.json",
    )
    if outcome != "ok":
        return

    base_result = {
        **base_result,
        "currentUrl": page.url,
        "opening": opening,
    }
    store.set_status(
        case_id,
        statuses.READY_FOR_USER_SUBMIT,
        result=base_result,
        customer_message="自动填报已完成（已进入进度跟踪页）。请继续完成电子签名",
    )
