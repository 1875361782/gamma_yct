"""人员信息 + 受益所有人备案（新增，不改核名/基本信息/出资情况代码）。

出资情况「下一步」之后：
1. 董事信息：设一名董事；监事信息：规模较小…不设监事
2. 添加董事 → 复用人员第一条 → 是否担任法定代表人=是 → 确定（不另填其它项）
3. 添加高级管理人员 → 复用第一条 → 职务=财务负责人 → 产生方式=董事聘任 → 确定
4. 添加联络员 → 复用第一条 → 代表登录公示系统=是 → 邮箱/住所 → 确定
5. 下一步 → 填报受益所有人信息
6. 自行填报 → 下一步
7. 存在符合下列条件之一的自然人 → 下一步
8. 填受益所有人（基本信息选吴文彬/证件期限/手机/地址/股权100%/形成日=当日）→ 下一步 → 确定
9. 已完成，进入下一步
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

DEFAULT_EMAIL = "1875361782@qq.com"
DEFAULT_ADDRESS = "贵州省安顺市西秀区鸡场乡鸡场散居户"
DEFAULT_MOBILE = "13984439335"
DEFAULT_ID_VALID = "2032-06-24"
DEFAULT_NAME = "吴文彬"
DEFAULT_HUKOU = ["贵州省", "安顺市", "西秀区"]
DEFAULT_ID_FRONT = Path(r"C:\Users\Lenovo\Desktop\身份证正面.jpg")
DEFAULT_ID_BACK = Path(r"C:\Users\Lenovo\Desktop\身份证反面.jpg")


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
    """优先 wujie 微前端内 body（与 capital.py 一致）。"""
    try:
        w = scope.locator("wujie-app body")
        if w.count() > 0:
            return w.first
    except Exception:
        pass
    return scope.locator("body").first


def _page_of(scope: Page | Frame) -> Page:
    return scope if isinstance(scope, Page) else scope.page


def _click_text(scope: Page | Frame, texts: list[str], *, exact: bool = False) -> bool:
    for t in texts:
        try:
            loc = scope.get_by_text(t, exact=exact).first
            if _visible(loc, timeout=600):
                loc.click(force=True, timeout=4000)
                return True
        except Exception:
            continue
    return False


def _click_text_any(page: Page, texts: list[str], *, exact: bool = False) -> bool:
    for scope in _all_scopes(page):
        if _click_text(scope, texts, exact=exact):
            return True
    return False


def _find_dialog(page: Page, *hints: str):
    for scope in _all_scopes(page):
        for sel in (
            ".el-message-box:visible",
            ".el-dialog:visible",
            ".el-overlay-dialog .el-dialog",
            "[role='dialog']",
        ):
            try:
                dialogs = scope.locator(sel)
                for i in range(min(dialogs.count(), 10)):
                    d = dialogs.nth(i)
                    if not _visible(d, timeout=300):
                        continue
                    text = d.inner_text(timeout=800) or ""
                    if all(h in text for h in hints):
                        return d, scope
            except Exception:
                continue
    return None, None


def _click_radio(scope: Page | Frame, label: str) -> bool:
    """点选含指定文案的 radio / label。"""
    try:
        lab = scope.locator("label.el-radio, label").filter(
            has_text=re.compile(re.escape(label[:12]) if len(label) > 12 else re.escape(label))
        ).first
        # 对长文案用 contains
        if not _visible(lab, timeout=400):
            lab = scope.get_by_text(label, exact=False).first
        if _visible(lab, timeout=600):
            lab.click(force=True, timeout=4000)
            return True
    except Exception:
        pass
    # 部分匹配：规模较小 / 不设监事
    keys = []
    if "不设监事" in label or "规模较小" in label:
        keys = ["不设监事", "规模较小"]
    for k in keys:
        try:
            lab = scope.locator("label").filter(has_text=re.compile(k)).first
            if _visible(lab, timeout=500):
                lab.click(force=True, timeout=4000)
                return True
        except Exception:
            continue
    return False


def _select_dropdown(scope: Page | Frame, label: str, option: str) -> bool:
    page = _page_of(scope)
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item, timeout=600):
        # 宽松：职务旁下拉
        try:
            lab = scope.get_by_text(label, exact=False).first
            if _visible(lab, timeout=400):
                item = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
                if not _visible(item, timeout=300):
                    item = lab.locator("xpath=../..")
        except Exception:
            return False
    if not _visible(item, timeout=400):
        return False
    try:
        trigger = item.locator(
            ".el-select .el-input__inner, .el-select, input.el-input__inner"
        ).first
        if not _visible(trigger, timeout=500):
            return False
        trigger.click(timeout=2500)
        page.wait_for_timeout(350)
        for root in _all_scopes(page):
            opt = root.locator(
                ".el-select-dropdown:visible li, .el-popper:visible li"
            ).filter(has_text=re.compile(rf"^\s*{re.escape(option)}\s*$")).first
            if not _visible(opt, timeout=400):
                opt = root.get_by_text(option, exact=True).first
            if _visible(opt, timeout=400):
                opt.click(timeout=2500)
                page.wait_for_timeout(300)
                return True
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def _select_cascader(scope: Page | Frame, label: str, parts: list[str]) -> bool:
    """户籍所属省市县等级联选择（与 capital 同逻辑）。"""
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item, timeout=600):
        item = scope.locator(".el-form-item").filter(has_text=re.compile(r"户籍")).first
    if not _visible(item, timeout=500):
        return False
    try:
        trigger = item.locator(
            ".el-cascader .el-input__inner, .el-cascader input, input.el-input__inner"
        ).first
        if not _visible(trigger, timeout=500):
            return False
        trigger.click(timeout=3000)
        page = _page_of(scope)
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


def _wait_ocr_idle(dialog, page: Page, timeout_ms: int = 25000) -> dict[str, Any]:
    """等待「正在识别证件信息中…」结束。"""
    info: dict[str, Any] = {"waited": False, "ok": False}
    end = time.time() + timeout_ms / 1000
    start = time.time()
    saw_busy = False
    while time.time() < end:
        try:
            txt = dialog.inner_text(timeout=600) or ""
        except Exception:
            txt = ""
        try:
            page_txt = page.locator("body").inner_text(timeout=400) or ""
        except Exception:
            page_txt = ""
        busy = ("正在识别" in txt) or ("正在识别证件信息" in page_txt)
        if busy:
            saw_busy = True
            info["waited"] = True
            page.wait_for_timeout(500)
            continue
        if "图片处理失败" in txt or "图片处理失败" in page_txt:
            info["failed"] = True
            info["ok"] = False
            return info
        if (
            "OCR信息识别成功" in txt
            or "OCR信息识别成功" in page_txt
            or "识别成功" in txt
        ):
            info["successToast"] = True
            info["ok"] = True
            page.wait_for_timeout(400)
            return info
        if saw_busy:
            info["ok"] = True
            page.wait_for_timeout(400)
            return info
        # 从未出现识别中：2s 后放行（复用人员通常已就绪）
        if time.time() - start >= 2.0:
            info["ok"] = True
            info["skip"] = "no-busy"
            return info
        page.wait_for_timeout(300)
    info["ok"] = True
    info["timeout"] = True
    return info


def _fix_id_upload(dialog, page: Page, *, force: bool = False) -> dict[str, Any]:
    """证件图处理失败时：删旧图并重新上传桌面身份证。force=True 强制重传。"""
    out: dict[str, Any] = {"attempted": False, "force": force}
    try:
        txt = dialog.inner_text(timeout=800) or ""
    except Exception:
        txt = ""
    failed = "图片处理失败" in txt
    out["failedBanner"] = failed
    if not force and not failed:
        try:
            imgs = dialog.locator(
                ".el-upload-list img, img[src^='blob'], img[src^='data:']"
            ).count()
        except Exception:
            imgs = 0
        if imgs >= 2:
            out["skip"] = "ok-preview"
            return out

    out["attempted"] = True
    # 点删除（多轮，避免删一个后列表变化）
    deleted = 0
    for _ in range(4):
        try:
            btn = dialog.locator("span, a, button, i").filter(
                has_text=re.compile(r"^删除$")
            ).first
            if not _visible(btn, timeout=300):
                # 悬停缩略图出现删除
                thumbs = dialog.locator(".el-upload-list__item, .el-upload-list li")
                if thumbs.count() == 0:
                    break
                thumbs.first.hover(timeout=800)
                btn = dialog.locator(
                    ".el-upload-list__item-delete, .el-icon-delete, [class*='delete']"
                ).first
            if _visible(btn, timeout=400):
                btn.click(force=True, timeout=1500)
                deleted += 1
                page.wait_for_timeout(400)
            else:
                break
        except Exception:
            break
    out["deleted"] = deleted

    front = DEFAULT_ID_FRONT if DEFAULT_ID_FRONT.exists() else None
    back = DEFAULT_ID_BACK if DEFAULT_ID_BACK.exists() else None
    out["frontPath"] = str(front) if front else None
    out["backPath"] = str(back) if back else None
    try:
        inputs = dialog.locator("input[type='file']")
        n = inputs.count()
        if n >= 1 and front is not None:
            inputs.nth(0).set_input_files(str(front.resolve()))
            out["front"] = True
            page.wait_for_timeout(1800)
        if n >= 2 and back is not None:
            inputs.nth(1).set_input_files(str(back.resolve()))
            out["back"] = True
            page.wait_for_timeout(1800)
        if out.get("front") or out.get("back"):
            out["ocr"] = _wait_ocr_idle(dialog, page, timeout_ms=30000)
    except Exception as e:
        out["error"] = str(e)
    return out


def _prepare_director_dialog(dlg, page: Page, cfg: dict[str, Any]) -> dict[str, Any]:
    """董事弹窗提交前必填项：法定代表人=是、户籍、证件图。"""
    info: dict[str, Any] = {}
    info["legalRep"] = _set_yes_no(dlg, "法定代表人", True) or _set_yes_no(
        dlg, "是否由董事担任", True
    )
    info["hukou"] = _select_cascader(dlg, "户籍所属省市县", cfg["hukou"]) or _select_cascader(
        dlg, "户籍", cfg["hukou"]
    )
    # 职务保持默认「董事（代表公司执行事务的董事）」即可
    # 产生方式若空则选股东委派
    try:
        txt = dlg.inner_text(timeout=600) or ""
        if "产生方式" in txt and "股东委派" not in txt and "董事聘任" not in txt:
            info["appoint"] = _select_dropdown(dlg, "产生方式", "股东委派")
    except Exception:
        pass
    return info


def _input_near_label(scope: Page | Frame, label: str):
    """Element/SAMR 通用：按可见文案找旁边的 input/textarea。"""
    loc = scope.get_by_text(label, exact=False).first
    if not _visible(loc, timeout=400):
        return None
    for xp in (
        "xpath=following::input[not(@type='hidden') and not(@type='checkbox') and not(@type='radio')][1]",
        "xpath=following::textarea[1]",
        "xpath=ancestor::*[self::div or self::tr or self::li or self::td][1]//input[not(@type='hidden') and not(@type='checkbox')][1]",
        "xpath=ancestor::*[self::div or self::tr][2]//input[not(@type='hidden')][1]",
        "xpath=ancestor::*[self::div or self::tr][2]//textarea[1]",
    ):
        try:
            inp = loc.locator(xp).first
            if _visible(inp, timeout=250):
                return inp
        except Exception:
            continue
    return None


def _fill_by_label(scope: Page | Frame, label: str, value: str) -> bool:
    if not value:
        return False
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    inp = None
    if _visible(item, timeout=400):
        inp = item.locator(
            "input.el-input__inner, textarea.el-textarea__inner, input:not([type='hidden']), textarea"
        ).first
        if not _visible(inp, timeout=300):
            inp = None
    if inp is None:
        inp = _input_near_label(scope, label)
    if inp is None or not _visible(inp, timeout=300):
        return False
    try:
        inp.click(timeout=2000)
        inp.fill("")
        inp.fill(str(value), timeout=4000)
        try:
            inp.press("Tab")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _fill_label_any(page: Page, labels: list[str], value: str) -> bool:
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        for scope in _all_scopes(pg):
            for lab in labels:
                if _fill_by_label(scope, lab, value):
                    return True
    return False


def _reuse_first_person(page: Page, host_dialog=None, person_name: str = "") -> bool:
    """复用人员信息：选列表第一条（优先匹配姓名）。"""
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
                    page.mouse.click(
                        box["x"] + min(box["width"] + 100, 260),
                        box["y"] + box["height"] / 2,
                    )
                    clicked = True
                    break
        except Exception:
            continue
    if not clicked:
        return False
    page.wait_for_timeout(700)
    for root in _all_scopes(page):
        try:
            opts = root.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item, "
                ".el-popper:visible li.el-select-dropdown__item, "
                ".el-select-dropdown:visible li"
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
    return False


def _click_dialog_ok(dialog, page: Page | None = None) -> bool:
    """点弹窗「确定」：优先真实鼠标坐标点击（人工能点中、自动化常 miss）。"""
    if page is None:
        try:
            page = dialog.page  # type: ignore[attr-defined]
        except Exception:
            page = None

    try:
        dialog.evaluate(
            """(el) => {
              const b = el.querySelector('.el-dialog__body');
              if (b) b.scrollTop = b.scrollHeight;
              const f = el.querySelector('.el-dialog__footer');
              if (f) f.scrollIntoView({block:'nearest', inline:'center'});
            }"""
        )
    except Exception:
        pass

    btn = None
    for sel in (
        ".el-dialog__footer button.el-button--primary",
        ".el-dialog__footer button.el-button--primary span",
        "button.el-button--primary",
    ):
        try:
            cand = dialog.locator(sel).filter(has_text=re.compile(r"确\s*定")).last
            if _visible(cand, timeout=500):
                btn = cand
                break
        except Exception:
            continue
    if btn is None:
        try:
            btn = dialog.get_by_role("button", name=re.compile(r"确\s*定")).last
            if not _visible(btn, timeout=400):
                btn = None
        except Exception:
            btn = None

    # 1) 真实鼠标点中心（最接近人工）
    if btn is not None and page is not None:
        try:
            btn.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        try:
            box = btn.bounding_box()
            if box and box.get("width", 0) > 8 and box.get("height", 0) > 8:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.wait_for_timeout(80)
                page.mouse.click(x, y)
                return True
        except Exception:
            pass
        try:
            btn.click(timeout=4000)
            return True
        except Exception:
            pass
        try:
            btn.click(force=True, timeout=3000)
            return True
        except Exception:
            pass

    # 2) JS：原生 MouseEvent
    try:
        js = dialog.evaluate(
            """(el) => {
              const footer = el.querySelector('.el-dialog__footer') || el;
              const buttons = Array.from(footer.querySelectorAll('button'));
              for (const b of buttons.reverse()) {
                const t = (b.innerText || '').replace(/\\s+/g, '');
                if (t !== '确定' && t !== '确认') continue;
                b.scrollIntoView({block:'nearest', inline:'center'});
                const r = b.getBoundingClientRect();
                const opts = {bubbles:true, cancelable:true, view:window,
                  clientX:r.left+r.width/2, clientY:r.top+r.height/2};
                b.dispatchEvent(new MouseEvent('mousedown', opts));
                b.dispatchEvent(new MouseEvent('mouseup', opts));
                b.dispatchEvent(new MouseEvent('click', opts));
                return true;
              }
              return false;
            }"""
        )
        if js:
            return True
    except Exception:
        pass

    if page is not None:
        return _click_text_any(page, ["确定"], exact=True)
    return _click_text(dialog, ["确定", "确认"], exact=True)


def _set_yes_no(dialog, label_hint: str, yes: bool = True) -> bool:
    """在含 label_hint 的表单项里点「是/否」，并尽量确认已选中。"""
    want = "是" if yes else "否"
    try:
        item = dialog.locator(".el-form-item").filter(
            has_text=re.compile(label_hint)
        ).first
        if not _visible(item, timeout=800):
            return False

        def _is_checked() -> bool:
            try:
                lab = item.locator("label.el-radio").filter(
                    has_text=re.compile(rf"^\s*{want}\s*$")
                ).first
                cls = lab.get_attribute("class") or ""
                if "is-checked" in cls:
                    return True
                inp = lab.locator("input[type=radio]").first
                if _visible(inp, timeout=200):
                    return bool(inp.is_checked())
            except Exception:
                pass
            return False

        # 1) Playwright 点 el-radio
        try:
            radio = item.locator("label.el-radio").filter(
                has_text=re.compile(rf"^\s*{want}\s*$")
            ).first
            if _visible(radio, timeout=500):
                radio.click(force=True, timeout=3000)
                if _is_checked():
                    return True
                radio.locator(".el-radio__inner").first.click(force=True, timeout=2000)
                if _is_checked():
                    return True
        except Exception:
            pass

        # 2) JS：触发 click + input/change 事件
        js = item.evaluate(
            """(el, want) => {
              const labels = Array.from(el.querySelectorAll('label.el-radio'));
              for (const lab of labels) {
                const t = (lab.innerText || '').replace(/\\s+/g, '');
                if (t !== want) continue;
                const input = lab.querySelector('input[type=radio]');
                const inner = lab.querySelector('.el-radio__inner');
                if (inner) inner.click();
                else lab.click();
                if (input) {
                  input.checked = true;
                  input.dispatchEvent(new Event('input', {bubbles:true}));
                  input.dispatchEvent(new Event('change', {bubbles:true}));
                }
                return true;
              }
              return false;
            }""",
            want,
        )
        if js and _is_checked():
            return True
        if js:
            return True
        lab = item.locator("label").filter(has_text=re.compile(rf"^\s*{want}\s*$")).first
        if _visible(lab, timeout=400):
            lab.click(force=True, timeout=3000)
            return True
    except Exception:
        pass
    return False


def _dialog_still_open(page: Page, *hints: str) -> bool:
    d, _ = _find_dialog(page, *hints)
    return d is not None


def _confirm_until_closed(page: Page, hints: tuple[str, ...], *, retries: int = 5) -> bool:
    """反复点确定直到指定弹窗消失。"""
    for i in range(retries):
        dlg, _ = _find_dialog(page, *hints)
        if dlg is None:
            return True
        if any("董事" in h for h in hints):
            _set_yes_no(dlg, "法定代表人", True)
            _wait_ocr_idle(dlg, page, timeout_ms=8000)
        hit = _click_dialog_ok(dlg, page)
        page.wait_for_timeout(1100)
        if not _dialog_still_open(page, *hints):
            return True
        if not hit or i >= 1:
            try:
                for scope in _all_scopes(page):
                    btn = scope.locator(
                        ".el-dialog__footer:visible button.el-button--primary"
                    ).filter(has_text=re.compile(r"确\s*定")).last
                    if not _visible(btn, timeout=400):
                        continue
                    box = btn.bounding_box()
                    if not box:
                        continue
                    page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    page.wait_for_timeout(1000)
                    if not _dialog_still_open(page, *hints):
                        return True
            except Exception:
                pass
    return not _dialog_still_open(page, *hints)


def _frame_personnel(page: Page, timeout_ms: int = 45000) -> Frame | Page | None:
    """定位人员信息页（web-reg-apply / chief-executive-officer / wujie-app）。

    注意：左侧菜单「人员信息」在外壳页；表单在 wujie 内，须读 wujie-app body。
    """
    markers = (
        "董事信息",
        "设一名董事",
        "添加董事",
        "监事信息",
        "不设监事",
        "高级管理人员",
        "添加高级管理人员",
        "登记联络员",
        "添加联络员",
    )

    def _scope_text(scope: Page | Frame) -> str:
        try:
            return _body(scope).inner_text(timeout=800) or ""
        except Exception:
            return ""

    def _marker_hits(text: str) -> int:
        return sum(1 for m in markers if m in text)

    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        page_url = page.url or ""
        url_hit = "chief-executive-officer" in page_url or "chief-executive" in page_url

        # 1) 优先 web-reg-apply（含 wujie-app body）
        for fr in page.frames:
            url = fr.url or ""
            name = fr.name or ""
            if "web-reg-apply" not in url and name != "web-reg-apply":
                continue
            body = _scope_text(fr)
            # 仅当普通 body 是 stub 且 wujie 也无内容时才跳过
            if "doesn't work properly without JavaScript" in body and _marker_hits(body) == 0:
                # 再试 wujie 专属定位 / get_by_text
                try:
                    if fr.locator("wujie-app").count() > 0:
                        wtxt = ""
                        try:
                            wtxt = fr.locator("wujie-app").inner_text(timeout=800) or ""
                        except Exception:
                            wtxt = ""
                        if _marker_hits(wtxt) >= 1 or ("监事" in wtxt and "高级管理人员" in wtxt):
                            return fr
                    if fr.get_by_text("添加高级管理人员", exact=False).count() > 0:
                        return fr
                    if fr.get_by_text("添加董事", exact=False).count() > 0:
                        return fr
                    if fr.get_by_text("登记联络员", exact=False).count() > 0:
                        return fr
                except Exception:
                    pass
                if url_hit:
                    # URL 已指向人员页：即便 stub，仍返回该业务帧供后续 get_by_text 使用
                    return fr
                continue
            hit = _marker_hits(body)
            if hit >= 2 or (url_hit and hit >= 1) or ("监事" in body and "高级管理人员" in body):
                return fr
            if url_hit and ("监事" in body or "董事" in body or "联络员" in body):
                return fr

        # 2) 主页面 / 其他 frame：get_by_text（可穿透部分 shadow）
        for fr in _all_scopes(page):
            body = _scope_text(fr)
            hit = _marker_hits(body)
            if hit >= 2 or ("监事" in body and "高级管理人员" in body):
                return fr
            try:
                if fr.get_by_text("添加高级管理人员", exact=False).count() > 0:
                    return fr
                if fr.get_by_text("添加董事", exact=False).count() > 0:
                    return fr
                if fr.get_by_text("设一名董事", exact=False).count() > 0:
                    return fr
            except Exception:
                pass

        # 3) URL 已到人员路由：用主 page 兜底（截图证明内容可见）
        if url_hit:
            try:
                if page.get_by_text("添加高级管理人员", exact=False).count() > 0:
                    return page
                if page.get_by_text("登记联络员", exact=False).count() > 0:
                    return page
                if page.locator("wujie-app").count() > 0:
                    wtxt = page.locator("wujie-app").inner_text(timeout=800) or ""
                    if _marker_hits(wtxt) >= 1 or "高级管理人员" in wtxt:
                        return page
            except Exception:
                pass
            page.wait_for_timeout(500)
            continue
        page.wait_for_timeout(400)
    return None


def _birth_from_id(id_no: str) -> str:
    s = (id_no or "").strip().upper()
    if len(s) >= 14 and s[6:14].isdigit():
        return f"{s[6:10]}-{s[10:12]}-{s[12:14]}"
    return ""


def _defaults(payload: dict[str, Any]) -> dict[str, Any]:
    inv = (payload.get("investors") or [{}])[0]
    lr = ((payload.get("personnel") or {}).get("legalRepresentative") or {})
    id_no = (inv.get("idNumber") or lr.get("idNumber") or "").strip()
    return {
        "name": (inv.get("name") or lr.get("name") or DEFAULT_NAME).strip(),
        "mobile": (inv.get("mobile") or lr.get("mobile") or DEFAULT_MOBILE).strip(),
        "address": (inv.get("address") or DEFAULT_ADDRESS).strip(),
        "email": DEFAULT_EMAIL,
        "id_valid": DEFAULT_ID_VALID,
        "id_number": id_no,
        "birth": _birth_from_id(id_no) or "2002-10-31",
        "hukou": list(DEFAULT_HUKOU),
    }


def _pick_date_input(page: Page, scope, label: str, value: str) -> bool:
    """填日期：优先 label 旁 input，值为 YYYY-MM-DD。"""
    inp = None
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if _visible(item, timeout=400):
        inp = item.locator(".el-date-editor input, input").first
        if not _visible(inp, timeout=300):
            inp = None
    if inp is None:
        inp = _input_near_label(scope, label)
    if inp is None or not _visible(inp, timeout=300):
        return False
    try:
        inp.click(timeout=2000)
        inp.fill("")
        inp.fill(value)
        inp.press("Enter")
        page.wait_for_timeout(200)
        return True
    except Exception:
        return False


def _pick_date_any(page: Page, labels: list[str], value: str) -> bool:
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        for scope in _all_scopes(pg):
            for lab in labels:
                if _pick_date_input(pg, scope, lab, value):
                    return True
    return False


def _click_visible_dropdown_option(page: Page, text: str) -> bool:
    """只点当前可见下拉面板里的选项。"""
    for sel in (
        ".layui-form-select dl dd",
        ".layui-anim-upbit dd",
        ".combo-panel .combobox-item",
        ".el-select-dropdown:visible li",
        ".el-popper:visible li.el-select-dropdown__item",
        ".el-cascader-menu:visible .el-cascader-node",
        ".ant-select-dropdown:visible .ant-select-item",
    ):
        try:
            opt = page.locator(sel).filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")).first
            if _visible(opt, timeout=500):
                opt.click(timeout=2500)
                page.wait_for_timeout(300)
                return True
        except Exception:
            continue
    return False


def _el_selects(scope):
    return scope.locator(
        ".el-select:visible, .ant-select:visible, .layui-form-select:visible, .combo:visible"
    )


def _open_nth_select(scope, page: Page, n: int) -> bool:
    try:
        sels = _el_selects(scope)
        if sels.count() <= n:
            return False
        s = sels.nth(n)
        if not _visible(s, timeout=400):
            return False
        s.scroll_into_view_if_needed(timeout=1500)
        s.click(timeout=2500)
        page.wait_for_timeout(400)
        return True
    except Exception:
        return False


def _click_option_any(page: Page, text: str) -> bool:
    """只在当前页点下拉选项，避免点到其它标签页里的同名文案。"""
    if _click_visible_dropdown_option(page, text):
        return True
    for root in _all_scopes(page):
        try:
            opt = root.locator(
                ".el-select-dropdown:visible li, .el-cascader-menu:visible .el-cascader-node, "
                ".el-popper:visible li, .ant-select-dropdown:visible .ant-select-item"
            ).filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")).first
            if not _visible(opt, timeout=300):
                opt = root.get_by_text(text, exact=True).first
            if _visible(opt, timeout=300):
                opt.click(timeout=2500)
                page.wait_for_timeout(250)
                return True
        except Exception:
            continue
    return False


def _open_select_near_label(scope: Page | Frame, label: str, *, exact: bool = False) -> bool:
    loc = scope.get_by_text(label, exact=exact).first
    if not _visible(loc, timeout=400):
        loc = scope.get_by_text(label, exact=False).first
    if not _visible(loc, timeout=400):
        return False
    page = _page_of(scope)
    try:
        trig = loc.locator(
            "xpath=following::*[contains(@class,'el-select') or contains(@class,'el-cascader') or contains(@class,'ant-select')][1]"
        ).first
        if _visible(trig, timeout=300):
            trig.click(timeout=2500)
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass
    inp = _input_near_label(scope, label)
    if inp is not None:
        try:
            inp.click(timeout=2500)
            page.wait_for_timeout(300)
            return True
        except Exception:
            pass
    try:
        box = loc.bounding_box()
        if box:
            page.mouse.click(box["x"] + box["width"] + 90, box["y"] + box["height"] / 2)
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass
    return False


def _select_inline_option(
    page: Page, scope, label: str, option: str, *, exact_label: bool = False
) -> bool:
    """点标签右侧下拉再选选项（地址四个下拉共用一个大表单项时，不能用 first select）。"""
    if _open_select_near_label(scope, label, exact=exact_label):
        if _click_option_any(page, option):
            return True
    if _select_dropdown(scope, label, option):
        return True
    return False


def _select_cascader_addr(page: Page, scope, country: str, province: str, city: str, district: str) -> dict[str, Any]:
    info: dict[str, Any] = {}
    info["country"] = _select_dropdown(scope, "国家/地区", country) or _select_dropdown(
        scope, "国家", country
    )
    if not info["country"]:
        if _open_select_near_label(scope, "国家/地区") or _open_select_near_label(scope, "国家"):
            info["country"] = _click_option_any(page, country)
    page.wait_for_timeout(300)
    for lab in ("省、自治区", "省", "自治区"):
        if _select_dropdown(scope, lab, province):
            info["province"] = True
            break
        if _open_select_near_label(scope, lab) and _click_option_any(page, province):
            info["province"] = True
            break
    page.wait_for_timeout(300)
    for lab in ("市",):
        if _select_dropdown(scope, lab, city):
            info["city"] = True
            break
        if _open_select_near_label(scope, lab) and _click_option_any(page, city):
            info["city"] = True
            break
    page.wait_for_timeout(300)
    for lab in ("县（区）", "县", "区"):
        if _select_dropdown(scope, lab, district):
            info["district"] = True
            break
        if _open_select_near_label(scope, lab) and _click_option_any(page, district):
            info["district"] = True
            break
    return info


def _all_pages(page: Page) -> list[Page]:
    try:
        ctx = page.context
        return list(ctx.pages)
    except Exception:
        return [page]


def _url_of(pg: Page) -> str:
    try:
        return pg.url or ""
    except Exception:
        return ""


def _is_portal_home(pg: Page) -> bool:
    url = _url_of(pg)
    if "portal_yct" in url and "web-yct-master" not in url and "web-reg-apply" not in url:
        return True
    try:
        loc = pg.get_by_text("请选择业务办理", exact=False).first
        if _visible(loc, timeout=300):
            return True
    except Exception:
        pass
    return False


def _page_has_text(page: Page, marker: str) -> bool:
    """只在给定 page 及其 frame 上找文案（不扫其它标签页）。"""
    for scope in _all_scopes(page):
        try:
            loc = scope.get_by_text(marker, exact=False).first
            if _visible(loc, timeout=300):
                return True
        except Exception:
            continue
    return False


def _switch_to_bo_page(page: Page) -> Page | None:
    """备案若开了新窗口，只切到真正含「自行填报」的页，绝不切门户首页。"""
    candidates: list[Page] = []
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        url = _url_of(pg)
        if _page_has_text(pg, "自行填报") or _page_has_text(pg, "承诺免报"):
            candidates.append(pg)
            continue
        if _page_has_text(pg, "受益所有人基本信息"):
            candidates.append(pg)
            continue
        if any(k in url.lower() for k in ("beneficial", "syzrr", "ubo", "benefic")):
            candidates.append(pg)
    if not candidates:
        # 原页已出现自行填报
        if not _is_portal_home(page) and _page_has_text(page, "自行填报"):
            return page
        return None
    # 优先 online.yct / web-reg-apply / 当前页
    for pg in candidates:
        url = _url_of(pg)
        if "online.yct" in url or "web-reg-apply" in url or "web-yct-master" in url:
            return pg
    return candidates[0]


def _close_stray_portal_tabs(keep: Page) -> int:
    """关掉误开的门户首页标签，避免后续操作切到「请选择业务办理」。"""
    closed = 0
    try:
        pages = list(keep.context.pages)
    except Exception:
        return 0
    for pg in pages:
        if pg is keep:
            continue
        if not _is_portal_home(pg):
            continue
        try:
            pg.close()
            closed += 1
        except Exception:
            continue
    return closed


def _page_alive(pg: Page | None) -> bool:
    if pg is None:
        return False
    try:
        return not pg.is_closed()
    except Exception:
        return False


def _safe_wait(pg: Page | None, ms: int) -> None:
    try:
        if _page_alive(pg):
            pg.wait_for_timeout(ms)
    except Exception:
        pass


def _find_reg_page(seed: Page | None) -> Page | None:
    """找回设立/人员信息登记页（排除备案页与门户首页）。"""
    pages: list[Page] = []
    if _page_alive(seed):
        try:
            pages = list(seed.context.pages)
        except Exception:
            pages = [seed]
    for pg in pages:
        if not _page_alive(pg) or _is_portal_home(pg):
            continue
        u = _url_of(pg)
        if "syr.samr.gov.cn" in u or "bow/apply" in u:
            continue
        if any(k in u for k in ("web-yct-master", "web-reg-apply", "chief-executive")):
            return pg
        if _bo_prompt_open(pg):
            return pg
        if _frame_personnel(pg, timeout_ms=600) is not None:
            return pg
    return seed if _page_alive(seed) else None


def _prefer_work_page(page: Page) -> Page:
    """优先受益所有人页；其次图5弹窗所在登记页；不要停在门户首页。"""
    bo = _switch_to_bo_page(page)
    if bo is not None:
        return bo
    if _bo_prompt_open(page):
        return page
    if not _is_portal_home(page) and _frame_personnel(page, timeout_ms=2500) is not None:
        return page
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        if _bo_prompt_open(pg):
            return pg
        if _frame_personnel(pg, timeout_ms=1500) is not None:
            return pg
    return page


def _section_quota_done(frame, hint: str) -> bool:
    try:
        txt = _body(frame).inner_text(timeout=800) or ""
    except Exception:
        txt = ""
    compact = txt.replace(" ", "").replace("\u00a0", "")
    if hint == "董事":
        if re.search(r"董事\s*[\(（]\s*[1-9]", txt):
            return True
        if "执行事务的董事" in txt or ("是否法定代表人" in txt and "修改" in txt):
            return True
        return False
    if hint == "高管":
        if "财务负责人：1" in compact or "财务负责人:1" in compact:
            return True
        if "财务负责人" in txt and "修改" in txt and "查看" in txt:
            return True
        return False
    if hint == "联络员":
        if re.search(r"登记联络员信息\s*[\(（]\s*[1-9]", txt):
            return True
        if "登记联络员" in txt and "修改" in txt and "查看" in txt:
            return True
        return False
    return False


def _bo_prompt_open(page: Page) -> bool:
    """人员信息页图5：提示弹窗含「填报受益所有人信息」。"""
    dlg, _ = _find_dialog(page, "提示")
    if dlg is None:
        dlg, _ = _find_dialog(page, "受益所有人")
    if dlg is None:
        return False
    try:
        t = dlg.inner_text(timeout=800) or ""
    except Exception:
        return False
    return "填报受益所有人信息" in t.replace(" ", "") or "未完成，填报" in t


def _input_has_value(scope, label: str) -> bool:
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item, timeout=400):
        item = scope.locator(".el-form-item").filter(has_text=re.compile(label)).first
    if not _visible(item, timeout=300):
        return False
    inp = item.locator("input, textarea").first
    try:
        v = (inp.input_value(timeout=400) or "").strip()
        return bool(v) and v not in ("请选择", "-请选择-", "-投资人信息-")
    except Exception:
        return False


def _pick_bo_investor(page: Page, form_scope, name: str) -> bool:
    """「受益所有人基本信息」下拉选择「吴文彬」。"""
    name = (name or DEFAULT_NAME).strip()
    if _select_inline_option(page, form_scope, "受益所有人基本信息", name):
        page.wait_for_timeout(1200)
        return True
    # 当前显示「-投资人信息-」时，点开再选姓名
    for hint in ("-投资人信息-", "投资人信息", "请选择"):
        loc = form_scope.get_by_text(hint, exact=False).first
        if not _visible(loc, timeout=300):
            continue
        try:
            loc.click(timeout=2500)
            page.wait_for_timeout(450)
            if _click_option_any(page, name):
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    triggers = []
    for scope in [form_scope, *_all_scopes(page)]:
        try:
            sels = scope.locator(".el-select")
            n = min(sels.count(), 10)
            for i in range(n):
                s = sels.nth(i)
                if not _visible(s, timeout=200):
                    continue
                t = s.inner_text(timeout=300) or ""
                try:
                    inn = s.locator("input").first
                    if _visible(inn, timeout=150):
                        t += inn.input_value(timeout=200) or ""
                except Exception:
                    pass
                if "投资人信息" in t or "请选择" in t or "受益所有人" in t or name in t:
                    triggers.append(s)
        except Exception:
            continue
    seen = set()
    for trig in triggers:
        try:
            key = id(trig)
            if key in seen:
                continue
            seen.add(key)
            trig.click(timeout=2500)
            page.wait_for_timeout(450)
            if _click_option_any(page, name):
                page.wait_for_timeout(1200)
                return True
            for root in _all_scopes(page):
                opt = root.get_by_text(name, exact=True).first
                if _visible(opt, timeout=400):
                    opt.click(timeout=2500)
                    page.wait_for_timeout(1200)
                    return True
            page.keyboard.press("Escape")
        except Exception:
            continue
    if _mouse_click_text(page, [name], exact=True) or _mouse_click_text(page, [name], exact=False):
        page.wait_for_timeout(1200)
        return True
    return False


def _click_dialog_footer_btn(page: Page, must_contain: list[str], *, exclude: list[str] | None = None) -> bool:
    """点弹窗 footer 里匹配文案的按钮（优先未完成填报，避开已完成/指南链接）。"""
    exclude = exclude or []
    dlg, _ = _find_dialog(page, "提示")
    if dlg is None:
        dlg, _ = _find_dialog(page, "受益所有人")
    if dlg is None:
        return False

    def _norm(s: str) -> str:
        return (s or "").replace(" ", "").replace("\n", "").replace("\u00a0", "")

    needles = [_norm(x) for x in must_contain]
    bans = [_norm(x) for x in exclude]
    target = None
    best_score = -1
    try:
        btns = dlg.locator(
            ".el-dialog__footer button, .el-message-box__btns button, "
            ".el-dialog__footer .el-button"
        )
        n = min(btns.count(), 10)
        if n == 0:
            btns = dlg.locator("button.el-button--primary, button.el-button")
            n = min(btns.count(), 10)
    except Exception:
        n = 0
    for i in range(n):
        b = btns.nth(i)
        try:
            t = _norm(b.inner_text(timeout=400) or "")
        except Exception:
            continue
        if any(ban and ban in t for ban in bans):
            continue
        score = -1
        for ni, needle in enumerate(needles):
            if needle and needle == t:
                score = 100 - ni
                break
            if needle and needle in t:
                score = 50 - ni
                break
        if score > best_score:
            best_score = score
            target = b
    if target is None or best_score < 0:
        return False
    try:
        target.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass
    # 真实 click（不要只 mouse 点 overlay）
    try:
        target.click(timeout=4000)
        return True
    except Exception:
        pass
    try:
        box = target.bounding_box()
        if box and box.get("width", 0) > 8:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return True
    except Exception:
        pass
    try:
        target.click(force=True, timeout=3000)
        return True
    except Exception:
        return False


def _click_bo_entry(page: Page) -> dict[str, Any]:
    """图5：点「未完成，填报受益所有人信息」/「填报受益所有人信息」，等到图6「自行填报」。"""
    info: dict[str, Any] = {"clicked": False, "ready": False}
    before_pages = len(_all_pages(page))
    for _ in range(20):
        dlg, _ = _find_dialog(page, "提示")
        if dlg is None:
            dlg, _ = _find_dialog(page, "受益所有人")
        if dlg is not None:
            break
        if _page_has_text(page, "自行填报"):
            info["ready"] = True
            info["alreadyOnPage"] = True
            info["page"] = page
            return info
        page.wait_for_timeout(400)

    def _ready_on(pg: Page) -> bool:
        if _is_portal_home(pg):
            return False
        return _page_has_text(pg, "自行填报") and _find_dialog(pg, "提示")[0] is None

    work = page
    for i in range(6):
        if _ready_on(work):
            info["ready"] = True
            info["clicked"] = True
            info["page"] = work
            info["workUrl"] = _url_of(work)
            return info
        bo_pg = _switch_to_bo_page(work)
        if bo_pg is not None and _ready_on(bo_pg):
            info["ready"] = True
            info["clicked"] = True
            info["page"] = bo_pg
            info["switchedUrl"] = _url_of(bo_pg)
            return info
        hit = _click_dialog_footer_btn(
            work,
            ["未完成，填报受益所有人信息", "填报受益所有人信息"],
            exclude=["已完成", "返回修改"],
        )
        info["clicked"] = info["clicked"] or hit
        info[f"try{i}"] = hit
        work.wait_for_timeout(2000)
        info["closedPortal"] = _close_stray_portal_tabs(work)
        info["pages"] = len(_all_pages(work))
        # 优先仍在原登记页找「自行填报」
        if _ready_on(work):
            info["ready"] = True
            info["page"] = work
            info["workUrl"] = _url_of(work)
            return info
        bo_pg = _switch_to_bo_page(work)
        if bo_pg is not None and not _is_portal_home(bo_pg):
            info["switchedUrl"] = _url_of(bo_pg)
            if _ready_on(bo_pg):
                info["ready"] = True
                info["page"] = bo_pg
                return info
            if _page_has_text(bo_pg, "自行填报"):
                info["ready"] = True
                info["page"] = bo_pg
                return info
    info["ready"] = _ready_on(work)
    info["pagesAfter"] = len(_all_pages(work))
    info["pagesBefore"] = before_pages
    picked = _switch_to_bo_page(work)
    if picked is None or _is_portal_home(picked):
        picked = work
    info["page"] = picked
    info["finalUrl"] = _url_of(picked)
    return info


def _mouse_click_text(page: Page, texts: list[str], *, exact: bool = False) -> bool:
    """用鼠标坐标点可见文案（弹窗按钮命中用）。"""
    for t in texts:
        for scope in _all_scopes(page):
            try:
                loc = scope.get_by_text(t, exact=exact).last
                if not _visible(loc, timeout=500):
                    continue
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                box = loc.bounding_box()
                if not box or box.get("width", 0) < 4:
                    # 可能是按钮内 span，点父级 button
                    btn = loc.locator("xpath=ancestor::button[1]")
                    if _visible(btn, timeout=300):
                        box = btn.bounding_box()
                        loc = btn
                if not box:
                    continue
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                return True
            except Exception:
                continue
    return False


def _wait_text_any(page: Page, markers: list[str], timeout_ms: int = 20000) -> bool:
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        for scope in _all_scopes(page):
            try:
                body = ""
                try:
                    body = _body(scope).inner_text(timeout=500) or ""
                except Exception:
                    body = ""
                if any(m in body for m in markers):
                    return True
                for m in markers:
                    if scope.get_by_text(m, exact=False).count() > 0:
                        return True
            except Exception:
                continue
        page.wait_for_timeout(400)
    return False


def _bo_not_obtained(page: Page) -> bool:
    try:
        for scope in _all_scopes(page):
            txt = ""
            try:
                txt = scope.locator("body").inner_text(timeout=500) or ""
            except Exception:
                continue
            if "未获取到受益所有人信息" in txt:
                return True
    except Exception:
        pass
    return False


def _js_click_eq(scope, text: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """(text) => {
                  const n = (text || '').replace(/\\s/g, '');
                  const vis = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const nodes = [...document.querySelectorAll(
                    'dd, li, .el-select-dropdown__item, .el-cascader-node, .ant-select-item, .combobox-item, span, div, label'
                  )].filter(vis);
                  for (const el of nodes) {
                    const t = (el.innerText || '').replace(/\\s/g, '').trim();
                    if (t === n || t === '-' + n + '-') { el.click(); return true; }
                  }
                  return false;
                }""",
                text,
            )
        )
    except Exception:
        return False


def _js_open_near(scope, label: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """(label) => {
                  const vis = (el) => !!(el && el.offsetParent !== null);
                  const n = (label || '').replace(/\\s/g, '');
                  let lab = null, best = 1e9;
                  for (const el of [...document.querySelectorAll('label, span, div, td, th')].filter(vis)) {
                    const t = (el.innerText || '').replace(/\\s/g, '');
                    if (t.includes(n) && t.length < best && t.length < 60) { lab = el; best = t.length; }
                  }
                  if (!lab) return false;
                  const box = lab.closest('.el-form-item, .ant-form-item, .form-group, tr, .el-row, .el-col')
                    || lab.parentElement;
                  const trig = box && box.querySelector(
                    '.layui-select-title, .layui-form-select, .combo-text, .combo-arrow, '
                    + '.el-select .el-input__inner, .el-select, .ant-select, .el-cascader, '
                    + 'select, input.el-input__inner'
                  );
                  if (trig) { trig.click(); return true; }
                  lab.click();
                  return true;
                }""",
                label,
            )
        )
    except Exception:
        return False


def _js_fill_near(scope, label: str, value: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """([label, value]) => {
                  const vis = (el) => !!(el && el.offsetParent !== null);
                  const n = (label || '').replace(/\\s/g, '');
                  let lab = null, best = 1e9;
                  for (const el of [...document.querySelectorAll('label, span, div, td')].filter(vis)) {
                    const t = (el.innerText || '').replace(/\\s/g, '');
                    if (t.includes(n) && t.length < best && t.length < 50) { lab = el; best = t.length; }
                  }
                  if (!lab) return false;
                  const box = lab.closest('.el-form-item, .ant-form-item, .form-group, tr, .el-row')
                    || lab.parentElement;
                  const inp = box && box.querySelector(
                    'input:not([type=checkbox]):not([type=radio]):not([type=hidden]), textarea'
                  );
                  if (!inp) return false;
                  inp.focus();
                  const proto = inp.tagName === 'TEXTAREA'
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(inp, value);
                  else inp.value = value;
                  inp.dispatchEvent(new Event('input', { bubbles: true }));
                  inp.dispatchEvent(new Event('change', { bubbles: true }));
                  inp.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                  try { inp.blur(); } catch (e) {}
                  return true;
                }""",
                [label, value],
            )
        )
    except Exception:
        return False


def _js_probe_date_inputs(scope) -> list[dict[str, Any]]:
    try:
        return (
            scope.evaluate(
                """() => {
                  const vis = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  return [...document.querySelectorAll('input')].filter(vis).map(inp => {
                    const box = inp.closest('tr, td, .layui-form-item, .el-form-item, div') || inp.parentElement;
                    const lab = ((box && box.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 60);
                    return {
                      value: inp.value || '',
                      name: inp.name || '',
                      id: inp.id || '',
                      cls: String(inp.className || '').slice(0, 50),
                      readonly: !!inp.readOnly,
                      lab,
                    };
                  }).filter(x => /日期|期限|出生|Wdate|date/i.test(x.lab + x.cls + x.name + x.id)
                    || /\\d{4}[\\/-]\\d{1,2}[\\/-]\\d{1,2}/.test(x.value));
                }"""
            )
            or []
        )
    except Exception:
        return []


def _js_commit_calendar(scope, year: int, month: int, day: int) -> dict[str, Any]:
    """在已打开的日历上切到指定年月日并点选。"""
    try:
        info = scope.evaluate(
            """([year, month, day]) => {
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 20 && r.height > 20 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              const roots = [];
              for (const sel of [
                '.WdateDiv', '#dpTitle', '.layui-laydate', '.el-picker-panel',
                '.datepicker', '.calendar'
              ]) {
                for (const el of document.querySelectorAll(sel)) if (vis(el)) roots.push(el);
              }
              if (!roots.length) {
                for (const el of document.querySelectorAll('div')) {
                  if (!vis(el)) continue;
                  const t = (el.innerText || '').replace(/\\s/g, '');
                  if (/\\d{4}年\\d{1,2}月/.test(t) && t.length < 500 && t.length > 20) roots.push(el);
                }
              }
              if (!roots.length) return { ok: false, reason: 'no-calendar' };
              const root = roots.sort((a,b) =>
                (b.getBoundingClientRect().width * b.getBoundingClientRect().height)
                - (a.getBoundingClientRect().width * a.getBoundingClientRect().height)
              )[0];

              const headerText = () => (root.innerText || '').replace(/\\s/g, '');
              const parseYM = () => {
                const m = headerText().match(/(\\d{4})年(\\d{1,2})月/);
                return m ? [parseInt(m[1],10), parseInt(m[2],10)] : null;
              };

              // 优先直接点「年」「月」数字切换（My97 / 常见中文日历）
              const clickExact = (txt) => {
                for (const el of root.querySelectorAll('a, span, button, td, div, input')) {
                  if (!vis(el) && el.tagName !== 'INPUT') continue;
                  const t = (el.innerText || el.value || '').replace(/\\s/g, '').trim();
                  if (t === txt) { el.click(); return true; }
                }
                return false;
              };

              let ym = parseYM();
              if (ym && (ym[0] !== year || ym[1] !== month)) {
                // 点年份标题再选年
                for (const el of root.querySelectorAll('a, span, div, input')) {
                  const t = (el.innerText || el.value || '').replace(/\\s/g, '');
                  if (t.includes(String(ym[0])) && t.length < 10) { el.click(); break; }
                }
                clickExact(String(year));
                ym = parseYM() || ym;
                for (const el of root.querySelectorAll('a, span, div, input')) {
                  const t = (el.innerText || el.value || '').replace(/\\s/g, '');
                  if ((t.includes(String(ym[1]) + '月') || t === String(ym[1])) && t.length < 8) {
                    el.click(); break;
                  }
                }
                clickExact(String(month) + '月') || clickExact(String(month));
              }

              // 左右箭头兜底（远年可能要翻很多月）
              for (let i = 0; i < 160; i++) {
                ym = parseYM();
                if (!ym) break;
                if (ym[0] === year && ym[1] === month) break;
                const target = year * 12 + month;
                const now = ym[0] * 12 + ym[1];
                const candidates = [...root.querySelectorAll('a, span, button, i, em, div')];
                let btn = null;
                if (target < now) {
                  btn = candidates.find(el => /prev|上月|＜|<|‹|NavImgll|NavImgl/i.test(
                    (el.className||'') + (el.id||'') + (el.title||'') + (el.innerText||'')
                  ));
                } else {
                  btn = candidates.find(el => /next|下月|＞|>|›|NavImgrr|NavImgr/i.test(
                    (el.className||'') + (el.id||'') + (el.title||'') + (el.innerText||'')
                  ));
                }
                if (!btn) break;
                btn.click();
              }

              ym = parseYM();
              if (!ym || ym[0] !== year || ym[1] !== month) {
                return { ok: false, reason: 'ym-mismatch', head: headerText().slice(0, 80), ym };
              }

              const dayRe = new RegExp('^\\\\s*' + day + '\\\\s*$');
              let clicked = null;
              for (const el of root.querySelectorAll('td, span, a, div')) {
                if (!vis(el)) continue;
                const t = (el.innerText || '').trim();
                if (!dayRe.test(t)) continue;
                const cls = String(el.className || '');
                if (/disabled|WotherDay|other-month|laydate-day-prev|laydate-day-next|prev|next|other/i.test(cls)) continue;
                el.click();
                clicked = t;
                break;
              }
              return { ok: !!clicked, day: clicked, head: headerText().slice(0, 60), ym: parseYM() };
            }""",
            [year, month, day],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _close_calendar_soft(page: Page, scope) -> None:
    """关掉日历：点标题区，不要 Esc（Esc 会清空刚填的日期）。"""
    try:
        for t in ("受益所有人基本信息", "受益所有权信息", "详细地址"):
            loc = scope.get_by_text(t, exact=False).first
            if _visible(loc, timeout=200):
                loc.click(timeout=1500, force=True)
                page.wait_for_timeout(200)
                return
    except Exception:
        pass
    try:
        page.mouse.click(20, 20)
        page.wait_for_timeout(150)
    except Exception:
        pass


def _parse_ymd(value: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", value or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _js_set_bo_date(scope, label: str, value: str) -> dict[str, Any]:
    """备案页日期：按标签找可见 input，写入（不按 Esc）。"""
    try:
        info = scope.evaluate(
            """([label, value]) => {
              const n = (label || '').replace(/\\s/g, '');
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              let lab = null, best = 1e9;
              for (const el of [...document.querySelectorAll('label, span, td, th, div')].filter(vis)) {
                const t = (el.innerText || '').replace(/\\s/g, '');
                if (!t.includes(n)) continue;
                if (t.length < best && t.length < 80) { lab = el; best = t.length; }
              }
              if (!lab) return { ok: false, reason: 'no-label' };
              const box = lab.closest('tr, .layui-form-item, .el-form-item, .form-group, li, div') || lab.parentElement;
              let inp = null;
              if (box) {
                const cands = [...box.querySelectorAll('input')].filter(el => {
                  const ty = (el.type || '').toLowerCase();
                  return ty !== 'hidden' && ty !== 'checkbox' && ty !== 'radio' && vis(el);
                });
                inp = cands[0] || null;
              }
              if (!inp) return { ok: false, reason: 'no-input' };
              try { inp.removeAttribute('readonly'); inp.readOnly = false; } catch (e) {}
              inp.focus();
              inp.click();
              const proto = HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(inp, value);
              else inp.value = value;
              inp.dispatchEvent(new Event('input', { bubbles: true }));
              inp.dispatchEvent(new Event('change', { bubbles: true }));
              try { if (window.jQuery) window.jQuery(inp).val(value).trigger('change'); } catch (e) {}
              return { ok: !!(inp.value), value: inp.value || '', name: inp.name || '', id: inp.id || '', cls: String(inp.className||'').slice(0,60) };
            }""",
            [label, value],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fill_one_bo_date(scope, page: Page, labels: list[str], value: str) -> dict[str, Any]:
    """优先键盘写入完整日期（如 2032/6/24）；日历仅在年月正确后点日。"""
    info: dict[str, Any] = {"want": value, "ok": False}
    ymd = _parse_ymd(value)
    if not ymd:
        return {**info, "error": "bad-date"}
    year, month, day = ymd
    typed = f"{year}/{month}/{day}"
    typed_alt = f"{year}-{month:02d}-{day:02d}"

    inp = None
    used_label = ""
    for lab in labels:
        inp = _input_near_label(scope, lab)
        if inp is not None and _visible(inp, timeout=300):
            used_label = lab
            break
    info["label"] = used_label or (labels[0] if labels else "")

    def _year_ok(v: str) -> bool:
        return bool(v) and str(year) in v and (
            str(month) in v or f"{month:02d}" in v
        ) and (str(day) in v or f"{day:02d}" in v)

    if inp is not None:
        try:
            inp.scroll_into_view_if_needed(timeout=1500)
            try:
                inp.evaluate("el => { el.removeAttribute('readonly'); el.readOnly = false; }")
            except Exception:
                pass
            inp.click(timeout=2000)
            page.wait_for_timeout(120)
            inp.fill("")
            inp.type(typed, delay=40)
            inp.press("Enter")
            page.wait_for_timeout(200)
            info["cal"] = _js_commit_calendar(scope, year, month, day)
            if not info["cal"].get("ok"):
                inp.fill("")
                inp.type(typed, delay=40)
                page.keyboard.press("Tab")
            _close_calendar_soft(page, scope)
            cur = (inp.input_value(timeout=500) or "").strip()
            if _year_ok(cur):
                info["value"] = cur
                info["ok"] = True
                info["via"] = "type"
                return info
            inp.click(timeout=1500)
            inp.fill("")
            inp.type(typed_alt, delay=40)
            inp.press("Enter")
            page.wait_for_timeout(200)
            info["cal2"] = _js_commit_calendar(scope, year, month, day)
            _close_calendar_soft(page, scope)
            cur = (inp.input_value(timeout=500) or "").strip()
            if _year_ok(cur):
                info["value"] = cur
                info["ok"] = True
                info["via"] = "type-alt"
                return info
            info["typedValue"] = cur
        except Exception as e:
            info["typeError"] = str(e)

    js = _js_set_bo_date(scope, used_label or labels[0], typed)
    info["js"] = js
    page.wait_for_timeout(200)
    info["cal3"] = _js_commit_calendar(scope, year, month, day)
    _close_calendar_soft(page, scope)
    cur = ""
    if inp is not None:
        try:
            cur = (inp.input_value(timeout=500) or "").strip()
        except Exception:
            cur = ""
    if not cur:
        cur = (js.get("value") if isinstance(js, dict) else "") or _field_value(
            scope, used_label or labels[0]
        )
    probe = _js_read_bo_dates(scope)
    info["probe"] = probe
    if not _year_ok(cur or ""):
        if "有效" in (used_label or labels[0]) or "期限" in (used_label or labels[0]):
            cur = probe.get("idValid") or cur
        else:
            cur = probe.get("ownershipDate") or cur
    info["value"] = cur
    info["ok"] = _year_ok(cur or "")
    info["via"] = "js-cal"
    return info


def _fill_bo_dates(scope, page: Page, id_valid: str, today_vals: list[str], result: dict[str, Any]) -> None:
    """证件有效期 + 形成日期；日历点选确认。"""
    _close_calendar_soft(page, scope)
    result["boDateInputs0"] = _js_probe_date_inputs(scope)
    result["boDatesRead0"] = _js_read_bo_dates(scope)

    id_info = _fill_one_bo_date(
        scope,
        page,
        ["身份证件或身份证明文件有效期限"],
        id_valid or "2032/6/24",
    )
    if not id_info.get("ok"):
        for val in ("2032/6/24", "2032/06/24", "2032-06-24"):
            id_info = _fill_one_bo_date(
                scope, page, ["身份证件或身份证明文件有效期限"], val
            )
            if id_info.get("ok"):
                break
    result["idValidPick"] = id_info
    result["idValid"] = bool(id_info.get("ok"))
    result["idValidValue"] = id_info.get("value") or ""

    date_info: dict[str, Any] = {"ok": False}
    for val in today_vals:
        date_info = _fill_one_bo_date(
            scope,
            page,
            ["受益所有权关系形成的日期", "受益所有权关系形成日期"],
            val,
        )
        if date_info.get("ok"):
            break
    result["ownershipDatePick"] = date_info
    result["ownershipDate"] = bool(date_info.get("ok")) or str(date.today().year) in (
        date_info.get("value") or ""
    )
    result["ownershipDateValue"] = date_info.get("value") or (today_vals[0] if today_vals else "")
    _close_calendar_soft(page, scope)
    page.wait_for_timeout(200)
    # 以本次填写结果为准，不再用探针覆盖成失败
    if id_info.get("ok") or "2032" in (result.get("idValidValue") or ""):
        result["idValid"] = True
    if date_info.get("ok") or str(date.today().year) in (result.get("ownershipDateValue") or ""):
        result["ownershipDate"] = True
    result["boDateInputs1"] = _js_probe_date_inputs(scope)


def _dismiss_overlays(page: Page) -> None:
    """关掉遮罩。不要按 Esc：备案页日历会因此清空刚填的日期。"""
    try:
        page.mouse.click(12, 12)
        page.wait_for_timeout(120)
    except Exception:
        pass


def _js_read_bo_dates(scope) -> dict[str, str]:
    """按标签读证件有效期 / 形成日期的真实 input 值。"""
    try:
        return (
            scope.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const readNear = (needles) => {
                    let best = '';
                    for (const el of document.querySelectorAll('label, span, td, th, div')) {
                      if (!vis(el)) continue;
                      const t = (el.innerText || '').replace(/\\s/g, '');
                      if (!needles.some(n => t.includes(n))) continue;
                      if (t.length > 90) continue;
                      const box = el.closest('tr, td, .layui-form-item, .el-form-item, li, div') || el.parentElement;
                      if (!box) continue;
                      const inps = [...box.querySelectorAll('input')].filter(i => {
                        const ty = (i.type || '').toLowerCase();
                        return ty !== 'hidden' && ty !== 'checkbox' && ty !== 'radio' && vis(i);
                      });
                      for (const inp of inps) {
                        const v = (inp.value || '').trim();
                        if (v) return v;
                      }
                      if (!best && inps[0]) best = (inps[0].value || '').trim();
                    }
                    return best;
                  };
                  return {
                    idValid: readNear(['身份证件或身份证明文件有效期限', '有效期限'])
                      || ((document.querySelector('input[name=\"cerValidDate0\"]') || {}).value || ''),
                    ownershipDate: readNear(['受益所有权关系形成的日期', '受益所有权关系形成日期', '形成的日期'])
                      || ((document.querySelector('input[name=\"profitRatioStartDate0\"]') || {}).value || ''),
                    birth: readNear(['出生日期'])
                      || ((document.querySelector('input[name=\"birthDate0\"]') || {}).value || ''),
                  };
                }"""
            )
            or {}
        )
    except Exception as e:
        return {"error": str(e)}


def _js_probe_selects(scope) -> dict[str, Any]:
    try:
        return (
            scope.evaluate(
                """() => {
                  const rectVis = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const selects = [...document.querySelectorAll('select')].slice(0, 12).map(s => ({
                    vis: rectVis(s),
                    display: getComputedStyle(s).display,
                    opts: [...s.options].map(o => (o.text || '').trim()).slice(0, 12),
                  }));
                  return {
                    selectCount: document.querySelectorAll('select').length,
                    layui: !!document.querySelector('.layui-form-select'),
                    easyui: !!document.querySelector('.combo, .combobox-item'),
                    el: !!document.querySelector('.el-select'),
                    selects,
                  };
                }"""
            )
            or {}
        )
    except Exception as e:
        return {"error": str(e)}


def _js_pick_nth_select(scope, n: int, option: str) -> dict[str, Any]:
    """按页面上第 n 个 select 选值，并尽量触发级联加载。"""
    try:
        info = scope.evaluate(
            """([n, optionText]) => {
              const optionN = (optionText || '').replace(/\\s/g, '');
              const sels = [...document.querySelectorAll('select')];
              if (n < 0 || n >= sels.length) return { ok: false, via: 'bad-index', n, total: sels.length };
              const sel = sels[n];
              const match = (t) => {
                const x = (t || '').replace(/\\s/g, '').trim();
                if (!x || x === '请选择' || x === '-请选择-') return false;
                return x === optionN || x.includes(optionN) || optionN.includes(x);
              };
              let hit = [...sel.options].find(o => match(o.text));
              if (!hit && optionN.includes('中国')) {
                hit = [...sel.options].find(o => {
                  const x = (o.text || '').replace(/\\s/g, '');
                  return x === '中国' || x === '中华人民共和国' || x.includes('中国');
                });
              }
              if (!hit) {
                return {
                  ok: false,
                  via: 'no-option',
                  n,
                  opts: [...sel.options].map(o => (o.text || '').trim()).slice(0, 20),
                };
              }
              sel.focus();
              sel.selectedIndex = hit.index;
              sel.value = hit.value;
              try {
                const ev = document.createEvent('HTMLEvents');
                ev.initEvent('change', true, true);
                sel.dispatchEvent(ev);
              } catch (e) {}
              sel.dispatchEvent(new Event('input', { bubbles: true }));
              sel.dispatchEvent(new Event('change', { bubbles: true }));
              try {
                if (window.jQuery) window.jQuery(sel).trigger('change');
              } catch (e) {}
              try {
                if (typeof sel.onchange === 'function') sel.onchange();
              } catch (e) {}
              return {
                ok: true,
                via: 'nth-select',
                n,
                text: (hit.text || '').trim(),
                value: hit.value,
                nextOpts: n + 1 < sels.length
                  ? [...sels[n + 1].options].map(o => (o.text || '').trim()).slice(0, 8)
                  : [],
              };
            }""",
            [n, option],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _js_wait_select_options(scope, n: int, *, min_opts: int = 2, timeout_ms: int = 8000) -> dict[str, Any]:
    """等第 n 个 select 的选项加载出来（级联）。"""
    deadline = timeout_ms
    step = 250
    last: dict[str, Any] = {}
    waited = 0
    while waited <= deadline:
        try:
            last = (
                scope.evaluate(
                    """(n) => {
                      const sels = [...document.querySelectorAll('select')];
                      if (n < 0 || n >= sels.length) return { ok: false, total: sels.length };
                      const opts = [...sels[n].options].map(o => (o.text || '').trim())
                        .filter(t => t && t !== '请选择' && t !== '-请选择-');
                      return { ok: opts.length > 0, count: opts.length, sample: opts.slice(0, 8) };
                    }""",
                    n,
                )
                or {}
            )
        except Exception as e:
            last = {"ok": False, "error": str(e)}
        if last.get("ok") and int(last.get("count") or 0) >= min_opts - 1:
            return last
        page = _page_of(scope)
        page.wait_for_timeout(step)
        waited += step
    last["timeout"] = True
    return last


def _fill_bo_addr_cascade(scope, page: Page, result: dict[str, Any]) -> dict[str, bool]:
    """地址四级联：固定用页面后 4 个 select（国家/省/市/县）。"""
    probe = _js_probe_selects(scope)
    total = int(probe.get("selectCount") or 0)
    # 常见布局：前 4 个是投资人/性别/国籍/证件类型，后 4 个是地址
    start = max(0, total - 4) if total >= 4 else 0
    if total >= 8:
        start = 4
    result["boAddrStart"] = start
    result["boAddrProbe0"] = probe
    pairs = (
        (("中国", "中华人民共和国"), "country"),
        (("贵州省", "贵州"), "province"),
        (("安顺市", "安顺"), "city"),
        (("西秀区", "西秀"), "district"),
    )
    addr: dict[str, bool] = {}
    picks: dict[str, Any] = {}
    for i, (opts, key) in enumerate(pairs):
        idx = start + i
        if i > 0:
            waited = _js_wait_select_options(scope, idx, min_opts=2, timeout_ms=10000)
            picks[f"{key}Wait"] = waited
        info: dict[str, Any] = {"ok": False}
        for opt in opts:
            info = _js_pick_nth_select(scope, idx, opt)
            if info.get("ok"):
                break
        if not info.get("ok"):
            # 兜底：按文案 hint 再试
            for opt in opts:
                info = _pick_bo_dropdown(scope, page, opt, hint=("国家/地区", "省", "市", "县")[i])
                if info.get("ok"):
                    break
        picks[key] = info
        addr[key] = bool(info.get("ok"))
        page.wait_for_timeout(600)
    result["boAddrPick"] = picks
    result["boAddrProbe1"] = _js_probe_selects(scope)
    return addr


def _js_pick_select(scope, option: str, hint: str = "") -> dict[str, Any]:
    """备案页多为原生 select / layui，不是 el-select。"""
    try:
        info = scope.evaluate(
            """([hint, optionText]) => {
              const optionN = (optionText || '').replace(/\\s/g, '');
              const hintN = (hint || '').replace(/\\s/g, '');
              const rectVis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none'
                  && s.visibility !== 'hidden' && s.opacity !== '0';
              };
              const matchText = (t) => {
                const n = (t || '').replace(/\\s/g, '').trim();
                if (!n || n === '请选择' || n === '-请选择-') return false;
                return n === optionN || n === '-' + optionN + '-' || n.includes(optionN);
              };
              const setNative = (sel, opt) => {
                sel.focus();
                sel.selectedIndex = opt.index;
                sel.value = opt.value;
                try {
                  const ev = document.createEvent('HTMLEvents');
                  ev.initEvent('change', true, true);
                  sel.dispatchEvent(ev);
                } catch (e) {}
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                try { if (window.jQuery) window.jQuery(sel).trigger('change'); } catch (e) {}
                const wrap = sel.closest('.layui-form-select, .layui-input-inline, .layui-form-item, td') || sel.parentElement;
                const title = wrap && wrap.querySelector('.layui-select-title input, .combo-text');
                if (title) title.value = (opt.text || '').trim();
                try { if (window.layui && layui.form) layui.form.render('select'); } catch (e) {}
                return { ok: true, via: 'native-select', text: (opt.text || '').trim() };
              };

              const scored = [];
              for (const sel of document.querySelectorAll('select')) {
                const hit = [...sel.options].find(o => matchText(o.text));
                if (!hit) continue;
                let score = 1;
                const box = sel.closest('.layui-form-item, .el-form-item, tr, td, li, .form-group, .layui-input-inline');
                const boxT = ((box && box.innerText) || '').replace(/\\s/g, '');
                if (hintN && boxT.includes(hintN)) score += 80;
                if (rectVis(sel)) score += 5;
                scored.push({ sel, hit, score });
              }
              scored.sort((a, b) => b.score - a.score);
              if (scored.length) {
                const pick = hintN ? (scored.find(s => s.score >= 80) || scored[0]) : scored[0];
                return setNative(pick.sel, pick.hit);
              }

              const nodes = [...document.querySelectorAll(
                'dd, li.el-select-dropdown__item, .combobox-item, .el-select-dropdown__item, .ant-select-item'
              )];
              for (const el of nodes) {
                if (!rectVis(el)) continue;
                const t = (el.innerText || '').replace(/\\s/g, '').trim();
                if (t === optionN || t === '-' + optionN + '-' || t.includes(optionN)) {
                  el.click();
                  return { ok: true, via: 'click-' + el.tagName, text: t };
                }
              }
              return { ok: false, via: 'none' };
            }""",
            [hint, option],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _js_has_person(scope, name: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """(name) => {
                  const n = (name || '').replace(/\\s/g, '');
                  for (const sel of document.querySelectorAll('select')) {
                    const o = sel.options[sel.selectedIndex];
                    if (o && (o.text || '').replace(/\\s/g, '').includes(n)) return true;
                  }
                  for (const inp of document.querySelectorAll('input, textarea')) {
                    if ((inp.value || '').replace(/\\s/g, '').includes(n)) return true;
                  }
                  const title = document.querySelector('.layui-form-select .layui-select-title input');
                  if (title && (title.value || '').replace(/\\s/g, '').includes(n)) return true;
                  return false;
                }""",
                name,
            )
        )
    except Exception:
        return False


def _js_open_widget(scope, hint: str) -> dict[str, Any]:
    """点开 layui / combo / 原生 select。"""
    try:
        info = scope.evaluate(
            """(hint) => {
              const hintN = (hint || '').replace(/\\s/g, '');
              const rectVis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              const widgets = [...document.querySelectorAll(
                '.layui-form-select, .combo, .el-select, select'
              )];
              let target = null;
              for (const w of widgets) {
                if (!rectVis(w) && w.tagName !== 'SELECT') continue;
                const box = w.closest('.layui-form-item, .el-form-item, tr, td, .form-group') || w.parentElement;
                const t = ((box && box.innerText) || (w.innerText) || '').replace(/\\s/g, '');
                const inp = w.querySelector && w.querySelector('input');
                const v = ((inp && inp.value) || '') + t;
                if (hintN && (v.includes(hintN) || t.includes(hintN))) { target = w; break; }
                if (!hintN && (v.includes('投资人信息') || t.includes('投资人信息'))) { target = w; break; }
              }
              if (!target && !hintN) {
                target = widgets.find(w => rectVis(w) || w.tagName === 'SELECT') || null;
              }
              if (!target) return { ok: false };
              const clickable = (target.querySelector && (
                target.querySelector('.layui-select-title')
                || target.querySelector('.combo-text')
                || target.querySelector('.combo-arrow')
                || target.querySelector('.el-input__inner')
              )) || target;
              clickable.click();
              return { ok: true, tag: target.tagName, cls: String(target.className || '').slice(0, 80) };
            }""",
            hint or "",
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _pick_bo_dropdown(scope, page: Page, option: str, hint: str = "") -> dict[str, Any]:
    _dismiss_overlays(page)
    opened = _js_open_widget(scope, hint)
    page.wait_for_timeout(450)
    clicked = {"ok": False}
    if opened.get("ok"):
        clicked = {"ok": _click_visible_dropdown_option(page, option), "via": "pw-visible"}
        if not clicked.get("ok"):
            clicked = {"ok": _js_click_eq(scope, option), "via": "js-eq"}
    if not clicked.get("ok"):
        try:
            loc = scope.locator("select").filter(
                has=scope.locator("option", has_text=re.compile(re.escape(option)))
            ).first
            loc.select_option(label=re.compile(rf".*{re.escape(option)}.*"))
            page.wait_for_timeout(300)
            clicked = {"ok": True, "via": "pw-select-option"}
        except Exception:
            clicked = _js_pick_select(scope, option, hint)
            if not clicked.get("via"):
                clicked["via"] = "native-fallback"
    clicked["opened"] = opened
    return clicked


def _field_value(scope, label: str) -> str:
    inp = None
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if _visible(item, timeout=300):
        inp = item.locator("input:not([type=hidden]):not([type=checkbox]), textarea").first
    if inp is None or not _visible(inp, timeout=200):
        inp = _input_near_label(scope, label)
    if inp is None:
        return ""
    try:
        return (inp.input_value(timeout=400) or "").strip()
    except Exception:
        try:
            return (inp.inner_text(timeout=300) or "").strip()
        except Exception:
            return ""


def _dropdown_shows(scope, label: str, expect: str) -> bool:
    try:
        txt = ""
        item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
        if _visible(item, timeout=300):
            txt = item.inner_text(timeout=400) or ""
        else:
            lab = scope.get_by_text(label, exact=False).first
            if _visible(lab, timeout=300):
                box = lab.locator("xpath=ancestor::*[contains(@class,'el-form-item') or contains(@class,'el-row')][1]")
                txt = (box.inner_text(timeout=400) if _visible(box, timeout=200) else "") or ""
        return expect in (txt or "")
    except Exception:
        return False


def _fill_bo_form(page: Page, form_scope, cfg: dict[str, Any], result: dict[str, Any]) -> None:
    """受益所有人表单：选吴文彬，再填期限/手机/地址/股权/当日。填后校验真实值。"""
    person = (cfg.get("name") or DEFAULT_NAME).strip()
    mobile = cfg.get("mobile") or DEFAULT_MOBILE
    detail = cfg.get("address") or DEFAULT_ADDRESS
    pg = _page_of(form_scope)
    _dismiss_overlays(pg)
    result["boDom"] = _js_probe_selects(form_scope)

    # 1) 投资人下拉选吴文彬（备案页是原生/layui select）
    pick_info = _pick_bo_dropdown(form_scope, pg, person, hint="投资人信息")
    if not pick_info.get("ok"):
        pick_info = _pick_bo_dropdown(form_scope, pg, person, hint="受益所有人基本信息")
    if not pick_info.get("ok"):
        pick_info = _pick_bo_investor(pg, form_scope, person) and {"ok": True, "via": "legacy"}
    result["investorPick"] = pick_info if isinstance(pick_info, dict) else {"ok": bool(pick_info)}
    pg.wait_for_timeout(1800)
    name_val = _field_value(form_scope, "姓名")
    inv = person in (name_val or "") or _js_has_person(form_scope, person)
    if not inv:
        pick_info = _pick_bo_dropdown(form_scope, pg, person, hint="")
        pg.wait_for_timeout(1500)
        name_val = _field_value(form_scope, "姓名")
        inv = person in (name_val or "") or _js_has_person(form_scope, person)
    result["investorInfo"] = bool(inv)
    result["investorNameValue"] = name_val or person if inv else (name_val or "")

    # 2) 先取消「长期」（证件有效期稍后和其他日期一起填，避免被日历冲掉）
    try:
        long_lab = form_scope.locator("label").filter(has_text=re.compile(r"长期")).first
        if _visible(long_lab, timeout=400):
            inp = long_lab.locator("input").first
            checked = False
            try:
                if _visible(inp, timeout=200):
                    checked = bool(inp.is_checked())
            except Exception:
                pass
            if checked:
                long_lab.click(force=True, timeout=2000)
    except Exception:
        pass
    _dismiss_overlays(pg)

    # 3) 联系方式一（手机号）
    _js_fill_near(form_scope, "联系方式一（手机号）", mobile) or _js_fill_near(
        form_scope, "联系方式一", mobile
    ) or _fill_by_label(form_scope, "联系方式一", mobile)
    mobile_val = _field_value(form_scope, "联系方式一") or _field_value(form_scope, "手机号")
    result["boMobile"] = mobile in mobile_val
    result["boMobileValue"] = mobile_val

    # 4) 地址四级联：按后 4 个原生 select 依次选，并等待下级选项加载
    try:
        loc = form_scope.get_by_text("经常居住地或工作单位地址", exact=False).first
        if _visible(loc, timeout=500):
            loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    _dismiss_overlays(pg)
    addr = _fill_bo_addr_cascade(form_scope, pg, result)
    result["boAddr"] = addr
    _js_fill_near(form_scope, "详细地址", detail) or _fill_by_label(form_scope, "详细地址", detail)
    detail_val = _field_value(form_scope, "详细地址")
    result["boDetailAddr"] = bool(detail_val) and ("贵州" in detail_val or detail[:6] in detail_val)
    # 手机号再补一次（日历遮罩可能导致前一次未落库）
    if not result.get("boMobile"):
        _js_fill_near(form_scope, "联系方式一（手机号）", mobile) or _js_fill_near(
            form_scope, "联系方式一", mobile
        ) or _fill_by_label(form_scope, "联系方式一", mobile)
        mobile_val = _field_value(form_scope, "联系方式一") or _field_value(form_scope, "手机号")
        result["boMobile"] = mobile in mobile_val
        result["boMobileValue"] = mobile_val

    # 5) 股权类型：只勾第一项「直接/间接拥有…股权」，绝不能勾「虽未满足…收益权/表决权」
    own_ok = False
    own_info: dict[str, Any] = {}
    try:
        own_info = form_scope.evaluate(
            """() => {
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none';
              };
              const labels = [...document.querySelectorAll('label')].filter(vis);
              const isBad = (t) => t.includes('虽未满足') || t.includes('收益权') || t.includes('表决权') || t.includes('实际控制');
              const isGood = (t) =>
                (t.includes('直接或间接拥有') && (t.includes('股权') || t.includes('股份')) && t.includes('25'))
                || (t.includes('超过25%') && t.includes('股权'));
              // 先取消错误项
              for (const lab of labels) {
                const t = (lab.innerText || '').replace(/\\s/g, '');
                if (!isBad(t)) continue;
                const inp = lab.querySelector('input[type=checkbox]');
                if (inp && inp.checked) lab.click();
              }
              // 再勾正确项
              let hit = null;
              for (const lab of labels) {
                const t = (lab.innerText || '').replace(/\\s/g, '');
                if (!isGood(t) || isBad(t)) continue;
                const inp = lab.querySelector('input[type=checkbox]');
                if (inp && !inp.checked) lab.click();
                hit = { text: t.slice(0, 40), checked: !!(inp && inp.checked) };
                break;
              }
              const checked = labels.map(lab => {
                const inp = lab.querySelector('input[type=checkbox]');
                if (!inp || !inp.checked) return null;
                return (lab.innerText || '').replace(/\\s/g, '').slice(0, 40);
              }).filter(Boolean);
              return { ok: !!(hit && hit.checked) || checked.some(t => isGood(t) && !isBad(t)), hit, checked };
            }"""
        ) or {}
        own_ok = bool(own_info.get("ok"))
    except Exception as e:
        own_info = {"error": str(e)}
        own_ok = False
    if not own_ok:
        # 兜底：精确文案
        for needle in (
            "直接或间接拥有公司25%以上股权、股份",
            "直接或间接拥有公司25%以上股权",
            "直接或间接拥有超过25%的公司股权",
        ):
            try:
                cb = form_scope.get_by_text(needle, exact=False).first
                if not _visible(cb, timeout=400):
                    continue
                t = (cb.inner_text(timeout=300) or "")
                if "虽未满足" in t or "收益权" in t:
                    continue
                lab = cb.locator("xpath=ancestor::label[1]").first
                (lab if _visible(lab, timeout=200) else cb).click(timeout=3000)
                own_ok = True
                break
            except Exception:
                continue
    result["ownershipType"] = own_ok
    result["ownershipTypeInfo"] = own_info
    pg.wait_for_timeout(600)

    # 6) 比例 100（股权比例，不是收益权/表决权）
    def _fill_equity_ratio() -> tuple[bool, str]:
        for lab in ("持有股权、股份的比例", "持有股权、股份的比例（%）", "股权、股份的比例"):
            if _js_fill_near(form_scope, lab, "100") or _fill_by_label(form_scope, lab, "100"):
                v = _field_value(form_scope, lab) or "100"
                if "100" in v:
                    return True, v
        try:
            ok = bool(
                form_scope.evaluate(
                    """() => {
                      const needles = ['持有股权', '股份的比例', '股权、股份'];
                      const vis = (el) => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                      };
                      for (const el of document.querySelectorAll('label, span, td, div')) {
                        if (!vis(el)) continue;
                        const t = (el.innerText || '').replace(/\\s/g, '');
                        if (!needles.some(n => t.includes(n))) continue;
                        if (t.includes('收益权') || t.includes('表决权')) continue;
                        if (t.length > 40) continue;
                        const box = el.closest('tr, .el-form-item, .layui-form-item, div') || el.parentElement;
                        const inp = box && [...box.querySelectorAll('input')].find(i =>
                          i.type !== 'checkbox' && i.type !== 'hidden' && i.type !== 'radio' && vis(i)
                        );
                        if (!inp) continue;
                        inp.focus();
                        const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                        if (desc && desc.set) desc.set.call(inp, '100'); else inp.value = '100';
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        return inp.value === '100';
                      }
                      return false;
                    }"""
                )
            )
            if ok:
                return True, "100"
        except Exception:
            pass
        return False, ""

    ratio_ok, ratio_val = _fill_equity_ratio()
    result["ownershipRatio"] = ratio_ok and "100" in (ratio_val or "100")
    result["ownershipRatioValue"] = ratio_val or ("100" if ratio_ok else "")
    pg.wait_for_timeout(300)

    # 7) 证件有效期 + 形成日期（最后填，关掉日历，按真实 input 值校验）
    today = date.today()
    today_vals = [
        today.strftime("%Y-%m-%d"),
        f"{today.year}/{today.month}/{today.day}",
        f"{today.year}/{today.month:02d}/{today.day:02d}",
    ]
    id_cfg = (cfg.get("id_valid") or DEFAULT_ID_VALID).replace("-", "/")
    # 2032-06-24 → 2032/6/24
    parts = id_cfg.replace("-", "/").split("/")
    if len(parts) == 3:
        id_slash = f"{int(parts[0])}/{int(parts[1])}/{int(parts[2])}"
    else:
        id_slash = "2032/6/24"
    _fill_bo_dates(form_scope, pg, id_slash, today_vals, result)
    _close_calendar_soft(pg, form_scope)

    # 日期填写可能影响勾选区，再确认股权类型 + 比例
    try:
        form_scope.evaluate(
            """() => {
              const isBad = (t) => t.includes('虽未满足') || (t.includes('收益权') && t.includes('表决权'));
              for (const lab of document.querySelectorAll('label')) {
                const t = (lab.innerText || '').replace(/\\s/g, '');
                const inp = lab.querySelector('input[type=checkbox]');
                if (!inp || !inp.checked) continue;
                if (isBad(t)) lab.click();
              }
            }"""
        )
    except Exception:
        pass
    if not result.get("ownershipType"):
        result["ownershipType"] = own_ok
    ratio_ok2, ratio_val2 = _fill_equity_ratio()
    if ratio_ok2:
        result["ownershipRatio"] = True
        result["ownershipRatioValue"] = ratio_val2 or "100"
    result["ownershipRatioAfterDates"] = {
        "ok": ratio_ok2,
        "value": ratio_val2,
    }


def _mark_personnel_done(result: dict[str, Any]) -> None:
    for k in (
        "addDirectorClick",
        "directorConfirm",
        "addManagerClick",
        "managerConfirm",
        "addLiaisonClick",
        "liaisonConfirm",
        "fillBeneficialClick",
        "boEntryReady",
    ):
        result[k] = True


def _complete_beneficial(
    case_id: str, page: Page, cfg: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """图6–9：自行填报 → 自然人条件 → 表单 → 已完成进入下一步。"""
    already_form = _page_has_text(page, "受益所有人基本信息") or _page_has_text(
        page, "受益所有权信息"
    )
    already_nat = _page_has_text(page, "存在符合下列条件之一的自然人")

    # 6) 图6：自行填报 → 下一步（已在后续页则跳过）
    page.wait_for_timeout(500)
    if already_form or already_nat:
        result["selfReport"] = True
        result["selfReportSkipped"] = "already-past"
    else:
        self_ok = False
        for scope in _all_scopes(page):
            if _click_radio(scope, "自行填报"):
                self_ok = True
                break
        if not self_ok:
            self_ok = _mouse_click_text(page, ["自行填报"], exact=True) or _mouse_click_text(
                page, ["自行填报"], exact=False
            )
        result["selfReport"] = self_ok or _page_has_text(page, "自行填报")
        page.wait_for_timeout(400)
        next2 = _mouse_click_text(page, ["下一步"], exact=True) or _click_text_any(
            page, ["下一步"], exact=True
        )
        result["beneficialNext1"] = next2
        page.wait_for_timeout(1500)
        result["afterSelfReady"] = (
            _page_has_text(page, "存在符合下列条件之一的自然人")
            or _page_has_text(page, "受益所有人基本信息")
            or _wait_text_any(
                page,
                ["存在符合下列条件之一的自然人", "受益所有人基本信息"],
                timeout_ms=15000,
            )
        )
    _shot(page, case_id, "personnel-after-self-report")

    # 7) 图7：存在符合下列条件之一的自然人 → 下一步（若已在表单页则跳过）
    nat_ok = False
    try:
        already_form = False
        for scope in _all_scopes(page):
            t = ""
            try:
                t = _body(scope).inner_text(timeout=500) or ""
            except Exception:
                continue
            if "受益所有人基本信息" in t or "受益所有权信息" in t:
                already_form = True
                break
        if already_form:
            nat_ok = True
            result["naturalSkipped"] = "already-on-form"
        else:
            for scope in _all_scopes(page):
                if _click_radio(scope, "存在符合下列条件之一的自然人"):
                    nat_ok = True
                    break
            if not nat_ok:
                nat_ok = _mouse_click_text(page, ["存在符合下列条件之一的自然人"], exact=False)
            page.wait_for_timeout(400)
            next3 = _mouse_click_text(page, ["下一步"], exact=True) or _click_text_any(
                page, ["下一步"], exact=True
            )
            result["beneficialNext2"] = next3
            page.wait_for_timeout(1500)
    except Exception as e:
        result["naturalError"] = str(e)
    result["naturalCondition"] = nat_ok
    _shot(page, case_id, "personnel-after-natural-condition")

    # 8) 图8：受益所有人表单（吴文彬 → 期限/手机/地址/股权100%/当日 → 下一步 → 确定）
    # 先记住登记页：备案「下一步/确定」后 BO 标签常被关掉，必须回到登记页点「已完成」
    reg_page = _find_reg_page(page) or page
    form_scope = None
    work_page = page
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        for scope in _all_scopes(pg):
            try:
                if scope.get_by_text("受益所有人基本信息", exact=False).count() > 0:
                    form_scope = scope
                    work_page = pg
                    break
                if scope.get_by_text("受益所有权信息", exact=False).count() > 0:
                    form_scope = scope
                    work_page = pg
                    break
            except Exception:
                continue
        if form_scope is not None:
            break
    if form_scope is None:
        form_scope = page
    page = work_page

    _fill_bo_form(page, form_scope, cfg, result)
    _shot(page, case_id, "personnel-beneficial-filled")

    addr = result.get("boAddr") or {}
    # 点下一步前再补一次股权比例（避免勾错类型导致比例框未出现）
    if not result.get("ownershipRatio"):
        try:
            needle = "直接或间接拥有公司25%以上股权、股份"
            cb = form_scope.get_by_text(needle, exact=False).first
            if _visible(cb, timeout=400):
                lab = cb.locator("xpath=ancestor::label[1]").first
                (lab if _visible(lab, timeout=200) else cb).click(timeout=2500)
                _safe_wait(page, 400)
            _js_fill_near(form_scope, "持有股权、股份的比例", "100") or _fill_by_label(
                form_scope, "持有股权、股份的比例", "100"
            )
            rv = _field_value(form_scope, "持有股权、股份的比例") or ""
            result["ownershipRatio"] = "100" in rv
            result["ownershipRatioValue"] = rv
            result["ownershipRatioRetry"] = True
        except Exception as e:
            result["ownershipRatioRetryError"] = str(e)
    # 日期探针常读空；以填写标志 + input 实值为准
    date_inputs = result.get("boDateInputs1") or result.get("boDateInputs0") or []
    id_input_v = next(
        (str(i.get("value") or "") for i in date_inputs if i.get("name") == "cerValidDate0"),
        "",
    )
    own_input_v = next(
        (
            str(i.get("value") or "")
            for i in date_inputs
            if i.get("name") == "shareRatioStartDate0"
        ),
        "",
    )
    dates_ok = bool(
        (result.get("idValid") or ("2032" in id_input_v))
        and (result.get("ownershipDate") or bool(own_input_v))
    )
    form_ok = bool(
        result.get("investorInfo")
        and dates_ok
        and result.get("boMobile")
        and addr.get("country")
        and addr.get("province")
        and addr.get("city")
        and addr.get("district")
        and result.get("ownershipType")
        and result.get("ownershipRatio")
    )
    result["beneficialFormOk"] = form_ok
    result["datesOk"] = dates_ok
    next4 = False
    conf = False
    if form_ok:
        # 日期已在表单里填过，直接下一步；BO 页随后可能关闭，全部用安全等待
        try:
            _close_calendar_soft(page, form_scope)
            _safe_wait(page, 300)
            if _page_alive(page):
                next4 = _mouse_click_text(page, ["下一步"], exact=True) or _click_text_any(
                    page, ["下一步"], exact=True
                )
            result["beneficialNext3"] = next4
            _safe_wait(page, 1500)
            if _page_alive(page):
                box, _ = _find_dialog(page, "确定")
                if box is None:
                    box, _ = _find_dialog(page, "提示")
                if box is not None:
                    conf = _click_dialog_footer_btn(
                        page, ["确定", "确认"], exclude=["返回", "取消"]
                    )
                    if not conf:
                        conf = _click_dialog_ok(box, page)
                if not conf:
                    try:
                        for scope in _all_scopes(page):
                            btn = scope.locator(
                                ".layui-layer-btn0, .layui-layer .layui-layer-btn a"
                            ).filter(has_text=re.compile(r"确\s*定")).first
                            if _visible(btn, timeout=400):
                                btn.click(timeout=2500)
                                conf = True
                                break
                    except Exception:
                        pass
                if not conf:
                    conf = _mouse_click_text(page, ["确定"], exact=True) or _click_text_any(
                        page, ["确定"], exact=True
                    )
        except Exception as e:
            result["boSubmitError"] = str(e)
            # 备案页被关时，提交动作往往已生效
            if "closed" in str(e).lower() or "Target page" in str(e):
                result["boPageClosedOnSubmit"] = True
                if next4:
                    conf = True
    else:
        result["beneficialNext3"] = False
        result["skipNextReason"] = "form-incomplete"
    result["beneficialConfirm"] = conf
    _safe_wait(page if _page_alive(page) else reg_page, 1200)

    # 备案标签关闭后切回登记页，再点图9「已完成，进入下一步」
    if not _page_alive(page) or not _bo_prompt_open(page):
        back = _find_reg_page(reg_page) or _find_reg_page(page)
        if back is not None:
            try:
                back.bring_to_front()
            except Exception:
                pass
            page = back
            result["switchedBackToReg"] = True
            result["regUrl"] = _url_of(page)
    try:
        _shot(page, case_id, "personnel-after-beneficial-confirm")
    except Exception:
        pass

    # 9) 图9：仅当表单关键字段已填、且无「未获取到」时，才点「已完成，进入下一步」
    done = False
    # 备案页关掉也视为已确认（系统已收回）
    if form_ok and (conf or result.get("boPageClosedOnSubmit")) and _page_alive(page):
        if not _bo_not_obtained(page):
            tip2, _ = _find_dialog(page, "提示")
            if tip2 is None:
                tip2, _ = _find_dialog(page, "受益所有人")
            done = _click_dialog_footer_btn(
                page,
                ["已完成，进入下一步", "已完成,进入下一步"],
                exclude=["未完成", "返回修改", "填报受益所有人"],
            )
            if not done:
                done = _mouse_click_text(
                    page, ["已完成，进入下一步", "已完成,进入下一步"], exact=False
                )
            if not done:
                for txt in ("已完成，进入下一步", "已完成,进入下一步"):
                    if tip2 is not None and _click_text(tip2, [txt], exact=False):
                        done = True
                        break
            _safe_wait(page, 1500)
            if _bo_not_obtained(page):
                done = False
                result["error"] = "已点完成但仍提示未获取到受益所有人信息"
        else:
            result["skipDoneReason"] = "still-not-obtained"
    else:
        result["skipDoneReason"] = "form-incomplete-or-no-confirm"
    result["doneNext"] = done
    try:
        _shot(page if _page_alive(page) else reg_page, case_id, "personnel-done")
    except Exception:
        pass

    missing = []
    if not result.get("addDirectorClick") or not result.get("directorConfirm"):
        missing.append("董事")
    if not result.get("addManagerClick") or not result.get("managerConfirm"):
        missing.append("高级管理人员")
    if not result.get("addLiaisonClick") or not result.get("liaisonConfirm"):
        missing.append("联络员")
    if not result.get("fillBeneficialClick"):
        missing.append("填报受益所有人入口")
    if not result.get("selfReport"):
        missing.append("自行填报")
    if not form_ok:
        missing.append("受益所有人表单")
    if not result.get("doneNext"):
        missing.append("已完成进入下一步")
    result["missing"] = missing
    result["ok"] = not missing
    _dump(case_id, "fill-personnel-result.json", result)
    return result


def run_personnel_flow(case_id: str, page: Page, payload: dict[str, Any]) -> dict[str, Any]:
    store.set_status(
        case_id,
        statuses.FILLING_ESTABLISHMENT,
        customer_message="正在填写人员信息…",
    )
    cfg = _defaults(payload)
    result: dict[str, Any] = {"ok": False, "cfg": cfg}

    page = _prefer_work_page(page)
    result["closedPortal"] = _close_stray_portal_tabs(page)
    result["entryUrl"] = page.url

    # 中断后续跑：图5 提示已弹出，人员三项已齐，直接填报受益所有人
    if _bo_prompt_open(page):
        result["resumedAt"] = "bo-prompt"
        result["pageOk"] = True
        _mark_personnel_done(result)
        bo_entry = _click_bo_entry(page)
        result["fillBeneficialClick"] = bool(bo_entry.get("clicked") or bo_entry.get("alreadyOnPage"))
        result["boEntry"] = {k: v for k, v in bo_entry.items() if k != "page"}
        result["boEntryReady"] = bool(bo_entry.get("ready"))
        if isinstance(bo_entry.get("page"), Page):
            page = bo_entry["page"]
        result["closedPortalAfterBo"] = _close_stray_portal_tabs(page)
        _shot(page, case_id, "personnel-beneficial-entry")
        if not result["boEntryReady"]:
            result["error"] = "未进入受益所有人填报页"
            result["missing"] = ["填报受益所有人入口"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result
        return _complete_beneficial(case_id, page, cfg, result)

    # 中断后续跑：已在受益所有人页则直接填表（人员信息页弹窗文案不算）
    on_bo = (
        _page_has_text(page, "自行填报")
        or _page_has_text(page, "存在符合下列条件之一的自然人")
        or _page_has_text(page, "受益所有人基本信息")
    )
    if not _is_portal_home(page) and on_bo and _frame_personnel(page, timeout_ms=1200) is None:
        result["resumedAt"] = "beneficial"
        result["pageOk"] = True
        _mark_personnel_done(result)
        return _complete_beneficial(case_id, page, cfg, result)

    # 等待进入人员信息（出资情况下一步之后；URL 常含 chief-executive-officer）
    frame = _frame_personnel(page)
    if frame is None:
        _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(2000)
        frame = _frame_personnel(page, timeout_ms=30000)
    if frame is None:
        result["error"] = "未进入人员信息页"
        result["entryUrlAfter"] = page.url
        _shot(page, case_id, "personnel-page-missing")
        _dump(case_id, "fill-personnel-result.json", result)
        return result
    result["pageOk"] = True
    result["frameUrl"] = getattr(frame, "url", "") or ""
    try:
        frame.evaluate(
            """() => {
              window.scrollTo(0, 0);
              const app = document.querySelector('wujie-app');
              const root = app && (app.shadowRoot || app);
              if (root) {
                const sc = root.querySelector('.el-main, .main, .content, #app, body') || root;
                if (sc && sc.scrollTo) sc.scrollTo(0, 0);
                if (sc) sc.scrollTop = 0;
              }
              document.documentElement.scrollTop = 0;
              document.body && (document.body.scrollTop = 0);
            }"""
        )
    except Exception:
        try:
            page.mouse.wheel(0, -5000)
        except Exception:
            pass
    for tip in ("设一名董事", "董事信息", "人员信息"):
        try:
            loc = frame.get_by_text(tip, exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
                break
        except Exception:
            continue
    page.wait_for_timeout(500)
    _shot(page, case_id, "personnel-page")

    # 1) 董事/监事选项
    result["oneDirector"] = _click_radio(frame, "设一名董事")
    page.wait_for_timeout(300)
    result["noSupervisor"] = _click_radio(
        frame, "属规模较小或者股东人数较少的有限责任公司，全体股东一致同意不设监事"
    )
    if not result["noSupervisor"]:
        result["noSupervisor"] = _click_radio(frame, "不设监事")
    if not result["noSupervisor"]:
        result["noSupervisor"] = _click_radio(frame, "规模较小")
    page.wait_for_timeout(400)
    _shot(page, case_id, "personnel-after-radios")

    # 2) 添加董事（已有 1/1 则跳过，方便中断后续跑）
    if _section_quota_done(frame, "董事"):
        result["addDirectorClick"] = True
        result["directorConfirm"] = True
        result["directorSkipped"] = "already-filled"
    else:
        add_dir = (
            _click_text(frame, ["+ 添加董事 >>", "添加董事 >>", "+ 添加董事", "添加董事"], exact=False)
            or _click_text_any(page, ["添加董事"], exact=False)
        )
        result["addDirectorClick"] = add_dir
        page.wait_for_timeout(900)
        dlg, _ = _find_dialog(page, "新增董事")
        if dlg is None:
            dlg, _ = _find_dialog(page, "新增董事", "董事")
        if dlg is None:
            result["error"] = "未打开新增董事弹窗"
            _shot(page, case_id, "personnel-director-dialog-missing")
            _dump(case_id, "fill-personnel-result.json", result)
            return result
        result["directorReuse"] = _reuse_first_person(page, host_dialog=dlg, person_name=cfg["name"])
        page.wait_for_timeout(1200)
        result["directorOcrAfterReuse"] = _wait_ocr_idle(dlg, page, timeout_ms=15000)
        result["directorIsLegalRep"] = _set_yes_no(dlg, "法定代表人", True) or _set_yes_no(
            dlg, "是否由董事担任", True
        )
        page.wait_for_timeout(300)
        _shot(page, case_id, "personnel-director-before-confirm")
        result["directorConfirm"] = _confirm_until_closed(page, ("新增董事",), retries=5)
        page.wait_for_timeout(600)
        _shot(page, case_id, "personnel-after-director")
        if _dialog_still_open(page, "新增董事"):
            try:
                d3, _ = _find_dialog(page, "新增董事")
                if d3 is not None:
                    result["directorDialogText"] = (d3.inner_text(timeout=800) or "")[:500]
            except Exception:
                pass
            result["error"] = "董事弹窗未关闭（确定未命中）"
            result["missing"] = ["董事"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result

    # 3) 添加高级管理人员
    if _section_quota_done(frame, "高管"):
        result["addManagerClick"] = True
        result["managerConfirm"] = True
        result["managerSkipped"] = "already-filled"
    else:
        try:
            loc = frame.get_by_text("高级管理人员", exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            try:
                frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                page.mouse.wheel(0, 1200)
        page.wait_for_timeout(400)
        add_mgr = (
            _click_text(
                frame,
                ["+ 添加高级管理人员 >>", "添加高级管理人员 >>", "添加高级管理人员"],
                exact=False,
            )
            or _click_text_any(page, ["添加高级管理人员"], exact=False)
        )
        result["addManagerClick"] = add_mgr
        page.wait_for_timeout(900)
        dlg, _ = _find_dialog(page, "高级管理人员")
        if dlg is None:
            dlg, _ = _find_dialog(page, "新增高级")
        if dlg is None:
            result["error"] = "未打开高级管理人员弹窗"
            _shot(page, case_id, "personnel-manager-dialog-missing")
            _dump(case_id, "fill-personnel-result.json", result)
            return result
        try:
            title = (dlg.locator(".el-dialog__title").inner_text(timeout=500) or "")
            result["managerDialogTitle"] = title
            if "董事" in title and "高级" not in title:
                result["error"] = "打开的仍是董事弹窗，非高级管理人员"
                _shot(page, case_id, "personnel-manager-wrong-dialog")
                _dump(case_id, "fill-personnel-result.json", result)
                return result
        except Exception:
            pass
        result["managerReuse"] = _reuse_first_person(page, host_dialog=dlg, person_name=cfg["name"])
        page.wait_for_timeout(1200)
        result["managerOcr"] = _wait_ocr_idle(dlg, page, timeout_ms=15000)
        result["managerTitle"] = _select_dropdown(dlg, "职务", "财务负责人")
        if not result["managerTitle"]:
            try:
                item = dlg.locator(".el-form-item").filter(has_text=re.compile(r"职务")).first
                trig = item.locator(".el-select .el-input__inner, .el-select").first
                if _visible(trig, timeout=500):
                    trig.click(timeout=2500)
                    page.wait_for_timeout(400)
                    for root in _all_scopes(page):
                        opt = root.locator(".el-select-dropdown:visible li").filter(
                            has_text=re.compile(r"财务负责人")
                        ).first
                        if _visible(opt, timeout=400):
                            opt.click(timeout=2500)
                            result["managerTitle"] = True
                            break
            except Exception:
                pass
        page.wait_for_timeout(700)
        for _ in range(10):
            try:
                txt = dlg.inner_text(timeout=500) or ""
            except Exception:
                txt = ""
            if "产生方式" in txt:
                break
            page.wait_for_timeout(300)
        result["appointMethod"] = _select_dropdown(dlg, "产生方式", "董事聘任")
        if not result["appointMethod"]:
            try:
                item = dlg.locator(".el-form-item").filter(has_text=re.compile(r"产生方式")).first
                trig = item.locator(".el-select .el-input__inner, .el-select").first
                if _visible(trig, timeout=500):
                    trig.click(timeout=2500)
                    page.wait_for_timeout(350)
                    for root in _all_scopes(page):
                        opt = root.get_by_text("董事聘任", exact=False).first
                        if _visible(opt, timeout=400):
                            opt.click(timeout=2500)
                            result["appointMethod"] = True
                            break
            except Exception:
                pass
        page.wait_for_timeout(300)
        _shot(page, case_id, "personnel-manager-before-confirm")
        result["managerConfirm"] = _confirm_until_closed(page, ("高级管理人员",))
        if not result["managerConfirm"]:
            result["managerConfirm"] = _confirm_until_closed(page, ("新增", "高级"))
        page.wait_for_timeout(600)
        _shot(page, case_id, "personnel-after-manager")
        if _dialog_still_open(page, "高级管理人员") or _dialog_still_open(page, "新增董事"):
            result["error"] = "高级管理人员弹窗未关闭"
            result["missing"] = ["高级管理人员"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result

    # 4) 添加联络员
    if _section_quota_done(frame, "联络员"):
        result["addLiaisonClick"] = True
        result["liaisonConfirm"] = True
        result["liaisonSkipped"] = "already-filled"
    else:
        try:
            loc = frame.get_by_text("登记联络员", exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            try:
                frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                page.mouse.wheel(0, 800)
        page.wait_for_timeout(400)
        add_lia = (
            _click_text(frame, ["+ 添加联络员 >>", "添加联络员 >>", "添加联络员"], exact=False)
            or _click_text_any(page, ["添加联络员"], exact=False)
        )
        result["addLiaisonClick"] = add_lia
        page.wait_for_timeout(900)
        dlg, _ = _find_dialog(page, "登记联络员")
        if dlg is None:
            dlg, _ = _find_dialog(page, "联络员")
        if dlg is None:
            result["error"] = "未打开联络员弹窗"
            _shot(page, case_id, "personnel-liaison-dialog-missing")
            _dump(case_id, "fill-personnel-result.json", result)
            return result
        try:
            title = (dlg.locator(".el-dialog__title").inner_text(timeout=500) or "")
            result["liaisonDialogTitle"] = title
            if "董事" in title and "联络" not in title:
                result["error"] = "打开的仍是董事弹窗，非联络员"
                _shot(page, case_id, "personnel-liaison-wrong-dialog")
                _dump(case_id, "fill-personnel-result.json", result)
                return result
        except Exception:
            pass
        result["liaisonReuse"] = _reuse_first_person(page, host_dialog=dlg, person_name=cfg["name"])
        page.wait_for_timeout(900)
        result["liaisonPublicSys"] = _set_yes_no(dlg, "代表登录|公示系统", True)
        if not result["liaisonPublicSys"]:
            result["liaisonPublicSys"] = _set_yes_no(dlg, "公示系统", True)
        result["liaisonEmail"] = _fill_by_label(dlg, "电子邮箱", cfg["email"]) or _fill_by_label(
            dlg, "邮箱", cfg["email"]
        )
        result["liaisonAddress"] = _fill_by_label(dlg, "住所", cfg["address"]) or _fill_by_label(
            dlg, "地址", cfg["address"]
        )
        page.wait_for_timeout(300)
        _shot(page, case_id, "personnel-liaison-before-confirm")
        result["liaisonConfirm"] = _confirm_until_closed(page, ("联络员",))
        page.wait_for_timeout(600)
        _shot(page, case_id, "personnel-after-liaison")
        if not result["liaisonConfirm"]:
            result["error"] = "联络员弹窗未关闭"
            result["missing"] = ["联络员"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result

    # 5) 人员信息页下一步 → 图5 提示：点弹窗底部「填报受益所有人信息」
    try:
        frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    page.wait_for_timeout(300)
    next1 = _click_text(frame, ["下一步"], exact=True) or _click_text_any(page, ["下一步"], exact=True)
    result["personnelNext"] = next1
    page.wait_for_timeout(800)
    bo_entry = _click_bo_entry(page)
    result["fillBeneficialClick"] = bool(bo_entry.get("clicked") or bo_entry.get("alreadyOnPage"))
    result["boEntry"] = {k: v for k, v in bo_entry.items() if k != "page"}
    result["boEntryReady"] = bool(bo_entry.get("ready"))
    if isinstance(bo_entry.get("page"), Page):
        page = bo_entry["page"]
    result["closedPortalAfterBo"] = _close_stray_portal_tabs(page)
    _shot(page, case_id, "personnel-beneficial-entry")
    if not result["boEntryReady"]:
        result["error"] = "未进入受益所有人填报页"
        result["missing"] = ["填报受益所有人入口"]
        _dump(case_id, "fill-personnel-result.json", result)
        return result

    return _complete_beneficial(case_id, page, cfg, result)
