"""出资情况板块（新增，不改核名/基本信息代码）。

流程（基本信息「下一步」之后，页面已在 stockholder-info）：
1. 注册资本填 100（万人民币元）
2. 点「+ 增加 >>」打开股东弹窗
3. 出资类型勾选「自然人」→ 出现复用人员信息 → 选列表第一个
4. 投资人类型=自然人股东 / 民族=汉族 / 户籍=贵州省/安顺市/西秀区
   住所=贵州省安顺市西秀区鸡场乡鸡场散居户 / 移动电话=13984439335
5. 认缴出资信息点「增加」一条后填写：开始=当日，结束=4年后，方式=货币，金额=100
6. 确定 → 出资情况页 → 下一步
   注：复用人员信息选中后不再上传身份证，直接往下填。
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page

from apps.api import statuses, store

DEFAULT_ID_FRONT = Path(r"C:\Users\Lenovo\Desktop\身份证正面.jpg")
DEFAULT_ID_BACK = Path(r"C:\Users\Lenovo\Desktop\身份证反面.jpg")


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # 2/29 → 2/28
        return d.replace(year=d.year + years, day=28)


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


def _frame_stockholder(page: Page, timeout_ms: int = 45000) -> Frame | None:
    """定位出资情况页所在 web-reg-apply 帧。"""
    end = time.time() + timeout_ms / 1000
    markers = ("出资情况", "注册资本", "股东", "认缴合计", "+ 增加")
    while time.time() < end:
        for fr in page.frames:
            url = fr.url or ""
            name = fr.name or ""
            if "web-reg-apply" not in url and name != "web-reg-apply":
                continue
            if "stockholder-info" in (page.url or "") or "stockholder" in url:
                try:
                    body = _body(fr).inner_text(timeout=800) or ""
                except Exception:
                    body = ""
                if any(m in body for m in markers):
                    return fr
            try:
                body = _body(fr).inner_text(timeout=600) or ""
            except Exception:
                body = ""
            if "doesn't work properly without JavaScript" in body:
                continue
            if sum(1 for m in markers if m in body) >= 2:
                return fr
        for fr in page.frames:
            try:
                if fr.get_by_text("注册资本", exact=False).count() > 0 and fr.get_by_text(
                    "股东", exact=False
                ).count() > 0:
                    return fr
            except Exception:
                continue
        page.wait_for_timeout(400)
    return None


def _fill_by_label(scope: Page | Frame, label: str, value: str) -> bool:
    if not value:
        return False
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item):
        # 宽松：含 label 的容器
        item = scope.locator("div, tr, li").filter(has_text=re.compile(rf"^\s*\*?\s*{re.escape(label)}")).first
    if not _visible(item):
        return False
    inp = item.locator(
        "input.el-input__inner, textarea.el-textarea__inner, input[type='text'], input:not([type='hidden'])"
    ).first
    if not _visible(inp):
        return False
    try:
        inp.click(timeout=3000)
        inp.fill("")
        inp.fill(str(value), timeout=5000)
        try:
            inp.dispatch_event("input")
            inp.dispatch_event("change")
            inp.press("Tab")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _page_of(scope: Page | Frame) -> Page:
    return scope if isinstance(scope, Page) else scope.page


def _select_dropdown(scope: Page | Frame, label: str, option: str) -> bool:
    """点开 label 旁下拉，选 option。"""
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item):
        return False
    try:
        trigger = item.locator(
            ".el-select .el-input__inner, .el-select, input[readonly], .el-input__inner"
        ).first
        if not _visible(trigger):
            return False
        trigger.click(timeout=3000)
        page = _page_of(scope)
        page.wait_for_timeout(400)
        for root in _all_scopes(page):
            opt = root.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item, "
                ".el-popper:visible li"
            ).filter(has_text=re.compile(rf"^\s*{re.escape(option)}\s*$")).first
            if not _visible(opt, timeout=600):
                opt = root.get_by_text(option, exact=True).first
            if _visible(opt, timeout=600):
                opt.click(timeout=3000)
                return True
        for root in _all_scopes(page):
            opt = root.get_by_text(option, exact=False).first
            if _visible(opt, timeout=500):
                opt.click(timeout=3000)
                return True
    except Exception:
        return False
    return False


def _select_cascader(scope: Page | Frame, label: str, parts: list[str]) -> bool:
    """户籍所属省市县等级联选择。"""
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item):
        return False
    try:
        trigger = item.locator(
            ".el-cascader .el-input__inner, .el-cascader input, input.el-input__inner"
        ).first
        if not _visible(trigger):
            return False
        trigger.click(timeout=3000)
        page = scope.page if hasattr(scope, "page") else scope  # type: ignore[attr-defined]
        if not isinstance(page, Page):
            page = scope.page  # type: ignore[attr-defined]
        page.wait_for_timeout(400)
        for part in parts:
            clicked = False
            for root in _all_scopes(page):
                node = root.locator(
                    ".el-cascader-panel:visible .el-cascader-node, "
                    ".el-cascader-menu:visible .el-cascader-node, "
                    ".el-popper:visible li, .el-cascader-node"
                ).filter(has_text=re.compile(rf"^\s*{re.escape(part)}\s*$")).first
                if not _visible(node, timeout=500):
                    node = root.get_by_text(part, exact=True).first
                if _visible(node, timeout=500):
                    try:
                        node.click(timeout=2500)
                        clicked = True
                        page.wait_for_timeout(350)
                        break
                    except Exception:
                        continue
            if not clicked:
                return False
        return True
    except Exception:
        return False


def _pick_date(page: Page, scope: Page | Frame, label: str, target: date) -> bool:
    """打开日期控件并选中目标日（尽量用输入框直接填）。"""
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item):
        # 表格内单元格
        item = scope.locator("td, .el-table__cell, div").filter(
            has_text=re.compile(re.escape(label))
        ).first
    # 认缴弹层里可能没有 label，按 placeholder / 列顺序处理由调用方传 locator
    inp = None
    if _visible(item):
        inp = item.locator("input.el-input__inner, input").first
    if not _visible(inp):
        return False
    text = target.strftime("%Y-%m-%d")
    try:
        inp.click(timeout=3000)
        inp.fill("")
        inp.fill(text, timeout=4000)
        try:
            inp.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(300)
        # 若日历弹出，点当天/对应日
        for root in _all_scopes(page):
            try:
                if target == date.today():
                    cell = root.locator("td.available.today, td.today").first
                    if _visible(cell, timeout=400):
                        cell.click(timeout=2000)
                        return True
                # 按日数字点（当月）
                day = str(target.day)
                cell = root.locator(
                    f"td.available:not(.disabled) span:text-is('{day}'), "
                    f"td.available:not(.prev-month):not(.next-month)"
                ).filter(has_text=re.compile(rf"^\s*{day}\s*$")).first
                if _visible(cell, timeout=300):
                    cell.click(timeout=2000)
                    return True
            except Exception:
                continue
        return True
    except Exception:
        return False


def _defaults(payload: dict[str, Any]) -> dict[str, Any]:
    """用户指定值优先；payload 仅作姓名/电话/住所兜底。"""
    inv = (payload.get("investors") or [{}])[0]
    lr = ((payload.get("personnel") or {}).get("legalRepresentative") or {})
    return {
        "registered_capital": "100",
        "investor_type": "自然人股东",
        "ethnicity": "汉族",
        "hukou": ["贵州省", "安顺市", "西秀区"],
        "address": (inv.get("address") or "贵州省安顺市西秀区鸡场乡鸡场散居户").strip(),
        "mobile": (inv.get("mobile") or "13984439335").strip(),
        "method": (inv.get("investmentMethod") or "货币").strip(),
        "amount": "100",
        "name": (inv.get("name") or lr.get("name") or "吴文彬").strip(),
        "idNumber": (inv.get("idNumber") or lr.get("idNumber") or "").strip(),
    }


def _find_dialog(page: Page, *hints: str):
    for scope in _all_scopes(page):
        for sel in (
            ".el-message-box:visible",
            ".el-dialog:visible",
            ".el-overlay-dialog .el-dialog",
            "[role='dialog']",
            ".el-message-box",
        ):
            try:
                dialogs = scope.locator(sel)
                for i in range(min(dialogs.count(), 8)):
                    d = dialogs.nth(i)
                    if not _visible(d, timeout=400):
                        continue
                    text = d.inner_text(timeout=800) or ""
                    if all(h in text for h in hints):
                        return d, scope
            except Exception:
                continue
    return None, None


def _dismiss_switch_tip(page: Page) -> bool:
    """关闭「切换出资者类型…将被清空」提示。"""
    dismissed = False
    for _ in range(4):
        tip, scope = _find_dialog(page, "切换出资者类型")
        if tip is None:
            tip, scope = _find_dialog(page, "将被清空")
        if tip is None:
            # message-box
            for root in _all_scopes(page):
                try:
                    box = root.locator(".el-message-box").filter(
                        has_text=re.compile("清空|出资者类型")
                    ).first
                    if _visible(box, timeout=400):
                        tip = box
                        scope = root
                        break
                except Exception:
                    continue
        if tip is None:
            break
        try:
            btn = tip.locator("button.el-button--primary").filter(has_text="确定").first
            if not _visible(btn, timeout=400):
                btn = tip.get_by_role("button", name=re.compile(r"确\s*定")).first
            if _visible(btn, timeout=400):
                btn.click(force=True, timeout=2500)
                dismissed = True
                page.wait_for_timeout(500)
                continue
        except Exception:
            pass
        if scope is not None and _click_text(scope, ["确定"], exact=True):
            dismissed = True
            page.wait_for_timeout(500)
            continue
        break
    return dismissed


def _click_add_button(scope: Page | Frame, near_text: str | None = None) -> bool:
    """点「+ 增加 >>」；只在真正点到按钮时返回 True（避免误判）。"""
    page = _page_of(scope)
    add_re = re.compile(r"^\s*\+?\s*增\s*加\s*(>>)?\s*$")

    def _try_click(btn) -> bool:
        if not _visible(btn, timeout=600):
            return False
        try:
            btn.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        try:
            btn.click(force=True, timeout=4000)
            return True
        except Exception:
            try:
                box = btn.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    return True
            except Exception:
                return False
        return False

    # 1) 标题同行右侧增加按钮
    if near_text:
        try:
            title = scope.get_by_text(near_text, exact=True).first
            if not _visible(title):
                title = scope.get_by_text(near_text, exact=False).first
            if _visible(title):
                for xpath in (
                    "xpath=ancestor::div[contains(@class,'header') or contains(@class,'title') or contains(@class,'hd')][1]",
                    "xpath=ancestor::div[.//button or .//a][1]",
                    "xpath=ancestor::div[2]",
                    "xpath=ancestor::div[3]",
                ):
                    row = title.locator(xpath)
                    if row.count() == 0:
                        continue
                    btn = row.first.locator("button, a, span").filter(has_text=add_re).first
                    if _try_click(btn):
                        return True
        except Exception:
            pass

    # 2) 限定在 scope 内的增加按钮
    try:
        btn = scope.locator("button, a").filter(has_text=add_re).first
        if _try_click(btn):
            return True
    except Exception:
        pass
    try:
        btn = scope.get_by_role("button", name=add_re).first
        if _try_click(btn):
            return True
    except Exception:
        pass
    try:
        btn = scope.get_by_text("+ 增加 >>", exact=True).first
        if _try_click(btn):
            return True
    except Exception:
        pass
    try:
        btn = scope.get_by_text("增加 >>", exact=True).first
        if _try_click(btn):
            return True
    except Exception:
        pass
    return False


def _natural_checked(dialog) -> bool:
    if dialog is None:
        return False
    try:
        lab = dialog.locator("label.el-checkbox, label.el-radio").filter(
            has_text=re.compile(r"^\s*自然人\s*$")
        ).first
        if not _visible(lab, timeout=500):
            lab = dialog.locator("label.el-checkbox, label.el-radio").filter(
                has_text=re.compile(r"自然人")
            ).first
        if not _visible(lab, timeout=500):
            return False
        cls = lab.get_attribute("class") or ""
        if "is-checked" in cls:
            return True
        inp = lab.locator("input").first
        if inp.count() and inp.is_checked():
            return True
    except Exception:
        pass
    return False


def _check_natural_person(page: Page, dialog) -> bool:
    """勾选出资类型「自然人」前面的小框，并确认勾选成功。"""
    if dialog is None:
        return False
    if _natural_checked(dialog):
        return True
    for sel in ("label.el-checkbox", "label.el-radio"):
        try:
            lab = dialog.locator(sel).filter(has_text=re.compile(r"自然人")).first
            if not _visible(lab):
                continue
            inner = lab.locator(
                ".el-checkbox__inner, .el-radio__inner, input[type='checkbox'], input[type='radio']"
            ).first
            if _visible(inner, timeout=400):
                inner.click(force=True, timeout=2500)
            else:
                lab.click(force=True, timeout=2500)
            page.wait_for_timeout(400)
            if _natural_checked(dialog):
                return True
        except Exception:
            continue
    try:
        node = dialog.get_by_text("自然人", exact=True).first
        if _visible(node):
            box = node.bounding_box()
            if box:
                page.mouse.click(box["x"] - 18, box["y"] + box["height"] / 2)
                page.wait_for_timeout(400)
                if _natural_checked(dialog):
                    return True
    except Exception:
        pass
    return _natural_checked(dialog)


def _reuse_first_person(page: Page, host_dialog=None, person_name: str = "") -> bool:
    """点击「复用人员信息」下拉，选第一人（优先匹配姓名）。"""
    roots = []
    if host_dialog is not None:
        roots.append(host_dialog)
    roots.extend(_all_scopes(page))
    clicked = False
    for root in roots:
        try:
            item = root.locator(".el-form-item").filter(has_text=re.compile(r"复用人员信息")).first
            if _visible(item, timeout=600):
                trigger = item.locator(
                    ".el-select .el-input__inner, .el-input__inner, input, .el-select"
                ).first
                if _visible(trigger, timeout=500):
                    trigger.click(force=True, timeout=3000)
                    clicked = True
                    break
            lab = root.get_by_text("复用人员信息", exact=False).first
            if _visible(lab, timeout=400):
                box = lab.bounding_box()
                if box:
                    page.mouse.click(box["x"] + min(box["width"] + 100, 260), box["y"] + box["height"] / 2)
                    clicked = True
                    break
        except Exception:
            continue
    if not clicked:
        return False
    page.wait_for_timeout(700)
    # 下拉选项：优先姓名，否则第一项
    for root in _all_scopes(page):
        try:
            opts = root.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item, "
                ".el-popper:visible li.el-select-dropdown__item, "
                ".el-select-dropdown:visible li, "
                ".el-autocomplete-suggestion:visible li"
            )
            n = min(opts.count(), 12)
            if n == 0:
                continue
            chosen = None
            if person_name:
                for i in range(n):
                    o = opts.nth(i)
                    if not _visible(o, timeout=300):
                        continue
                    txt = (o.inner_text(timeout=300) or "").strip()
                    if person_name in txt:
                        chosen = o
                        break
            if chosen is None:
                for i in range(n):
                    o = opts.nth(i)
                    if _visible(o, timeout=300):
                        chosen = o
                        break
            if chosen is not None:
                chosen.click(timeout=3000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    # 弹窗表格兜底
    dlg, _ = _find_dialog(page, "复用人员信息")
    if dlg is None:
        dlg, _ = _find_dialog(page, "选择人员")
    if dlg is None:
        return False
    try:
        rows = dlg.locator(".el-table__body-wrapper tbody tr, tbody tr")
        for i in range(min(rows.count(), 8)):
            row = rows.nth(i)
            if not _visible(row, timeout=400):
                continue
            txt = (row.inner_text(timeout=400) or "").strip()
            if not txt or "暂无数据" in txt:
                continue
            if person_name and person_name not in txt and i > 0:
                continue
            row.click(timeout=3000)
            page.wait_for_timeout(400)
            ok = dlg.locator("button.el-button--primary").filter(has_text="确定").first
            if _visible(ok, timeout=500):
                ok.click(timeout=3000)
            page.wait_for_timeout(800)
            return True
    except Exception:
        pass
    return False


def _upload_id_in_dialog(dialog, page: Page) -> dict[str, Any]:
    """股东弹窗内上传身份证人像面/国徽面。"""
    out = {"front": False, "back": False}
    front = DEFAULT_ID_FRONT if DEFAULT_ID_FRONT.exists() else None
    back = DEFAULT_ID_BACK if DEFAULT_ID_BACK.exists() else None
    if front is None and back is None:
        return out
    try:
        inputs = dialog.locator("input[type='file']")
        n = inputs.count()
        if n >= 1 and front is not None:
            inputs.nth(0).set_input_files(str(front.resolve()))
            out["front"] = True
            page.wait_for_timeout(1200)
        if n >= 2 and back is not None:
            inputs.nth(1).set_input_files(str(back.resolve()))
            out["back"] = True
            page.wait_for_timeout(1200)
    except Exception as e:
        out["error"] = str(e)
    # 等 OCR
    if out.get("front") or out.get("back"):
        page.wait_for_timeout(2500)
    return out


def _fill_person_basics(dialog, cfg: dict[str, Any], page: Page) -> dict[str, Any]:
    """补齐姓名/证件/性别等（复用失败时的兜底）。"""
    info: dict[str, Any] = {}
    if cfg.get("name"):
        # 避免把注册资本 100 留在姓名框
        try:
            item = dialog.locator(".el-form-item").filter(has_text=re.compile(r"姓名")).first
            if _visible(item):
                inp = item.locator("input").first
                if _visible(inp):
                    cur = inp.input_value(timeout=500) or ""
                    if cur.strip() in ("", "100", cfg["registered_capital"]):
                        inp.fill("")
                        inp.fill(cfg["name"])
                        info["name"] = True
                    else:
                        info["name"] = True
        except Exception:
            info["name"] = _fill_by_label(dialog, "姓名", cfg["name"])
    # 证件类型
    info["idType"] = _select_dropdown(dialog, "证件类型", "中华人民共和国居民身份证")
    if not info["idType"]:
        info["idType"] = _select_dropdown(dialog, "证件类型", "身份证")
    # 证件号码
    id_no = cfg.get("idNumber") or ""
    if id_no:
        info["idNo"] = _fill_by_label(dialog, "证件号码", id_no)
    # 性别：按身份证倒数第二位
    try:
        if id_no and len(id_no) >= 17:
            gender = "男" if int(id_no[16]) % 2 == 1 else "女"
            lab = dialog.locator("label.el-radio").filter(has_text=gender).first
            if _visible(lab, timeout=500):
                lab.click(force=True, timeout=2000)
                info["gender"] = gender
    except Exception:
        pass
    return info


def _fill_amount_verified(page: Page, scope, amount: str) -> dict[str, Any]:
    """填写认缴出资金额并校验 input.value 已变为目标值。"""
    info: dict[str, Any] = {"ok": False}
    target = str(amount).strip()

    def _verify(inp) -> bool:
        try:
            val = (inp.input_value(timeout=800) or "").strip()
            info["value"] = val
            return target in val.replace(",", "") or val.replace(".00", "") == target
        except Exception:
            return False

    def _type_in(inp, via: str) -> bool:
        try:
            inp.scroll_into_view_if_needed(timeout=1500)
            inp.click(force=True, timeout=2500)
            page.wait_for_timeout(120)
            try:
                inp.fill("")
            except Exception:
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            inp.fill(target, force=True)
            try:
                inp.evaluate(
                    """(e, v) => {
                      e.value = v;
                      e.dispatchEvent(new Event('input', {bubbles:true}));
                      e.dispatchEvent(new Event('change', {bubbles:true}));
                      e.blur();
                    }""",
                    target,
                )
            except Exception:
                pass
            try:
                inp.press("Tab")
            except Exception:
                pass
            page.wait_for_timeout(250)
            if _verify(inp):
                info["ok"] = True
                info["via"] = via
                return True
        except Exception as e:
            info["err"] = str(e)
        return False

    # 1) 标签「认缴出资金额」旁的 input
    for lab_txt in ("认缴出资金额(万)", "认缴出资金额", "出资金额"):
        try:
            lab = scope.get_by_text(lab_txt, exact=False).first
            if not _visible(lab, timeout=400):
                continue
            lab.scroll_into_view_if_needed(timeout=1500)
            for xpath in (
                "xpath=ancestor::div[contains(@class,'el-form-item')][1]//input[not(@readonly)]",
                "xpath=following::input[not(@readonly)][1]",
                "xpath=../following-sibling::*//input[not(@readonly)]",
                "xpath=../../..//input[not(@readonly)]",
            ):
                inp = lab.locator(xpath).first
                if not _visible(inp, timeout=350):
                    continue
                try:
                    if inp.evaluate("e => !!e.closest('.el-date-editor,.el-select')"):
                        continue
                except Exception:
                    pass
                if _type_in(inp, f"label:{lab_txt}"):
                    return info
        except Exception:
            continue

    # 2) JS 按标签几何找最近可填框，写入并回读
    try:
        js = scope.evaluate(
            """(root, amount) => {
              const norm = (s) => (s || '').replace(/\\s+/g,'').replace(/\\u00a0/g,'');
              const body = root.querySelector('.el-dialog__body') || root;
              let lab = null;
              for (const el of body.querySelectorAll('*')) {
                const own = norm(Array.from(el.childNodes).filter(n => n.nodeType===3).map(n => n.textContent).join(''));
                if (own.includes('认缴出资金额') && own.length < 30) { lab = el; break; }
              }
              if (!lab) return {ok:false, reason:'no-label'};
              lab.scrollIntoView({block:'center'});
              const lr = lab.getBoundingClientRect();
              let best = null, bestScore = 1e12;
              for (const inp of body.querySelectorAll('input')) {
                if (inp.closest('.el-date-editor') || inp.closest('.el-select')) continue;
                if (inp.disabled || inp.readOnly) continue;
                const r = inp.getBoundingClientRect();
                if (r.width < 24 || r.height < 10 || r.bottom <= 0) continue;
                const dy = Math.abs((r.y + r.height/2) - (lr.y + lr.height/2));
                if (dy > 56) continue;
                const dx = (r.x + r.width/2) - (lr.x + lr.width/2);
                const score = dy * 5 + Math.abs(dx);
                if (score < bestScore) { best = inp; bestScore = score; }
              }
              if (!best) return {ok:false, reason:'no-input'};
              best.focus();
              best.value = '';
              best.value = amount;
              best.dispatchEvent(new Event('input', {bubbles:true}));
              best.dispatchEvent(new Event('change', {bubbles:true}));
              const r = best.getBoundingClientRect();
              return {ok:true, value: best.value, x: r.x + r.width/2, y: r.y + r.height/2};
            }""",
            target,
        )
        info["js"] = js
        if isinstance(js, dict) and js.get("ok"):
            if target in str(js.get("value") or ""):
                info["ok"] = True
                info["via"] = "js-set"
                info["value"] = js.get("value")
                return info
            # 再键盘敲一遍
            if js.get("x"):
                page.mouse.click(float(js["x"]), float(js["y"]))
                page.keyboard.press("Control+A")
                page.keyboard.type(target, delay=40)
                page.keyboard.press("Tab")
                page.wait_for_timeout(300)
                # 再读一次
                val2 = scope.evaluate(
                    """(root, amount) => {
                      const norm = (s) => (s || '').replace(/\\s+/g,'');
                      const body = root.querySelector('.el-dialog__body') || root;
                      for (const el of body.querySelectorAll('*')) {
                        const own = norm(Array.from(el.childNodes).filter(n => n.nodeType===3).map(n => n.textContent).join(''));
                        if (!(own.includes('认缴出资金额') && own.length < 30)) continue;
                        const lr = el.getBoundingClientRect();
                        for (const inp of body.querySelectorAll('input')) {
                          if (inp.closest('.el-date-editor') || inp.closest('.el-select')) continue;
                          const r = inp.getBoundingClientRect();
                          if (Math.abs(r.y - lr.y) < 56) return inp.value || '';
                        }
                      }
                      return '';
                    }""",
                    target,
                )
                info["value"] = val2
                if target in str(val2 or ""):
                    info["ok"] = True
                    info["via"] = "js-type"
                    return info
    except Exception as e:
        info["jsErr"] = str(e)

    return info


def _fill_subscription(page: Page, dialog, cfg: dict[str, Any]) -> dict[str, Any]:
    """认缴出资信息：先点「增加」一条，再填开始/结束/货币/100。"""
    info: dict[str, Any] = {"opened": False, "ok": False}
    today = date.today()
    end_d = _plus_years(today, 4)
    start_s = today.strftime("%Y-%m-%d")
    end_s = end_d.strftime("%Y-%m-%d")
    info["endDate"] = end_d.isoformat()
    sub_dlg = None

    # 滚到认缴区
    try:
        title = dialog.get_by_text("认缴出资信息", exact=True).first
        if not _visible(title, timeout=600):
            title = dialog.get_by_text("认缴出资信息", exact=False).first
        if _visible(title, timeout=800):
            title.scroll_into_view_if_needed(timeout=2000)
            page.wait_for_timeout(400)
    except Exception:
        try:
            dialog.evaluate(
                """(el) => {
                  const body = el.querySelector('.el-dialog__body') || el;
                  body.scrollTop = body.scrollHeight;
                }"""
            )
        except Exception:
            pass

    before_txt = ""
    try:
        before_txt = dialog.inner_text(timeout=800) or ""
    except Exception:
        pass
    before_zero = "合计出资0万" in before_txt.replace(" ", "")
    info["hasRenzjTitle"] = "认缴出资" in before_txt

    # 点「增加」一条认缴记录
    added = False
    for near in ("认缴出资信息", "认缴出资", "认缴"):
        if _click_add_button(dialog, near_text=near):
            added = True
            info["openedVia"] = f"addBtn:{near}"
            break
    if not added:
        try:
            js_res = dialog.evaluate(
                """(root) => {
                  const norm = (s) => (s || '').replace(/\\s+/g,'').replace(/\\u00a0/g,'');
                  const isAdd = (t) => /^(?:\\+)?增加(?:>>|»|>{2})?$/.test(t);
                  const body = root.querySelector('.el-dialog__body') || root;
                  body.scrollTop = body.scrollHeight;
                  let titleEl = null;
                  for (const el of body.querySelectorAll('*')) {
                    const own = norm(Array.from(el.childNodes).filter(n => n.nodeType===3).map(n => n.textContent).join(''));
                    if (own === '认缴出资信息' || (own.includes('认缴出资信息') && own.length < 24)) {
                      titleEl = el; break;
                    }
                  }
                  if (titleEl) titleEl.scrollIntoView({block:'center'});
                  let rootEl = titleEl ? titleEl.parentElement : body;
                  for (let d = 0; d < 6 && rootEl; d++, rootEl = rootEl.parentElement) {
                    for (const el of rootEl.querySelectorAll('button,a,span,div')) {
                      const t = norm(el.innerText || el.textContent || '');
                      const r = el.getBoundingClientRect();
                      if (!isAdd(t) || r.width < 14 || r.height < 10 || r.width > 240) continue;
                      el.scrollIntoView({block:'center'});
                      el.click();
                      return {ok:true, t, y:r.y};
                    }
                  }
                  // 兜底：弹窗内最靠下的增加
                  const cands = [];
                  for (const el of body.querySelectorAll('button,a,span,div')) {
                    const t = norm(el.innerText || '');
                    const r = el.getBoundingClientRect();
                    if (!isAdd(t) || r.width < 14 || r.height < 10 || r.width > 240) continue;
                    cands.push({el, t, y:r.y});
                  }
                  cands.sort((a,b) => b.y - a.y);
                  if (cands.length) {
                    cands[0].el.scrollIntoView({block:'center'});
                    cands[0].el.click();
                    return {ok:true, t:cands[0].t, y:cands[0].y, via:'lowest'};
                  }
                  return {ok:false};
                }"""
            )
            info["addJs"] = js_res
            if isinstance(js_res, dict) and js_res.get("ok"):
                added = True
                info["openedVia"] = "js-add"
        except Exception as e:
            info["addError"] = str(e)
    if not added:
        info["error"] = "未点到认缴「增加」"
        _shot(page, cfg["case_id"], "capital-subscription-no-add")
        return info

    page.wait_for_timeout(800)
    _shot(page, cfg["case_id"], "capital-subscription-dialog")

    form = dialog
    info["formVia"] = "shareholder-dialog"
    info["opened"] = True

    # 等待增加后出现认缴输入框
    try:
        form.evaluate(
            """(el) => {
              const body = el.querySelector('.el-dialog__body') || el;
              body.scrollTop = body.scrollHeight;
              const hit = Array.from(body.querySelectorAll('*')).find(n => {
                const t = (n.textContent || '').replace(/\\s+/g,'');
                return t.includes('认缴出资信息') && t.length < 40;
              });
              if (hit) hit.scrollIntoView({block:'center'});
            }"""
        )
    except Exception:
        pass

    for _ in range(12):
        try:
            n = form.locator(
                'input[placeholder*="认缴开始"], input[placeholder*="认缴出资时间"], '
                '.el-date-editor input'
            ).count()
            info["waitPhCount"] = n
            if n >= 2:
                break
        except Exception:
            pass
        page.wait_for_timeout(350)

    probe: dict[str, Any] = {}
    try:
        probe = form.evaluate(
            """(root) => {
              const body = root.querySelector('.el-dialog__body') || root;
              const ph = (sel) => Array.from(body.querySelectorAll(sel)).map(e => e.placeholder||'');
              return {
                ok: true,
                allInputs: body.querySelectorAll('input').length,
                allDates: body.querySelectorAll('.el-date-editor').length,
                allSelects: body.querySelectorAll('.el-select').length,
                phStart: ph('input[placeholder*="认缴开始"]').length,
                phEnd: ph('input[placeholder*="认缴出资时间"]').length,
                phAmount: ph('input[placeholder*="认缴出资金额"], input[placeholder*="金额"]').length,
                datePh: ph('.el-date-editor input').slice(0,6),
                inputPh: Array.from(body.querySelectorAll('input')).slice(0,12).map(e => e.placeholder||'')
              };
            }"""
        ) or {}
        info["probe"] = probe
    except Exception as e:
        info["probeError"] = str(e)

    # 0) 优先按 placeholder 填（增加一条后出现的控件）
    start_ok = False
    end_ok = False
    method_ok = False
    amount_ok = False
    try:
        start_inp = form.locator('input[placeholder*="认缴开始时间"]').first
        if _visible(start_inp, timeout=800):
            start_inp.click(timeout=2000)
            start_inp.fill(start_s)
            start_inp.press("Enter")
            start_ok = True
            info["startVia"] = "placeholder"
    except Exception:
        pass
    try:
        end_inp = form.locator('input[placeholder*="认缴出资时间"]').first
        if not _visible(end_inp, timeout=400):
            end_inp = form.locator('input[placeholder*="认缴结束"]').first
        if _visible(end_inp, timeout=600):
            end_inp.click(timeout=2000)
            end_inp.fill(end_s)
            end_inp.press("Enter")
            end_ok = True
            info["endVia"] = "placeholder"
    except Exception:
        pass

    # 1) 表单项标签兜底
    if not start_ok:
        start_ok = _pick_date(page, form, "认缴开始时间", today)
    if not end_ok:
        end_ok = _pick_date(page, form, "认缴出资时间", end_d)
    if not end_ok:
        end_ok = _pick_date(page, form, "认缴结束时间", end_d)
    # 出资方式=货币（必须在金额前）
    method_ok = _select_dropdown(form, "认缴出资方式", cfg["method"])
    if not method_ok:
        try:
            lab = form.get_by_text("认缴出资方式", exact=False).first
            if _visible(lab, timeout=500):
                lab.scroll_into_view_if_needed(timeout=1500)
                for xpath in (
                    "xpath=ancestor::div[contains(@class,'el-form-item')][1]//div[contains(@class,'el-select')]",
                    "xpath=following::div[contains(@class,'el-select')][1]",
                ):
                    sel = lab.locator(xpath).first
                    if not _visible(sel, timeout=400):
                        continue
                    sel.click(timeout=2500)
                    page.wait_for_timeout(400)
                    for root in _all_scopes(page):
                        opt = root.locator(
                            ".el-select-dropdown:visible li, .el-popper:visible li"
                        ).filter(has_text=re.compile(r"货币")).first
                        if _visible(opt, timeout=500):
                            opt.click(timeout=2500)
                            method_ok = True
                            info["methodVia"] = "label-xpath"
                            break
                    if method_ok:
                        break
                    page.keyboard.press("Escape")
            info["methodPicked"] = method_ok
        except Exception as e:
            info["methodError"] = str(e)

    # 认缴出资金额：必须写进控件并校验 value（不能假阳性）
    amt_info = _fill_amount_verified(page, form, cfg["amount"])
    info["amountDetail"] = amt_info
    amount_ok = bool(amt_info.get("ok"))
    if amount_ok:
        info["amountVia"] = amt_info.get("via")

    # 日期兜底（标题下方日期框）
    title_y = 0.0
    try:
        title = form.get_by_text("认缴出资信息", exact=False).first
        if _visible(title, timeout=400):
            box = title.bounding_box()
            if box:
                title_y = float(box["y"])
    except Exception:
        pass
    if not (start_ok and end_ok):
        try:
            date_inps = form.locator(".el-date-editor input")
            date_els = []
            for i in range(min(date_inps.count(), 8)):
                el = date_inps.nth(i)
                if not _visible(el, timeout=200):
                    continue
                b = el.bounding_box()
                if not b:
                    continue
                if title_y and b["y"] + 2 < title_y:
                    continue
                date_els.append(el)
            if not start_ok and len(date_els) >= 1:
                date_els[0].click(timeout=2000)
                date_els[0].fill(start_s)
                date_els[0].press("Enter")
                start_ok = True
            if not end_ok and len(date_els) >= 2:
                date_els[1].click(timeout=2000)
                date_els[1].fill(end_s)
                date_els[1].press("Enter")
                end_ok = True
            info["dateEls"] = len(date_els)
        except Exception as e:
            info["dateFillError"] = str(e)

    # 金额再强填一次（方式选完后控件有时才可写）
    if not amount_ok:
        amt_info = _fill_amount_verified(page, form, cfg["amount"])
        info["amountDetail2"] = amt_info
        amount_ok = bool(amt_info.get("ok"))
        if amount_ok:
            info["amountVia"] = amt_info.get("via")

    info["start"] = start_ok
    info["end"] = end_ok
    info["method"] = method_ok
    info["amount"] = amount_ok

    # 独立认缴弹层才点其确定；股东窗底部确定留给后面
    if sub_dlg is not None and form is sub_dlg:
        try:
            btn = sub_dlg.locator(
                ".el-dialog__footer button.el-button--primary, button.el-button--primary"
            ).filter(has_text="确定").first
            if _visible(btn, timeout=600):
                btn.click(force=True, timeout=3000)
                info["subConfirm"] = True
                page.wait_for_timeout(800)
        except Exception:
            info["subConfirm"] = False

    try:
        after_txt = dialog.inner_text(timeout=800) or ""
        info["totalChanged"] = before_zero and ("合计出资0万" not in after_txt.replace(" ", ""))
        info["hasAmount100"] = "100" in after_txt and "认缴" in after_txt
    except Exception:
        pass

    # 金额未真正写入则不算完成
    info["ok"] = bool(start_ok and end_ok and method_ok and amount_ok)
    if not info["ok"]:
        info["error"] = "认缴字段未填全：" + ",".join(
            k for k, v in (("开始", start_ok), ("结束", end_ok), ("方式", method_ok), ("金额", amount_ok)) if not v
        )
    return info



def run_capital_flow(case_id: str, page: Page, payload: dict[str, Any]) -> dict[str, Any]:
    store.set_status(
        case_id,
        statuses.FILLING_ESTABLISHMENT,
        customer_message="正在填写出资情况…",
    )
    cfg = _defaults(payload)
    cfg["case_id"] = case_id
    result: dict[str, Any] = {"ok": False, "cfg": {k: v for k, v in cfg.items() if k != "case_id"}}

    # 等待出资情况页
    frame = _frame_stockholder(page)
    if frame is None:
        # 有时仍停在基本信息，再点一次下一步
        _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(1500)
        frame = _frame_stockholder(page)
    if frame is None:
        result["error"] = "未进入出资情况页"
        _shot(page, case_id, "capital-page-missing")
        _dump(case_id, "fill-capital-result.json", result)
        return result
    result["pageOk"] = True
    _shot(page, case_id, "capital-page")

    # 1) 注册资本
    cap_ok = _fill_by_label(frame, "注册资本", cfg["registered_capital"])
    if not cap_ok:
        try:
            inp = frame.locator("input").near(frame.get_by_text("注册资本", exact=False)).first
            if _visible(inp):
                inp.click()
                inp.fill(cfg["registered_capital"])
                cap_ok = True
        except Exception:
            pass
    result["registeredCapital"] = cap_ok
    page.wait_for_timeout(400)

    # 2) 股东区「+ 增加 >>」——逐个可见按钮点，直到出现出资者类型弹窗
    def _shareholder_dialog():
        for hint in ("出资者类型", "身份证明材料", "新增"):
            d, _ = _find_dialog(page, hint)
            if d is not None:
                return d
        for scope in (frame, *_all_scopes(page)):
            try:
                d = scope.locator(".el-dialog").filter(
                    has_text=re.compile(r"出资者类型|身份证明材料")
                ).first
                if _visible(d, timeout=400):
                    return d
            except Exception:
                continue
        return None

    def _click_shareholder_add() -> dict[str, Any]:
        info: dict[str, Any] = {"clicked": False}
        # A) 精确文案（含全角箭头）
        for txt in ("+ 增加 >>", "+ 增加 »", "增加 >>", "增加 »", "+ 增加", "增加"):
            try:
                btn = frame.get_by_text(txt, exact=True).first
                if _visible(btn, timeout=400):
                    btn.click(force=True, timeout=4000)
                    info.update({"clicked": True, "via": "text", "txt": txt})
                    return info
            except Exception:
                continue
        # B) role=button
        try:
            btn = frame.get_by_role("button", name=re.compile(r"增加")).first
            if _visible(btn, timeout=400):
                btn.click(force=True, timeout=4000)
                info.update({"clicked": True, "via": "role"})
                return info
        except Exception:
            pass
        # C) 所有含「增加」的小按钮，优先靠近「暂无数据/认缴合计」
        try:
            cands = frame.locator("button, a, span, div.el-button")
            n = min(cands.count(), 40)
            ranked: list[tuple[int, int]] = []
            for i in range(n):
                el = cands.nth(i)
                try:
                    if not el.is_visible(timeout=200):
                        continue
                    t = (el.inner_text(timeout=200) or "").replace("\n", "").strip()
                    tn = re.sub(r"\s+", "", t)
                    if "增加" not in tn or len(tn) > 12:
                        continue
                    box = el.bounding_box()
                    if not box or box["width"] > 240 or box["height"] > 60:
                        continue
                    score = 0
                    if ">>" in t or "»" in t or "+" in t:
                        score += 5
                    # 用页面位置：股东区大约在中上部偏右
                    if box["x"] > 600:
                        score += 3
                    ranked.append((score, i))
                except Exception:
                    continue
            ranked.sort(reverse=True)
            info["candidates"] = len(ranked)
            for score, i in ranked[:6]:
                el = cands.nth(i)
                try:
                    el.scroll_into_view_if_needed(timeout=1000)
                    el.click(force=True, timeout=4000)
                    info.update({"clicked": True, "via": "scan", "score": score, "idx": i})
                    return info
                except Exception:
                    try:
                        box = el.bounding_box()
                        if box:
                            page.mouse.click(
                                box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                            )
                            info.update({"clicked": True, "via": "scan-mouse", "idx": i})
                            return info
                    except Exception:
                        continue
        except Exception as e:
            info["scanError"] = str(e)
        # D) JS 在 frame 文档内找并 click
        try:
            js = frame.evaluate(
                """() => {
                  const norm = (s) => (s || '').replace(/\\s+/g,'').replace(/\\u00a0/g,'');
                  const nodes = Array.from(document.querySelectorAll('button,a,span,div'));
                  const hits = [];
                  for (const el of nodes) {
                    const t = norm(el.innerText || el.textContent || '');
                    const r = el.getBoundingClientRect();
                    if (!t.includes('增加') || t.length > 10) continue;
                    if (r.width < 20 || r.height < 12 || r.width > 220 || r.top < 60) continue;
                    let score = r.x;
                    if (t.includes('>>') || t.includes('»') || t.includes('+')) score += 1000;
                    hits.push({el, score, title: (el.innerText||'').trim()});
                  }
                  hits.sort((a,b) => b.score - a.score);
                  if (!hits.length) return {ok:false, n:0};
                  const el = hits[0].el;
                  el.scrollIntoView({block:'center'});
                  el.click();
                  return {ok:true, title: hits[0].title, n: hits.length};
                }"""
            )
            info["js"] = js
            if isinstance(js, dict) and js.get("ok"):
                info["clicked"] = True
                info["via"] = "js"
        except Exception as e:
            info["jsError"] = str(e)
        return info

    for attempt in range(8):
        click_info = _click_shareholder_add()
        result[f"addClick{attempt}"] = click_info
        page.wait_for_timeout(1200)
        dialog = _shareholder_dialog()
        if dialog is not None:
            result["dialogOpened"] = True
            break
        # 关掉可能误开的无关层
        _dismiss_switch_tip(page)
    _shot(page, case_id, "capital-shareholder-dialog")
    dialog = _shareholder_dialog()
    if dialog is None:
        try:
            dialog = frame.locator(".el-dialog").filter(has_text="自然人").first
            if not _visible(dialog, timeout=600):
                dialog = None
        except Exception:
            dialog = None
    if dialog is None:
        result["error"] = "未打开股东弹窗"
        result["dialogOpened"] = False
        _shot(page, case_id, "capital-add-missing")
        _dump(case_id, "fill-capital-result.json", result)
        return result
    result["dialogOpened"] = True

    # 3) 勾选自然人 → 关切换提示 → 确认仍勾选 → 复用人员信息
    result["naturalChecked"] = _check_natural_person(page, dialog)
    page.wait_for_timeout(400)
    result["switchTipDismissed"] = _dismiss_switch_tip(page)
    page.wait_for_timeout(500)
    # 提示关闭后勾选可能被清掉，再勾一次
    dialog, _ = _find_dialog(page, "新增")
    if dialog is None:
        dialog, _ = _find_dialog(page, "出资者类型")
    if dialog is None:
        dialog, _ = _find_dialog(page, "自然人")
    if dialog is not None and not _natural_checked(dialog):
        result["naturalRecheck"] = _check_natural_person(page, dialog)
        page.wait_for_timeout(400)
        result["switchTipDismissed2"] = _dismiss_switch_tip(page)
        page.wait_for_timeout(500)
    result["naturalStillChecked"] = _natural_checked(dialog) if dialog is not None else False
    _shot(page, case_id, "capital-after-natural")

    # 复用人员信息（下拉选吴文彬等）
    reused = False
    end_reuse = time.time() + 12
    while time.time() < end_reuse:
        if _reuse_first_person(page, host_dialog=dialog, person_name=cfg.get("name") or ""):
            reused = True
            break
        page.wait_for_timeout(500)
    result["reusedPerson"] = reused
    page.wait_for_timeout(1000)
    _dismiss_switch_tip(page)
    _shot(page, case_id, "capital-after-reuse")

    # 弹窗可能刷新，重新定位「新增」股东窗
    dialog, _ = _find_dialog(page, "投资人类型信息")
    if dialog is None:
        dialog, _ = _find_dialog(page, "认缴出资信息")
    if dialog is None:
        dialog, _ = _find_dialog(page, "新增")
    if dialog is None:
        dialog, _ = _find_dialog(page, "自然人")
    form = dialog or frame

    # 复用人员后身份信息已带出，不再上传身份证；未复用时才上传
    if reused:
        result["idUpload"] = {"skipped": True, "reason": "reusedPerson"}
    else:
        result["idUpload"] = _upload_id_in_dialog(form, page)
        page.wait_for_timeout(800)

    # 补齐姓名/证件/性别（纠错：防止姓名被写成 100）
    result["personBasics"] = _fill_person_basics(form, cfg, page)

    # 4) 依次填报
    result["investorType"] = _select_dropdown(form, "投资人类型", cfg["investor_type"])
    if not result["investorType"]:
        try:
            result["investorType"] = "自然人股东" in (form.inner_text(timeout=800) or "")
        except Exception:
            pass

    result["ethnicity"] = _select_dropdown(form, "民族", cfg["ethnicity"])
    if not result["ethnicity"]:
        try:
            result["ethnicity"] = "汉族" in (form.inner_text(timeout=800) or "")
        except Exception:
            pass

    result["hukou"] = _select_cascader(form, "户籍所属省市县", cfg["hukou"])
    if not result["hukou"]:
        result["hukou"] = _select_cascader(form, "户籍", cfg["hukou"])
    if not result["hukou"]:
        try:
            result["hukou"] = all(p in (form.inner_text(timeout=800) or "") for p in cfg["hukou"])
        except Exception:
            pass

    # 经济性质（截图中为必填）
    result["economicNature"] = _select_dropdown(form, "经济性质", "个体经营")
    if not result["economicNature"]:
        try:
            item = form.locator(".el-form-item").filter(has_text=re.compile(r"经济性质")).first
            if _visible(item):
                item.locator(".el-cascader, .el-select, input").first.click(timeout=2000)
                page.wait_for_timeout(400)
                for part in ("个体经营", "个人", "内资个人"):
                    for root in _all_scopes(page):
                        node = root.get_by_text(part, exact=False).first
                        if _visible(node, timeout=400):
                            node.click(timeout=2000)
                            page.wait_for_timeout(300)
                            break
                result["economicNature"] = True
        except Exception:
            pass

    result["address"] = _fill_by_label(form, "住所", cfg["address"])
    result["mobile"] = _fill_by_label(form, "移动电话", cfg["mobile"])
    if not result["mobile"]:
        result["mobile"] = _fill_by_label(form, "电话", cfg["mobile"])
    _shot(page, case_id, "capital-shareholder-filled")

    # 5) 认缴出资（先增加一条再填写）
    result["subscription"] = _fill_subscription(page, form, cfg)
    if not (result["subscription"] or {}).get("ok"):
        # 重新定位弹窗再试一次
        dialog2, _ = _find_dialog(page, "认缴出资信息")
        if dialog2 is not None:
            result["subscription"] = _fill_subscription(page, dialog2, cfg)
    _shot(page, case_id, "capital-after-subscription")

    # 6) 股东弹窗确定前：确保货币方式 + 金额已写入，再点确定
    _dismiss_switch_tip(page)
    try:
        dialog_pre, _ = _find_dialog(page, "认缴出资信息")
        if dialog_pre is None:
            dialog_pre, _ = _find_dialog(page, "投资人类型信息")
        if dialog_pre is None:
            dialog_pre, _ = _find_dialog(page, "新增")
        if dialog_pre is not None:
            # 方式=货币
            if not _select_dropdown(dialog_pre, "认缴出资方式", cfg["method"]):
                pass
            # 金额必须校验写入成功（确定前再写一遍）
            amt_chk = _fill_amount_verified(page, dialog_pre, cfg["amount"])
            result["amountBeforeConfirm"] = amt_chk
            if not amt_chk.get("ok"):
                page.wait_for_timeout(400)
                amt_chk = _fill_amount_verified(page, dialog_pre, cfg["amount"])
                result["amountBeforeConfirm2"] = amt_chk
            if not amt_chk.get("ok"):
                result["amountMissing"] = True
            # 币种
            if _select_dropdown(dialog_pre, "认缴出资币种", "人民币"):
                result["currency"] = True
            page.wait_for_timeout(400)
    except Exception as e:
        result["preConfirmError"] = str(e)

    dialog, _ = _find_dialog(page, "认缴出资信息")
    if dialog is None:
        dialog, _ = _find_dialog(page, "投资人类型信息")
    if dialog is None:
        dialog, _ = _find_dialog(page, "新增")
    if dialog is None:
        dialog, _ = _find_dialog(page, "出资者类型")
    confirmed = False
    confirm_info: dict[str, Any] = {}
    if dialog is not None:
        try:
            dialog.evaluate(
                """(el) => {
                  const b = el.querySelector('.el-dialog__body');
                  if (b) b.scrollTop = b.scrollHeight;
                  const f = el.querySelector('.el-dialog__footer');
                  if (f) f.scrollIntoView({block:'center'});
                }"""
            )
        except Exception:
            pass
        page.wait_for_timeout(400)
        for attempt in range(3):
            try:
                btn = dialog.locator(".el-dialog__footer button.el-button--primary").filter(
                    has_text=re.compile(r"确\s*定")
                ).first
                if not _visible(btn, timeout=400):
                    btn = dialog.get_by_role("button", name=re.compile(r"确\s*定")).last
                if not _visible(btn, timeout=400):
                    btn = dialog.locator("button.el-button--primary").filter(
                        has_text=re.compile(r"确\s*定")
                    ).last
                if _visible(btn, timeout=500):
                    btn.scroll_into_view_if_needed(timeout=1500)
                    btn.click(force=True, timeout=4000)
                    confirmed = True
                    confirm_info["via"] = f"btn-{attempt}"
                    break
            except Exception as e:
                confirm_info["btnErr"] = str(e)
            try:
                js = dialog.evaluate(
                    """(el) => {
                      const footer = el.querySelector('.el-dialog__footer') || el;
                      const btns = Array.from(footer.querySelectorAll('button, .el-button'));
                      for (const b of btns.reverse()) {
                        const t = (b.innerText || '').replace(/\\s+/g, '');
                        if (t !== '确定' && t !== '确认') continue;
                        b.scrollIntoView({block:'center'});
                        b.click();
                        return {ok:true, t};
                      }
                      return {ok:false, n: btns.length};
                    }"""
                )
                confirm_info["js"] = js
                if isinstance(js, dict) and js.get("ok"):
                    confirmed = True
                    confirm_info["via"] = f"js-{attempt}"
                    break
            except Exception as e:
                confirm_info["jsErr"] = str(e)
            page.wait_for_timeout(400)
        if not confirmed:
            confirmed = _click_text(dialog, ["确定", "确认"], exact=True)
            if confirmed:
                confirm_info["via"] = "click-text"
    result["confirmInfo"] = confirm_info
    page.wait_for_timeout(1000)
    _dismiss_switch_tip(page)

    def _dialog_open() -> Any:
        for hint in ("认缴出资信息", "投资人类型信息", "出资者类型"):
            d, _ = _find_dialog(page, hint)
            if d is not None and _visible(d, timeout=400):
                return d
        d, _ = _find_dialog(page, "新增")
        if d is not None and _visible(d, timeout=400):
            return d
        return None

    def _form_errors(dlg) -> list[str]:
        errs: list[str] = []
        try:
            nodes = dlg.locator(".el-form-item__error")
            for i in range(min(nodes.count(), 16)):
                t = re.sub(r"\s+", "", (nodes.nth(i).inner_text(timeout=200) or "").strip())
                if t and t not in errs:
                    errs.append(t[:60])
        except Exception:
            pass
        return errs

    def _restore_person(dlg) -> dict[str, Any]:
        """校验报姓名/投资人类型等被清空时，恢复自然人+复用+基础字段。"""
        info: dict[str, Any] = {}
        try:
            info["natural"] = _check_natural_person(page, dlg)
            page.wait_for_timeout(400)
            _dismiss_switch_tip(page)
            info["reused"] = _reuse_first_person(
                page, host_dialog=dlg, person_name=cfg.get("name") or ""
            )
            page.wait_for_timeout(600)
            _dismiss_switch_tip(page)
            info["basics"] = _fill_person_basics(dlg, cfg, page)
            info["investorType"] = _select_dropdown(dlg, "投资人类型", cfg["investor_type"])
            info["ethnicity"] = _select_dropdown(dlg, "民族", cfg["ethnicity"])
            info["hukou"] = _select_cascader(dlg, "户籍所属省市县", cfg["hukou"]) or _select_cascader(
                dlg, "户籍", cfg["hukou"]
            )
            info["economic"] = _select_dropdown(dlg, "经济性质", "个体经营")
            info["address"] = _fill_by_label(dlg, "住所", cfg["address"])
            info["mobile"] = _fill_by_label(dlg, "移动电话", cfg["mobile"]) or _fill_by_label(
                dlg, "电话", cfg["mobile"]
            )
            id_no = cfg.get("idNumber") or ""
            if len(id_no) >= 14:
                birth = f"{id_no[6:10]}-{id_no[10:12]}-{id_no[12:14]}"
                if _pick_date(page, dlg, "出生日期", date.fromisoformat(birth)):
                    info["birth"] = birth
            _select_dropdown(dlg, "认缴出资方式", cfg["method"])
            info["amount"] = _fill_amount_verified(page, dlg, cfg["amount"])
        except Exception as e:
            info["error"] = str(e)
        return info

    def _click_dialog_ok(dlg) -> bool:
        try:
            dlg.evaluate(
                """(el) => {
                  const b = el.querySelector('.el-dialog__body');
                  if (b) b.scrollTop = b.scrollHeight;
                  const f = el.querySelector('.el-dialog__footer');
                  if (f) f.scrollIntoView({block:'center'});
                }"""
            )
        except Exception:
            pass
        page.wait_for_timeout(250)
        try:
            btn = dlg.locator(".el-dialog__footer button.el-button--primary").filter(
                has_text=re.compile(r"确\s*定")
            ).first
            if _visible(btn, timeout=500):
                btn.click(force=True, timeout=4000)
                return True
        except Exception:
            pass
        try:
            js = dlg.evaluate(
                """(el) => {
                  const footer = el.querySelector('.el-dialog__footer') || el;
                  for (const b of Array.from(footer.querySelectorAll('button')).reverse()) {
                    const t = (b.innerText || '').replace(/\\s+/g, '');
                    if (t === '确定' || t === '确认') { b.click(); return true; }
                  }
                  return false;
                }"""
            )
            return bool(js)
        except Exception:
            return _click_text(dlg, ["确定", "确认"], exact=True)

    # 确定后若弹窗仍在：读校验 → 恢复人员/补金额 → 再点确定
    still = _dialog_open()
    result["dialogStillOpen"] = still is not None
    if still is not None:
        errs = _form_errors(still)
        result["formErrors"] = errs
        need_person = any(
            k in "".join(errs) for k in ("姓名", "证件", "投资人类型", "移动电话", "经济性质", "证照")
        ) or (not _natural_checked(still))
        if need_person:
            result["personRestore"] = _restore_person(still)
            page.wait_for_timeout(500)
        else:
            id_no = cfg.get("idNumber") or ""
            if len(id_no) >= 14:
                birth = f"{id_no[6:10]}-{id_no[10:12]}-{id_no[12:14]}"
                if _pick_date(page, still, "出生日期", date.fromisoformat(birth)):
                    result["birthFixed"] = birth
            try:
                _select_dropdown(still, "认缴出资币种", "人民币")
            except Exception:
                pass
            result["amountOnRetry"] = _fill_amount_verified(page, still, cfg["amount"])
        for retry in range(3):
            still = _dialog_open()
            if still is None:
                result["dialogStillOpen"] = False
                break
            _fill_amount_verified(page, still, cfg["amount"])
            if _click_dialog_ok(still):
                confirmed = True
                confirm_info["retry"] = retry
            page.wait_for_timeout(1000)
            _dismiss_switch_tip(page)
            still = _dialog_open()
            if still is None:
                result["dialogStillOpen"] = False
                break
            result["formErrors"] = _form_errors(still)
            if any(k in "".join(result["formErrors"]) for k in ("姓名", "投资人类型", "移动电话")):
                result["personRestore2"] = _restore_person(still)
        result["dialogStillOpen"] = _dialog_open() is not None

    if result.get("dialogStillOpen"):
        confirmed = False
        result["confirmNote"] = "弹窗未关闭，确定未生效"
    result["shareholderConfirm"] = confirmed
    result["confirmInfo"] = confirm_info
    _shot(page, case_id, "capital-after-confirm")

    frame2 = _frame_stockholder(page, timeout_ms=20000) or frame
    if result.get("dialogStillOpen"):
        d = _dialog_open()
        if d is not None:
            _fill_amount_verified(page, d, cfg["amount"])
            _click_dialog_ok(d)
            page.wait_for_timeout(1000)
            result["dialogStillOpen"] = _dialog_open() is not None

    try:
        body = _body(frame2).inner_text(timeout=1500) or ""
        result["hasShareholderRow"] = "暂无数据" not in body and (
            "自然人" in body or (cfg["name"] and cfg["name"] in body) or "100" in body
        )
    except Exception:
        result["hasShareholderRow"] = False

    next_ok = False
    if not result.get("dialogStillOpen"):
        for _ in range(4):
            _dismiss_switch_tip(page)
            try:
                frame2.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                try:
                    page.mouse.wheel(0, 1600)
                except Exception:
                    pass
            page.wait_for_timeout(350)
            next_ok = _click_text(frame2, ["下一步"], exact=True)
            if not next_ok:
                next_ok = _click_text_any(page, ["下一步"], exact=True)
            if not next_ok:
                try:
                    btn = frame2.get_by_role("button", name=re.compile(r"下一步")).first
                    if _visible(btn, timeout=400):
                        btn.click(force=True, timeout=4000)
                        next_ok = True
                except Exception:
                    pass
            if next_ok:
                break
            page.wait_for_timeout(500)
    else:
        result["nextSkippedReason"] = "股东弹窗未关闭"
    result["nextClicked"] = next_ok
    page.wait_for_timeout(1500)
    _shot(page, case_id, "capital-after-next")

    missing = []
    if not result.get("registeredCapital"):
        missing.append("注册资本")
    if not result.get("reusedPerson") and not result.get("naturalChecked"):
        missing.append("复用人员/自然人")
    if not (result.get("subscription") or {}).get("ok"):
        missing.append("认缴出资")
    if not result.get("shareholderConfirm") or result.get("dialogStillOpen"):
        missing.append("股东确定")
    if not result.get("nextClicked"):
        missing.append("下一步")
    result["missing"] = missing
    result["ok"] = not missing
    _dump(case_id, "fill-capital-result.json", result)
    return result
