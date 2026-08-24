"""核名之后的基本信息板块（新增，不改核名代码）。

流程：
1. 比对结果：勾选「已知晓」→ 下一步
2. 告知书/承诺书：勾选两个「已详细阅读」→ 确定
3. 温馨提示：我已知晓上述内容
4. 经办人：上传身份证正反面 → OCR → 移动电话 / 经办人类型
5. 企业信息：申请机关 / 企业类型 / 经营期限 / 住所弹窗 / 联系电话 / 章程决议日期 → 暂存
6. 经营范围：选择 → 跳过 → 推荐/行业分类 → 搜科技 → 前五个结果全部添加 → 生成结果
7. 下拉到底 → 下一步
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

# 经营范围页按钮文案实际是「添 加」（中间可能是普通空格/nbsp），不能只匹配「添加」
_ADD_BTN_RE = re.compile(r"^\s*添\s*加\s*$")
_ADD_BTN_ROLE_RE = re.compile(r"添\s*加")


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


def _notice_dialog_visible(page: Page) -> bool:
    for scope in _all_scopes(page):
        try:
            dlg = scope.locator(".el-dialog").filter(has_text="告知书").filter(has_text="承诺书").first
            if _visible(dlg, timeout=400):
                return True
            dlg2 = scope.locator(".el-dialog").filter(has_text="已详细阅读").filter(has_text="确定").first
            if _visible(dlg2, timeout=400):
                return True
        except Exception:
            continue
    return False


def _force_dismiss_notice(page: Page, case_id: str) -> dict[str, Any]:
    """勾选告知/承诺并点确定，仅在弹窗消失时返回 ok。"""
    info: dict[str, Any] = {"ok": False, "checked": 0, "clicks": 0}
    for attempt in range(12):
        if not _notice_dialog_visible(page):
            info["ok"] = True
            info["attempt"] = attempt
            return info
        res = _eval_all_frames(page, _js_handle_notice())
        info["js"] = res
        for scope in _all_scopes(page):
            try:
                dlg = scope.locator(".el-dialog").filter(has_text="告知书").filter(has_text="承诺书").first
                if not _visible(dlg):
                    dlg = scope.locator(".el-dialog").filter(has_text="已详细阅读").first
                if not _visible(dlg):
                    continue
                tab = dlg.get_by_text("企业名称登记承诺书", exact=True).first
                if _visible(tab):
                    try:
                        tab.click(timeout=1200)
                    except Exception:
                        pass
                for kw in ("告知书", "承诺书"):
                    lab = dlg.locator("label.el-checkbox").filter(has_text=kw).first
                    if not _visible(lab):
                        continue
                    cls = lab.get_attribute("class") or ""
                    if "is-checked" not in cls:
                        try:
                            lab.locator(".el-checkbox__inner").first.click(force=True, timeout=2000)
                        except Exception:
                            try:
                                lab.click(force=True, timeout=2000)
                            except Exception:
                                pass
                checked_n = dlg.locator("label.el-checkbox.is-checked").count()
                info["checked"] = checked_n
                if checked_n < 2:
                    break
                page.wait_for_timeout(350)
                btn = dlg.locator("button.el-button--primary").filter(has_text="确定").first
                if not _visible(btn):
                    btn = dlg.get_by_role("button", name=re.compile(r"确\s*定")).first
                if not _visible(btn):
                    btn = dlg.locator("button, .el-button").filter(has_text="确定").last
                if not _visible(btn):
                    break
                # 多种点击方式，避免 Vue/shadow 吞事件
                try:
                    btn.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                try:
                    btn.evaluate(
                        """el => {
                          el.disabled = false;
                          el.classList.remove('is-disabled');
                          el.click();
                          el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, composed:true}));
                        }"""
                    )
                    info["clicks"] += 1
                except Exception:
                    pass
                try:
                    btn.click(force=True, timeout=2500)
                    info["clicks"] += 1
                except Exception:
                    box = btn.bounding_box()
                    if box:
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        info["clicks"] += 1
                break
            except Exception:
                continue
        page.wait_for_timeout(900)
        _shot(page, case_id, f"after-notice-try-{attempt}")
        if not _notice_dialog_visible(page):
            info["ok"] = True
            info["attempt"] = attempt
            return info
        if _dialog_visible(page, "温馨提示") or _dialog_visible(page, "经办人信息") or _dialog_visible(
            page, "请上传身份证人像面"
        ):
            info["ok"] = True
            info["attempt"] = attempt
            return info
    info["ok"] = not _notice_dialog_visible(page)
    return info


def _visible_dialog(page: Page, title_hint: str):
    for scope in _all_scopes(page):
        for sel in (
            ".el-dialog:visible",
            ".el-message-box:visible",
            ".el-overlay-dialog .el-dialog",
            "[role='dialog']",
        ):
            try:
                dialogs = scope.locator(sel)
                for i in range(min(dialogs.count(), 6)):
                    d = dialogs.nth(i)
                    if not _visible(d):
                        continue
                    text = d.inner_text(timeout=800) or ""
                    if title_hint in text:
                        return d, scope
            except Exception:
                continue
    return None, None


def _check_in_dialog(dialog, keywords: list[str]) -> bool:
    """在指定弹层内勾选复选框。"""
    if dialog is None:
        return False
    # 1) 弹层内所有未勾选 checkbox
    try:
        boxes = dialog.locator("label.el-checkbox:not(.is-checked) .el-checkbox__inner, .el-checkbox__input:not(.is-checked) .el-checkbox__inner")
        n = boxes.count()
        if n > 0:
            for i in range(min(n, 4)):
                try:
                    boxes.nth(i).click(timeout=3000, force=True)
                except Exception:
                    continue
            return True
    except Exception:
        pass
    # 2) 按文案定位
    for kw in keywords:
        try:
            lab = dialog.locator("label.el-checkbox").filter(has_text=kw).first
            if _visible(lab):
                cls = lab.get_attribute("class") or ""
                if "is-checked" not in cls:
                    inner = lab.locator(".el-checkbox__inner").first
                    if inner.count():
                        inner.click(timeout=3000, force=True)
                    else:
                        lab.click(timeout=3000, force=True)
                return True
        except Exception:
            continue
        try:
            node = dialog.get_by_text(kw, exact=False).first
            if _visible(node):
                # 点文案前的方框：向左偏移点击
                box = node.bounding_box()
                if box:
                    dialog.page.mouse.click(box["x"] - 18, box["y"] + box["height"] / 2)
                    return True
        except Exception:
            continue
    return False


def _click_in_dialog(dialog, texts: list[str]) -> bool:
    if dialog is None:
        return False
    for t in texts:
        try:
            btn = dialog.get_by_role("button", name=t).first
            if _visible(btn):
                btn.click(timeout=5000)
                return True
        except Exception:
            pass
        try:
            btn = dialog.locator("button, .el-button").filter(has_text=t).first
            if _visible(btn):
                btn.click(timeout=5000)
                return True
        except Exception:
            pass
    return False


def _check_near_text(page: Page, keywords: list[str]) -> bool:
    """勾选文案附近的复选框（未勾选才点）。"""
    for hint in ("比对结果", "告知书", "承诺书", "已详细阅读", "温馨提示"):
        dialog, _ = _visible_dialog(page, hint)
        if dialog is not None and _check_in_dialog(dialog, keywords):
            return True
    checked_any = False
    for scope in _all_scopes(page):
        for kw in keywords:
            try:
                lab = scope.locator("label.el-checkbox").filter(has_text=kw).first
                if _visible(lab):
                    cls = lab.get_attribute("class") or ""
                    if "is-checked" not in cls:
                        lab.locator(".el-checkbox__inner").first.click(timeout=3000, force=True)
                    checked_any = True
            except Exception:
                continue
    return checked_any


def _body(scope: Page | Frame):
    """wujie 下可能有两个 body，优先业务 body。"""
    try:
        w = scope.locator("wujie-app body")
        if w.count() > 0:
            return w.first
    except Exception:
        pass
    return scope.locator("body").first


def _frame_has_text(scope: Page | Frame, text: str) -> bool:
    try:
        loc = scope.get_by_text(text, exact=False)
        if loc.count() > 0:
            return True
    except Exception:
        pass
    try:
        body = _body(scope).inner_text(timeout=600)
        if text in (body or ""):
            return True
    except Exception:
        pass
    return False


def _dialog_visible(page: Page, *texts: str) -> bool:
    for scope in _all_scopes(page):
        if all(_frame_has_text(scope, t) for t in texts):
            return True
    # Playwright 主页面 text 引擎有时能扫到子 frame
    try:
        if all(page.get_by_text(t, exact=False).count() > 0 for t in texts):
            return True
    except Exception:
        pass
    return False


def _wait_text(page: Page, texts: list[str], timeout_sec: float = 30) -> bool:
    end = time.time() + timeout_sec
    while time.time() < end:
        for t in texts:
            if _dialog_visible(page, t):
                return True
        page.wait_for_timeout(400)
    return False


def _debug_frames(page: Page) -> list[dict[str, Any]]:
    rows = []
    for fr in page.frames:
        try:
            body = (_body(fr).inner_text(timeout=500) or "")[:120]
        except Exception as e:
            body = f"err:{e}"
        rows.append({"url": (fr.url or "")[:120], "name": fr.name, "snip": body.replace("\n", " ")})
    return rows


def _eval_all_frames(page: Page, expression: str) -> Any:
    """在所有 frame 执行 JS，返回第一个 ok=True 的 result。"""
    last = None
    for fr in page.frames:
        try:
            last = fr.evaluate(expression)
            # 注意：{ok:false} 在 Python 里也是 truthy，必须看 ok
            if isinstance(last, dict) and last.get("ok"):
                return last
        except Exception:
            continue
    return last


def _js_handle_compare() -> str:
    return """() => {
  const root = document.body;
  if (!root) return {ok:false, reason:'no-body'};
  const has = (t) => (root.innerText || '').includes(t);
  if (!(has('比对结果') || has('已知晓该名称'))) return {ok:false, reason:'no-compare'};
  // 取文本最短且同时含关键文案的容器，避免点到整页
  const candidates = [...document.querySelectorAll('.el-dialog, .el-message-box, .el-overlay-dialog, [class*="dialog"], div')]
    .filter(n => {
      const t = n.innerText || '';
      const r = n.getBoundingClientRect();
      return t.includes('已知晓该名称') && t.includes('下一步') && n.offsetParent !== null
        && r.width > 280 && r.height > 180 && r.width < 1200;
    })
    .sort((a, b) => (a.innerText||'').length - (b.innerText||'').length);
  const dialog = candidates[0] || root;
  const info = {
    dialogClass: dialog.className || '',
    checkboxHtml: (dialog.querySelector('.el-checkbox, input[type=checkbox]') || {}).outerHTML || null,
  };
  let checked = 0;
  const clickEl = (el) => {
    if (!el) return;
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    if (typeof el.click === 'function') el.click();
  };
  // Element Plus: original + inner + label
  const originals = [...dialog.querySelectorAll('input.el-checkbox__original, input[type=checkbox]')];
  for (const inp of originals) {
    clickEl(inp);
    try { inp.checked = true; inp.dispatchEvent(new Event('change', {bubbles:true})); } catch (e) {}
    checked++;
  }
  const inners = [...dialog.querySelectorAll('.el-checkbox__inner')];
  for (const inner of inners) { clickEl(inner); checked++; }
  const labels = [...dialog.querySelectorAll('label.el-checkbox, .el-checkbox')];
  for (const lab of labels) {
    if ((lab.innerText || '').includes('已知晓') || labels.length === 1) clickEl(lab);
  }
  if (!checked) {
    const tip = [...dialog.querySelectorAll('*')].find(el =>
      ((el.childNodes||[]).length) && (el.textContent||'').trim().startsWith('已知晓该名称') && (el.innerText||'').length < 400);
    if (tip) {
      const box = tip.closest('label') || tip.previousElementSibling || tip.parentElement;
      clickEl(box);
      checked++;
    }
  }
  const isChecked = !!dialog.querySelector('.el-checkbox.is-checked, .el-checkbox__input.is-checked, input[type=checkbox]:checked');
  const btns = [...dialog.querySelectorAll('button, .el-button')].filter(b =>
    (b.innerText || '').replace(/\\s/g,'') === '下一步' && b.offsetParent !== null);
  let next = false;
  if (btns.length) {
    clickEl(btns[btns.length - 1]);
    next = true;
  }
  return {ok:true, checked, isChecked, next, info, btnCount: btns.length};
}"""


def _js_handle_notice() -> str:
    return """() => {
  const deepQueryAll = (selector) => {
    const out = [];
    const walk = (root) => {
      if (!root || !root.querySelectorAll) return;
      try { out.push(...root.querySelectorAll(selector)); } catch (e) {}
      let all = [];
      try { all = root.querySelectorAll('*'); } catch (e) { return; }
      for (const el of all) {
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    return out;
  };
  const clickEl = (el) => {
    if (!el) return;
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, composed:true, view:window}));
    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, composed:true, view:window}));
    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, composed:true, view:window}));
    if (typeof el.click === 'function') el.click();
  };
  const dialogs = deepQueryAll('.el-dialog, [role=dialog]');
  const dialog = dialogs.find(n => {
    const t = n.innerText || '';
    const r = n.getBoundingClientRect();
    return t.includes('告知书') && t.includes('承诺书') && t.includes('确定')
      && r.width > 280 && r.height > 180;
  }) || dialogs.find(n => {
    const t = n.innerText || '';
    const r = n.getBoundingClientRect();
    return t.includes('已详细阅读') && t.includes('确定') && r.height > 200;
  });
  if (!dialog) return {ok:false, reason:'no-notice-dialog', dialogCount: dialogs.length};
  const commitTab = [...dialog.querySelectorAll('.el-tabs__item, [role=tab], button, span, div')]
    .find(el => (el.innerText || '').trim() === '企业名称登记承诺书');
  if (commitTab) clickEl(commitTab);
  const labels = [...dialog.querySelectorAll('label.el-checkbox')].filter(lab => {
    const t = lab.innerText || '';
    return t.includes('告知书') || t.includes('承诺书') || t.includes('已详细阅读');
  });
  let n = 0;
  for (const lab of labels) {
    const inp = lab.querySelector('input.el-checkbox__original, input[type=checkbox]');
    const inner = lab.querySelector('.el-checkbox__inner');
    if (!lab.classList.contains('is-checked')) {
      clickEl(inner || inp || lab);
      if (inp) {
        try {
          inp.checked = true;
          inp.dispatchEvent(new Event('input', {bubbles:true}));
          inp.dispatchEvent(new Event('change', {bubbles:true}));
        } catch (e) {}
      }
    }
    n++;
  }
  const both = dialog.querySelectorAll('label.el-checkbox.is-checked').length >= 2;
  const btn = [...dialog.querySelectorAll('button.el-button--primary, button.el-button, button')]
    .find(b => {
      const t = (b.innerText || '').replace(/\\s/g,'');
      return (t === '确定' || t === '确 定') && !b.disabled && !b.classList.contains('is-disabled');
    });
  if (both && btn) {
    const r = btn.getBoundingClientRect();
    clickEl(btn);
    return {ok:true, checked:n, both, confirm:true, btnBox:{x:r.x,y:r.y,w:r.width,h:r.height}};
  }
  return {ok:true, checked:n, both, confirm:false};
}"""


def _js_handle_warm() -> str:
    return """() => {
  const roots = [document.querySelector('wujie-app'), document.body].filter(Boolean);
  const text = roots.map(r => r.innerText || '').join('\\n');
  if (!text.includes('我已知晓上述内容') && !text.includes('温馨提示')) return {ok:false};
  const nodes = [...document.querySelectorAll('button, .el-button, a, span, div')];
  const btn = nodes.find(b => {
    const t = (b.innerText || '').replace(/\\s/g,'');
    const r = b.getBoundingClientRect();
    return t === '我已知晓上述内容' && r.width > 40 && r.height > 16;
  });
  if (btn) {
    btn.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    if (typeof btn.click === 'function') btn.click();
    return {ok:true};
  }
  return {ok:false};
}"""


def _dismiss_system_busy(page: Page) -> bool:
    dismissed = False
    for scope in _all_scopes(page):
        try:
            if not (
                _frame_has_text(scope, "系统繁忙")
                or _frame_has_text(scope, "请稍后重试")
                or _frame_has_text(scope, "请稍后再试")
            ):
                continue
            dlg = scope.locator(".el-message-box, .el-dialog").filter(
                has_text=re.compile("系统繁忙|稍后")
            ).first
            btn = dlg.locator("button, .el-button").filter(has_text="确定").first if _visible(dlg) else None
            if _visible(btn):
                btn.click(force=True, timeout=3000)
                dismissed = True
            elif _click_text(scope, ["确定"], exact=True):
                dismissed = True
        except Exception:
            continue
    return dismissed


def handle_post_name_dialogs(page: Page, case_id: str) -> dict[str, Any]:
    """比对结果 → 告知/承诺 → 温馨提示。"""
    out: dict[str, Any] = {"compareNext": False, "noticeConfirm": False, "warmAck": False}
    out["framesDebug"] = _debug_frames(page)
    _shot(page, case_id, "dialogs-start")

    if _dialog_visible(page, "经办人信息") or _dialog_visible(page, "请上传身份证人像面"):
        out["skippedAlreadyBasic"] = True
        return out

    # 名称下一步偶发系统繁忙：先关掉再等比对
    if _dismiss_system_busy(page):
        out["dismissedBusy"] = True
        page.wait_for_timeout(1500)
        _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(1200)

    # 1) 比对结果（放宽等待；检测失败也尝试直接点）
    saw_compare = _wait_text(
        page, ["比对结果", "计算机比对通过", "已知晓该名称", "已知晓该名称存在审核"], timeout_sec=20
    )
    out["sawCompare"] = saw_compare
    # 检测失败时仍尝试（弹窗常在 iframe，文案扫描可能漏检）
    if True:
        _shot(page, case_id, "dialog-compare")
        for attempt in range(6):
            res = _eval_all_frames(page, _js_handle_compare())
            out["compareJs"] = res
            checked = bool(isinstance(res, dict) and (res.get("isChecked") or res.get("checked")))
            for fr in page.frames:
                try:
                    # 不依赖 .el-dialog class，用文案过滤可见容器
                    dlg = fr.locator("div").filter(has_text="比对结果").filter(has_text="已知晓该名称").first
                    if not _visible(dlg, timeout=500):
                        dlg = fr.locator(".el-dialog, .el-message-box").filter(has_text="已知晓该名称").first
                    if not _visible(dlg, timeout=500):
                        continue
                    for sel in (
                        "input[type='checkbox']",
                        "input.el-checkbox__original",
                        ".el-checkbox__inner",
                        "label.el-checkbox",
                        ".el-checkbox",
                    ):
                        try:
                            el = dlg.locator(sel).first
                            if el.count() == 0:
                                continue
                            if sel.startswith("input"):
                                try:
                                    el.check(force=True, timeout=1500)
                                except Exception:
                                    el.click(force=True, timeout=1500)
                            else:
                                el.click(force=True, timeout=1500)
                            checked = True
                        except Exception:
                            continue
                    tip = dlg.get_by_text("已知晓该名称存在审核不通过", exact=False).first
                    if not _visible(tip):
                        tip = dlg.get_by_text("已知晓该名称", exact=False).first
                    if _visible(tip):
                        box = tip.bounding_box()
                        if box:
                            page.mouse.click(box["x"] - 24, box["y"] + 12)
                            checked = True
                    # 弹层下一步
                    btn = dlg.locator("button, .el-button").filter(has_text="下一步").last
                    if _visible(btn):
                        btn.click(timeout=4000)
                        out["compareNext"] = True
                    break
                except Exception:
                    continue
            out["compareChecked"] = checked
            if isinstance(res, dict) and res.get("next"):
                out["compareNext"] = True
            page.wait_for_timeout(900)
            _shot(page, case_id, f"after-compare-try-{attempt}")
            if out["compareNext"] and not _dialog_visible(page, "比对结果"):
                break
            # 若已进入告知书也算过了比对
            if _dialog_visible(page, "已详细阅读") or _dialog_visible(page, "企业名称登记告知书"):
                out["compareNext"] = True
                break
            if _dialog_visible(page, "经办人信息") or _dialog_visible(page, "请上传身份证人像面"):
                out["compareNext"] = True
                break
        page.wait_for_timeout(800)
        _shot(page, case_id, "after-compare-next")

    # 2) 告知书 / 承诺书（两勾 + 确定关闭弹窗才算成功）
    if _wait_text(page, ["企业名称登记告知书", "已详细阅读", "承诺书"], timeout_sec=15) or _notice_dialog_visible(
        page
    ):
        _shot(page, case_id, "dialog-notice")
        notice = _force_dismiss_notice(page, case_id)
        out["noticeDetail"] = notice
        out["noticeJs"] = notice.get("js")
        out["noticeCheckedCount"] = notice.get("checked")
        out["noticeConfirm"] = bool(notice.get("ok"))
        page.wait_for_timeout(800)

    # 3) 温馨提示
    if _wait_text(page, ["温馨提示", "我已知晓上述内容"], timeout_sec=15):
        _shot(page, case_id, "dialog-warm")
        for _ in range(4):
            res = _eval_all_frames(page, _js_handle_warm())
            out["warmJs"] = res
            out["warmAck"] = bool(isinstance(res, dict) and res.get("ok"))
            if not out["warmAck"]:
                out["warmAck"] = _click_text_any(page, ["我已知晓上述内容"], exact=False)
            page.wait_for_timeout(800)
            if out["warmAck"] or _dialog_visible(page, "经办人信息") or _dialog_visible(page, "请上传身份证人像面"):
                out["warmAck"] = True
                break

    if not out["warmAck"]:
        out["warmAck"] = _click_text_any(page, ["我已知晓上述内容"], exact=False)
        if not out["warmAck"]:
            res = _eval_all_frames(page, _js_handle_warm())
            out["warmAck"] = bool(isinstance(res, dict) and res.get("ok"))

    _shot(page, case_id, "after-post-name-dialogs")
    return out


def _frame_looks_basic(fr: Frame) -> bool:
    """真正进入设立基本信息，排除未挂载的 wujie 空壳。"""
    try:
        body = (_body(fr).inner_text(timeout=800) or "")
    except Exception:
        body = ""
    if "doesn't work properly without JavaScript" in body:
        return False
    if "Welcome to nginx" in body:
        return False
    markers = ("经办人信息", "请上传身份证人像面", "企业基本信息", "申请机关")
    if any(m in body for m in markers):
        return True
    try:
        if fr.get_by_text("请上传身份证人像面", exact=False).count() > 0:
            return True
        if fr.get_by_text("经办人信息", exact=False).count() > 0:
            return True
        if fr.get_by_text("申请机关", exact=False).count() > 0:
            return True
    except Exception:
        pass
    return False


def _frame_basic(page: Page, timeout_ms: int = 60000) -> Frame | None:
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        # 比对/告知弹窗仍在时先关掉再进基本信息
        if _dialog_visible(page, "比对结果"):
            _eval_all_frames(page, _js_handle_compare())
            page.wait_for_timeout(500)
            continue
        if _notice_dialog_visible(page):
            _eval_all_frames(page, _js_handle_notice())
            page.wait_for_timeout(600)
            continue
        if _dialog_visible(page, "我已知晓上述内容") or _dialog_visible(page, "温馨提示"):
            _click_text_any(page, ["我已知晓上述内容"], exact=False)
            _eval_all_frames(page, _js_handle_warm())
            page.wait_for_timeout(800)
        # 优先有真实内容的 web-reg-apply
        for fr in page.frames:
            url = fr.url or ""
            name = fr.name or ""
            if ("web-reg-apply" in url or name == "web-reg-apply") and _frame_looks_basic(fr):
                return fr
        for fr in page.frames:
            if _frame_looks_basic(fr):
                return fr
        page.wait_for_timeout(500)
    return None


def _fill_label(frame: Frame, label: str, value: str) -> bool:
    if not value:
        return False
    item = frame.locator(".el-form-item").filter(has_text=re.compile(label)).first
    if not _visible(item):
        return False
    inp = item.locator(
        "input.el-input__inner, textarea.el-textarea__inner, input[type='text']"
    ).first
    if not _visible(inp):
        return False
    try:
        inp.click(timeout=3000)
        inp.fill("")
        inp.fill(value, timeout=5000)
        inp.dispatch_event("input")
        inp.dispatch_event("change")
        return True
    except Exception:
        return False


def _pick_select(frame: Frame, label: str, options: list[str]) -> str | None:
    item = frame.locator(".el-form-item").filter(has_text=re.compile(label)).first
    if not _visible(item):
        return None
    try:
        sel = item.locator(".el-select").first
        if _visible(sel):
            sel.click(timeout=3000)
        else:
            item.locator("input.el-input__inner").first.click(timeout=3000)
    except Exception:
        return None
    frame.page.wait_for_timeout(450)
    for opt in options:
        for scope in (frame, frame.page):
            try:
                loc = scope.locator(
                    ".el-select-dropdown:visible .el-select-dropdown__item"
                ).filter(has_text=opt).first
                if _visible(loc):
                    loc.click(timeout=4000)
                    frame.page.wait_for_timeout(300)
                    return opt
            except Exception:
                pass
            try:
                loc = scope.get_by_text(opt, exact=True).first
                if _visible(loc):
                    loc.click(timeout=4000)
                    return opt
            except Exception:
                pass
    return None


def _upload_id(frame: Frame, hint: str, path: Path, index: int) -> dict[str, Any]:
    out: dict[str, Any] = {"hint": hint, "path": str(path), "ok": False}
    if not path.exists():
        out["error"] = "file_missing"
        return out
    file_input = None
    try:
        zone = frame.locator(
            f"xpath=//*[contains(normalize-space(.),'{hint}')]"
            f"/ancestor::*[contains(@class,'el-upload') or contains(@class,'upload') "
            f"or contains(@class,'el-form-item') or self::div][1]"
        ).first
        if zone.count():
            cand = zone.locator("input[type='file']").first
            if cand.count():
                file_input = cand
    except Exception:
        pass
    if file_input is None or file_input.count() == 0:
        files = frame.locator("input[type='file']")
        if files.count() > index:
            file_input = files.nth(index)
    if file_input is None or file_input.count() == 0:
        # 点击虚线上传区
        try:
            box = frame.get_by_text(hint, exact=False).first
            if _visible(box):
                box.click(timeout=3000)
                frame.page.wait_for_timeout(300)
                files = frame.locator("input[type='file']")
                if files.count() > index:
                    file_input = files.nth(index)
        except Exception as e:
            out["error"] = str(e)
    if file_input is None or file_input.count() == 0:
        out["error"] = "input_not_found"
        return out
    try:
        file_input.set_input_files(str(path.resolve()))
        frame.page.wait_for_timeout(1200)
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


def _read_agent_ocr_fields(frame: Frame) -> tuple[str, str]:
    name_val, id_val = "", ""
    try:
        name_val = (
            frame.locator(".el-form-item")
            .filter(has_text="经办人姓名")
            .locator("input")
            .first.input_value(timeout=800)
            or ""
        ).strip()
    except Exception:
        pass
    try:
        id_val = (
            frame.locator(".el-form-item")
            .filter(has_text="证件号码")
            .locator("input")
            .first.input_value(timeout=800)
            or ""
        ).strip()
    except Exception:
        pass
    return name_val, id_val


def _ocr_busy(frame: Frame) -> bool:
    """页面仍在识别/加载时不要填后续字段。"""
    busy_markers = (
        "识别中",
        "正在识别",
        "OCR",
        "读取中",
        "上传中",
        "请稍候",
        "请稍后",
    )
    try:
        body = _body(frame).inner_text(timeout=600) or ""
    except Exception:
        body = ""
    if any(m in body for m in busy_markers):
        return True
    try:
        if frame.locator(".el-loading-mask:visible, .el-icon-loading:visible").count() > 0:
            return True
    except Exception:
        pass
    return False


def _wait_ocr(frame: Frame, timeout_sec: float = 90) -> dict[str, Any]:
    """上传身份证后必须等 OCR 回填姓名+证件号，再继续填其他项。"""
    info: dict[str, Any] = {"ok": False, "name": "", "idNo": ""}
    # 给上传预览/服务端一点启动时间
    frame.page.wait_for_timeout(1500)
    end = time.time() + timeout_sec
    stable_hits = 0
    while time.time() < end:
        if _ocr_busy(frame):
            stable_hits = 0
            frame.page.wait_for_timeout(800)
            continue
        name_val, id_val = _read_agent_ocr_fields(frame)
        info["name"], info["idNo"] = name_val, id_val
        # 证件号至少 15 位；姓名非空
        if name_val and len(re.sub(r"\s+", "", id_val)) >= 15:
            stable_hits += 1
            # 连续两次读到，避免识别过程中半成品
            if stable_hits >= 2:
                info["ok"] = True
                return info
        else:
            stable_hits = 0
        frame.page.wait_for_timeout(900)
    return info


def _district_from_payload(payload: dict[str, Any]) -> str:
    reg = payload.get("registration") or {}
    addr = reg.get("address") or {}
    for raw in (reg.get("district"), addr.get("district"), addr.get("detail"), reg.get("authority")):
        text = str(raw or "").strip()
        if not text:
            continue
        m = re.search(r"([\u4e00-\u9fff]{1,6}区)", text)
        if m:
            return m.group(1)
        if text.endswith("区") and 1 < len(text) <= 8:
            return text
    return ""


def _authority_candidates(payload: dict[str, Any]) -> list[str]:
    reg = payload.get("registration") or {}
    explicit = str(reg.get("authority") or "").strip()
    district = _district_from_payload(payload)
    out: list[str] = []
    if explicit:
        out.append(explicit)
    if district:
        out.extend(
            [
                f"{district}市场监督管理局",
                f"{district}市场监管局",
                district,
            ]
        )
    # 兜底：仍保留虹口，避免无区时完全无法选
    for x in ("虹口区市场监督管理局", "虹口区市场监管局", "虹口区"):
        if x not in out:
            out.append(x)
    return out


def _parse_shanghai_address(detail: str) -> dict[str, str]:
    """从详细地址尽量拆出街道/路/号/室；解析失败则返回空字段。"""
    text = re.sub(r"\s+", "", (detail or "").strip())
    out = {"street": "", "road": "", "number": "", "room": ""}
    if not text:
        return out
    m = re.search(
        r"(?:上海市)?(?:[\u4e00-\u9fff]+区)?"
        r"(?P<street>[\u4e00-\u9fff]+(?:街道|镇|乡))?"
        r"(?P<road>[\u4e00-\u9fff0-9]+(?:路|街|道|巷|弄))?"
        r"(?P<number>\d+号)?"
        r"(?P<room>.+)?$",
        text,
    )
    if not m:
        return out
    out["street"] = (m.group("street") or "").strip()
    out["road"] = (m.group("road") or "").strip()
    num = (m.group("number") or "").strip()
    out["number"] = num.replace("号", "") if num else ""
    room = (m.group("room") or "").strip()
    out["room"] = room
    return out


def _fill_address_dialog(
    page: Page, frame: Frame, payload: dict[str, Any], case_id: str
) -> dict[str, Any]:
    reg = payload.get("registration") or {}
    addr = reg.get("address") or {}
    result: dict[str, Any] = {"opened": False, "ok": False}

    # 点住所输入框打开弹窗
    opened = False
    item = frame.locator(".el-form-item").filter(has_text=re.compile("住所")).first
    try:
        if _visible(item):
            inp = item.locator("input, textarea").first
            if _visible(inp):
                inp.click(timeout=4000)
                opened = True
    except Exception:
        pass
    if not opened:
        opened = _click_text(frame, ["住所"], exact=False)
    page.wait_for_timeout(800)

    # 找住所填报弹层
    dialog = None
    for scope in _all_scopes(page):
        d = scope.locator(".el-dialog:visible, .el-drawer:visible").filter(has_text="住所填报").first
        if _visible(d):
            dialog = d
            break
        d = scope.locator(".el-dialog:visible").filter(has_text="邮政编码").first
        if _visible(d):
            dialog = d
            break
    if dialog is None:
        result["error"] = "住所填报弹窗未出现"
        return result
    result["opened"] = True
    _shot(page, case_id, "address-dialog")

    detail = str(addr.get("detail") or "").strip()
    parsed = _parse_shanghai_address(detail)
    # payload 有 detail 则优先用解析结果；解析不全时回落原演示默认（虹口四川北路）
    street = parsed["street"] or "四川北路街道"
    road = parsed["road"] or "四川北路"
    number = parsed["number"] or "1318"
    room = parsed["room"] or "4楼"
    postal = (addr.get("postalCode") or "200081").strip()
    result["detailSource"] = detail or "(fallback)"
    result["parsed"] = parsed

    # 街道/乡镇
    try:
        st = dialog.locator(".el-select").filter(has_text="街道").first
        if not _visible(st):
            # 点含「请选择」的第二个下拉（区后面）
            selects = dialog.locator(".el-select")
            if selects.count() >= 2:
                selects.nth(1).click(timeout=3000)
            else:
                dialog.get_by_text("街道", exact=False).first.click(timeout=3000)
        else:
            st.click(timeout=3000)
        page.wait_for_timeout(400)
        for scope in (dialog, page, frame):
            opt = scope.locator(".el-select-dropdown:visible .el-select-dropdown__item").filter(
                has_text=street
            ).first
            if _visible(opt):
                opt.click(timeout=3000)
                result["street"] = street
                break
            opt2 = scope.get_by_text(street, exact=True).first
            if _visible(opt2):
                opt2.click(timeout=3000)
                result["street"] = street
                break
    except Exception as e:
        result["streetError"] = str(e)

    page.wait_for_timeout(400)

    # 路名
    try:
        road_inp = dialog.get_by_placeholder("请输入").first
        # 更稳：路名旁输入
        inputs = dialog.locator("input.el-input__inner:visible")
        # 常见布局：区、街道是 select，随后路/弄/号/幢/室
        filled_road = False
        for i in range(min(inputs.count(), 12)):
            inp = inputs.nth(i)
            ph = (inp.get_attribute("placeholder") or "")
            # 跳过日期/邮编等
            if "邮" in ph or "不动产权" in ph:
                continue
            # 尝试填第一个空文本框为路名
            try:
                val = inp.input_value(timeout=300)
            except Exception:
                val = ""
            if not val and not filled_road:
                inp.click(timeout=2000)
                inp.fill(road)
                page.wait_for_timeout(600)
                # 点建议路名
                for scope in (page, frame, dialog):
                    sug = scope.get_by_text(road, exact=True).first
                    if _visible(sug):
                        sug.click(timeout=3000)
                        break
                filled_road = True
                result["road"] = road
                break
        if not filled_road and _visible(road_inp):
            road_inp.fill(road)
            result["road"] = road
    except Exception as e:
        result["roadError"] = str(e)

    page.wait_for_timeout(400)

    # 号 / 室：按后缀 label 找前一个 input
    def _fill_before_label(suffix: str, value: str) -> bool:
        try:
            lab = dialog.locator(f"xpath=//*[normalize-space()='{suffix}']").first
            if not _visible(lab):
                return False
            inp = lab.locator("xpath=preceding::input[1]")
            if inp.count() == 0:
                return False
            inp.first.click(timeout=2000)
            inp.first.fill(value)
            return True
        except Exception:
            return False

    result["number"] = _fill_before_label("号", number) or _fill_before_label("号", f"{number}号")
    result["room"] = _fill_before_label("室", room) or _fill_before_label("室", f"{room}室")

    # 邮编
    try:
        postal_item = dialog.locator(".el-form-item").filter(has_text="邮政编码").first
        if _visible(postal_item):
            postal_item.locator("input").first.fill(postal)
            result["postal"] = postal
        else:
            pin = dialog.get_by_placeholder("邮政", exact=False).first
            if _visible(pin):
                pin.fill(postal)
                result["postal"] = postal
            else:
                # 含 200081 模式的空 input
                for i in range(dialog.locator("input.el-input__inner").count()):
                    inp = dialog.locator("input.el-input__inner").nth(i)
                    ph = inp.get_attribute("placeholder") or ""
                    if "邮" in ph or "编码" in ph:
                        inp.fill(postal)
                        result["postal"] = postal
                        break
    except Exception as e:
        result["postalError"] = str(e)

    # 生成地址（可选）
    _click_text(dialog, ["生成地址"], exact=False)
    page.wait_for_timeout(400)

    result["ok"] = _click_text(dialog, ["确定", "确 定"], exact=False)
    if not result["ok"]:
        result["ok"] = _click_text_any(page, ["确定"], exact=True)
    page.wait_for_timeout(800)
    return result


def _is_scope_plugin_url(url: str) -> bool:
    u = url or ""
    return "jyfwyun.com" in u or "/plugin/search" in u


def _is_scope_tab_url(url: str) -> bool:
    u = url or ""
    return _is_scope_plugin_url(u) or "sourceType=STRD" in u or "flag=trd" in u or "search-trd" in u


def _scope_ui_ready(scope: Page | Frame) -> bool:
    """仅认定经营范围插件 UI；勿把设立页/master 壳页当经营范围页。"""
    url = getattr(scope, "url", "") or ""
    if _is_scope_plugin_url(url):
        return True
    # 设立页「尚未添加经营范围」绝不是插件页
    markers = (
        "请输入搜索关键字",
        "已添加条目数",
        "经营范围规范表述",
        "规范表述查询",
        "生成结果",
        "选好了",
        "推荐的主营业务",
    )
    try:
        body = _body(scope).inner_text(timeout=800) or ""
    except Exception:
        body = ""
    if "尚未添加经营范围" in body and "请输入搜索关键字" not in body and "已添加条目数" not in body:
        return False
    if "doesn't work properly without JavaScript" in body or "Welcome to nginx" in body:
        return False
    if any(m in body for m in markers):
        return True
    for m in markers:
        try:
            if scope.get_by_text(m, exact=False).count() > 0:
                return True
        except Exception:
            continue
    try:
        if scope.locator(
            "input[placeholder*='搜索关键字'], input[placeholder*='请输入搜索']"
        ).count() > 0:
            return True
    except Exception:
        pass
    return False


def _find_scope_plugin_frame(page: Page) -> Page | Frame | None:
    """优先返回 jyfwyun 业务帧；没有则返回已就绪的插件 UI。"""
    for p in page.context.pages:
        if p.is_closed():
            continue
        for fr in p.frames:
            if _is_scope_plugin_url(fr.url or "") and _scope_ui_ready(fr):
                return fr
        for fr in p.frames:
            if _is_scope_plugin_url(fr.url or ""):
                return fr
    for p in page.context.pages:
        if p.is_closed():
            continue
        if _is_scope_plugin_url(p.url or "") and _scope_ui_ready(p):
            return p
        for fr in p.frames:
            if _scope_ui_ready(fr):
                return fr
        if _is_scope_tab_url(p.url or "") and _scope_ui_ready(p):
            return p
    return None


def _find_scope_host(page: Page) -> Page | Frame | None:
    return _find_scope_plugin_frame(page)


def _find_scope_tabs(page: Page) -> list[Page]:
    out: list[Page] = []
    for p in page.context.pages:
        if p.is_closed() or p == page:
            continue
        if _is_scope_tab_url(p.url or ""):
            out.append(p)
    return out


def _close_scope_tabs(page: Page) -> int:
    closed = 0
    for p in list(page.context.pages):
        if p.is_closed() or p == page:
            continue
        if not _is_scope_tab_url(p.url or ""):
            continue
        try:
            p.close()
            closed += 1
        except Exception:
            pass
    try:
        page.bring_to_front()
    except Exception:
        pass
    return closed


def _wait_basic_stable(page: Page, timeout_sec: float = 20) -> Frame | None:
    """暂存后页面常短暂空白，等待经营范围区域回来。"""
    end = time.time() + timeout_sec
    while time.time() < end:
        fr = _frame_basic(page, timeout_ms=2000)
        if fr is None:
            page.wait_for_timeout(400)
            continue
        try:
            body = _body(fr).inner_text(timeout=800) or ""
        except Exception:
            body = ""
        if len(body.strip()) < 40:
            page.wait_for_timeout(400)
            continue
        if "选择经营范围" in body or "尚未添加经营范围" in body or "经营范围" in body:
            return fr
        page.wait_for_timeout(400)
    return _frame_basic(page, timeout_ms=5000)


def _fill_business_scope_flow(
    page: Page, frame: Frame, case_id: str, industry: str = "科技"
) -> dict[str, Any]:
    """按用户截图路径选择经营范围。industry 用于搜索与主营一致的科技类条目。"""
    result: dict[str, Any] = {"ok": False, "opened": False}
    keyword = (industry or "科技").strip() or "科技"

    # 暂存后可能白屏，先稳住再点
    stable = _wait_basic_stable(page, timeout_sec=18)
    if stable is not None:
        frame = stable

    def _click_open_scope(fr: Frame) -> bool:
        # 已有经营范围页则复用，禁止再点一次开第二个 master 窗口
        if _find_scope_tabs(page) or _find_scope_plugin_frame(page) is not None:
            result["reusedScopeTab"] = True
            return True
        try:
            fr.get_by_text("经营范围", exact=True).first.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            try:
                fr.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                page.mouse.wheel(0, 2400)
        page.wait_for_timeout(400)
        # 只用精确入口，避免宽泛 div.has_text 点到整块容器误导航
        candidates = [
            fr.locator(".yd-tips.normal, .yd-tips, .business-scope .yd-tips").filter(
                has_text=re.compile(r"选择经营范围")
            ).first,
            fr.get_by_text("+ 选择经营范围 >>", exact=True).first,
            fr.get_by_text("选择经营范围 >>", exact=True).first,
            fr.get_by_text("选择经营范围", exact=True).first,
            fr.locator("a,button,span").filter(has_text=re.compile(r"^\s*\+?\s*选择经营范围")).first,
        ]
        before_pages = list(page.context.pages)
        for loc in candidates:
            if not _visible(loc, timeout=1200):
                continue
            try:
                loc.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            try:
                with page.context.expect_page(timeout=6000) as ni:
                    loc.click(timeout=5000)
                try:
                    np = ni.value
                    np.wait_for_load_state("domcontentloaded", timeout=15000)
                    result["newPageUrl"] = np.url
                    # 若误开成门户/核名壳（非经营范围 STRD），立刻关掉，避免看起来像重跑全流程
                    u = np.url or ""
                    if not _is_scope_tab_url(u) and (
                        "portal_yct" in u
                        or "name-declaration" in u
                        or ("web-yct-master" in u and "STRD" not in u and "trd" not in u.lower())
                    ):
                        try:
                            np.close()
                        except Exception:
                            pass
                        result["closedAccidentalPortal"] = True
                        continue
                except Exception:
                    pass
                return True
            except Exception:
                try:
                    loc.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    if _find_scope_tabs(page) or _find_scope_plugin_frame(page) is not None:
                        return True
                    if len(page.context.pages) > len(before_pages):
                        # 新开了页但还没识别到插件：仍算点开成功，后面专等 jyfwyun
                        tabs = [p for p in page.context.pages if p not in before_pages and not p.is_closed()]
                        if tabs:
                            result["newPageUrl"] = tabs[0].url
                        return True
                    for scope in _all_scopes(page):
                        dlg = scope.locator(
                            ".el-dialog:visible, .el-drawer:visible"
                        ).filter(has_text=re.compile("经营范围规范|请输入搜索关键字|选好了")).first
                        if _visible(dlg):
                            return True
                except Exception:
                    continue
        return False

    host = None
    # 最多点开一次；后续只等插件帧，禁止反复 expect_page 连开多个 master
    for attempt in range(3):
        if _find_scope_plugin_frame(page) is not None:
            host = _find_scope_plugin_frame(page)
            break
        fr = _wait_basic_stable(page, timeout_sec=10) or frame
        if not _find_scope_tabs(page):
            clicked = _click_open_scope(fr)
            result[f"clicked{attempt}"] = clicked
        else:
            result[f"clicked{attempt}"] = "reuse"
        page.wait_for_timeout(800)
        end = time.time() + 18
        while time.time() < end:
            # 只前置经营范围相关页，不要把其它标签全拉到前台
            for p in _find_scope_tabs(page):
                try:
                    p.bring_to_front()
                except Exception:
                    pass
            host = _find_scope_plugin_frame(page)
            if host is not None:
                break
            # 「跳过」只点在已识别的插件帧/STRD 页里，绝不点设立页/核名壳
            for p in _find_scope_tabs(page):
                for fr2 in p.frames:
                    if _is_scope_plugin_url(fr2.url or "") or _scope_ui_ready(fr2):
                        if _click_text(fr2, ["跳过"], exact=True):
                            result["skippedOnWait"] = True
                if _scope_ui_ready(p) and _click_text(p, ["跳过"], exact=True):
                    result["skippedOnWait"] = True
            page.wait_for_timeout(500)
        if host is not None:
            break
        result[f"openTry{attempt}"] = {
            "pages": [((p.url or "")[:120]) for p in page.context.pages],
        }
        _shot(page, case_id, f"scope-open-miss-{attempt}")
    result["opened"] = host is not None
    if host is None:
        result["closedScopeTabs"] = _close_scope_tabs(page)
        _shot(page, case_id, "scope-opened")
        result["error"] = "未打开经营范围选择页"
        return result

    def _scope_page() -> Page:
        tabs = _find_scope_tabs(page)
        if tabs:
            return tabs[0]
        if isinstance(host, Page):
            return host
        try:
            return host.page  # type: ignore[attr-defined]
        except Exception:
            return page

    def _scope_work_frame(spage: Page) -> Page | Frame | None:
        """真实经营范围 UI 只在 jyfwyun；未就绪时返回 None，禁止在 master 空壳上乱点。"""
        plugin = _find_scope_plugin_frame(page)
        if plugin is not None:
            return plugin
        for fr in spage.frames:
            if _is_scope_plugin_url(fr.url or ""):
                return fr
        for p in spage.context.pages:
            if p.is_closed():
                continue
            if _is_scope_plugin_url(p.url or ""):
                return p
            for fr in p.frames:
                if _is_scope_plugin_url(fr.url or ""):
                    return fr
        return None

    def _shot_host(name: str) -> None:
        sp = _scope_page()
        try:
            sp.bring_to_front()
        except Exception:
            pass
        path = store.case_dir(case_id) / "screenshots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sp.screenshot(path=str(path), full_page=False)
        except Exception:
            _shot(page, case_id, name)

    def _added_count(spage: Page | Frame) -> int:
        """读取「已添加条目数: N条」（优先业务帧）。"""
        scopes: list = []
        try:
            scopes = _scope_scopes()
        except Exception:
            scopes = [spage]
            if hasattr(spage, "frames"):
                scopes = [spage] + list(spage.frames)
        for scope in scopes:
            try:
                txt = _body(scope).inner_text(timeout=1200) or ""
            except Exception:
                try:
                    txt = scope.evaluate("() => document.body ? document.body.innerText : ''") or ""
                except Exception:
                    txt = ""
            m = re.search(r"已添加条目数\s*[:：]?\s*(\d+)\s*条", txt)
            if m:
                return int(m.group(1))
            try:
                loc = scope.get_by_text(re.compile(r"已添加条目数\s*[:：]?\s*\d+\s*条"), exact=False).first
                if loc.count() > 0:
                    t = loc.inner_text(timeout=500) or ""
                    m2 = re.search(r"(\d+)\s*条", t)
                    if m2:
                        return int(m2.group(1))
            except Exception:
                pass
            try:
                n = scope.get_by_text("删除", exact=True).count()
                if n > 0 and "已选条目" in txt:
                    return n
            except Exception:
                pass
        return 0

    def _js_add_scope_items() -> str:
        return """(limit = 5) => {
          const deepAll = (selector) => {
            const out = [];
            const walk = (root) => {
              if (!root || !root.querySelectorAll) return;
              try { out.push(...root.querySelectorAll(selector)); } catch (e) {}
              let all = [];
              try { all = root.querySelectorAll('*'); } catch (e) { return; }
              for (const el of all) { if (el.shadowRoot) walk(el.shadowRoot); }
            };
            walk(document);
            return out;
          };
          const clickEl = (el) => {
            el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, composed:true, view:window}));
            el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, composed:true, view:window}));
            el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, composed:true, view:window}));
            if (typeof el.click === 'function') el.click();
          };
          const prefer = ['M1002', 'M2070', '工程和技术研究和试验发展', '技术服务', '科技中介服务', '电竞信息科技'];
          const avoid = ['前置许可', '小额贷款'];
          const addBtns = deepAll('button, a, span, div').filter(el => {
            const t = (el.innerText || '').trim();
            const r = el.getBoundingClientRect();
            const norm = t.replace(/\\s+/g,'').replace(/\\u00a0/g,'');
            return norm === '添加' && r.width > 20 && r.height > 12 && r.x < (window.innerWidth * 0.72);
          });
          const cards = [];
          for (const btn of addBtns) {
            let card = btn.parentElement;
            for (let i = 0; i < 8 && card; i++) {
              const t = card.innerText || '';
              if (t.includes('一般事项') || t.includes('前置许可') || /[A-Z]\\d{3,}/.test(t)) break;
              card = card.parentElement;
            }
            const text = (card && card.innerText) || '';
            cards.push({btn, text, avoid: avoid.some(a => text.includes(a))});
          }
          // 优先：非前置许可 + 含 prefer 关键词/码；带「主」优先
          cards.sort((a, b) => {
            const score = (c) => {
              let s = 0;
              if (c.text.includes('主')) s += 50;
              if (c.text.includes('一般事项')) s += 20;
              if (c.avoid) s -= 100;
              for (let i = 0; i < prefer.length; i++) if (c.text.includes(prefer[i])) s += 40 - i;
              return s;
            };
            return score(b) - score(a);
          });
          let n = 0;
          const picked = [];
          for (const c of cards) {
            if (c.avoid) continue;
            clickEl(c.btn);
            picked.push((c.text || '').split('\\n')[0].slice(0, 40));
            n++;
            if (n >= limit) break;
          }
          return {ok: n > 0, n, picked, btnCount: addBtns.length};
        }"""

    def _add_top_n_from_search(spage: Page, n: int = 5) -> dict[str, Any]:
        """搜索结果左栏按可见顺序点前 n 个「添 加」（跳过前置许可/小额贷款）。"""
        before = _added_count(spage)
        info: dict[str, Any] = {
            "before": before,
            "clicked": 0,
            "added": 0,
            "after": before,
            "picked": [],
            "skipped": [],
            "target": n,
        }
        try:
            scopes = _scope_scopes()
        except Exception:
            scopes = [spage] + list(getattr(spage, "frames", []))

        # 已跳过的前置许可标题，避免反复点同一项
        skipped_titles: set[str] = set()

        for _ in range(n + 6):
            if info["added"] >= n:
                break
            progressed = False
            for scope in scopes:
                btns = scope.locator("button, a, span").filter(has_text=_ADD_BTN_RE)
                try:
                    bcnt = min(btns.count(), 20)
                except Exception:
                    bcnt = 0
                if bcnt == 0:
                    # JS 兜底枚举左栏添加按钮
                    try:
                        js_res = scope.evaluate(
                            """() => {
                              const deepAll = (selector) => {
                                const out = [];
                                const walk = (root) => {
                                  if (!root || !root.querySelectorAll) return;
                                  try { out.push(...root.querySelectorAll(selector)); } catch (e) {}
                                  let all = [];
                                  try { all = root.querySelectorAll('*'); } catch (e) { return; }
                                  for (const el of all) { if (el.shadowRoot) walk(el.shadowRoot); }
                                };
                                walk(document);
                                return out;
                              };
                              return deepAll('button, a, span, div').filter(el => {
                                const t = (el.innerText || '').trim().replace(/\\s+/g,'').replace(/\\u00a0/g,'');
                                const r = el.getBoundingClientRect();
                                return t === '添加' && r.width > 20 && r.height > 12 && r.x < (window.innerWidth * 0.72);
                              }).length;
                            }"""
                        )
                        info["jsBtnCount"] = js_res
                    except Exception:
                        pass
                for bi in range(bcnt):
                    el = btns.nth(bi)
                    try:
                        if not el.is_visible(timeout=500):
                            continue
                        box = el.bounding_box()
                        if not box or box["x"] > 1000:
                            continue
                        try:
                            parent_txt = el.locator(
                                "xpath=ancestor::div[.//text()[contains(.,'一般事项') or contains(.,'前置许可')]][1]"
                            ).inner_text(timeout=500) or ""
                        except Exception:
                            parent_txt = ""
                        title = (parent_txt.split("\n")[0] or "").strip()[:50]
                        if "小额贷款" in parent_txt or (
                            "前置许可" in parent_txt and "一般事项" not in parent_txt
                        ):
                            if title and title not in skipped_titles:
                                skipped_titles.add(title)
                                info["skipped"].append(title)
                            continue
                        if title and title in skipped_titles:
                            continue
                        # 已在已选中的不重复点
                        if title and any(title[:12] in (p or "") for p in info["picked"]):
                            continue
                        cur = info["after"]
                        try:
                            el.scroll_into_view_if_needed(timeout=1000)
                        except Exception:
                            pass
                        el.click(force=True, timeout=3000)
                        info["clicked"] += 1
                        page.wait_for_timeout(800)
                        try:
                            _handle_naics_modal()
                        except Exception:
                            pass
                        after = _added_count(spage)
                        info["after"] = after
                        if after > cur:
                            info["added"] += 1
                            info["picked"].append(title or f"item-{info['added']}")
                            progressed = True
                            break
                        # 点击无效果：用坐标再点一次
                        try:
                            spage.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                            page.wait_for_timeout(700)
                            try:
                                _handle_naics_modal()
                            except Exception:
                                pass
                            after = _added_count(spage)
                            info["after"] = after
                            if after > cur:
                                info["added"] += 1
                                info["picked"].append(title or f"item-{info['added']}")
                                progressed = True
                                break
                        except Exception:
                            pass
                    except Exception:
                        continue
                if progressed:
                    break
            if not progressed:
                page.wait_for_timeout(500)
                try:
                    _handle_naics_modal()
                except Exception:
                    pass
                # 连续两轮无进展才停，尽量凑满前 5 个可添加结果
                info["stall"] = int(info.get("stall") or 0) + 1
                if info["stall"] >= 2:
                    break
            else:
                info["stall"] = 0
        info["after"] = _added_count(spage)
        return info

    def _add_verified(spage: Page, limit: int = 5, min_total: int = 2) -> dict[str, Any]:
        """兜底：按偏好码/名称补齐条目。"""
        before = _added_count(spage)
        info: dict[str, Any] = {"before": before, "clicked": 0, "after": before, "picked": []}
        prefer_codes = ("M1002", "M2070", "M3041")
        prefer_names = (
            "工程和技术研究和试验发展",
            "技术服务、技术开发、技术咨询",
            "技术服务",
            "科技中介服务",
            "科学研究",
            "电竞信息科技",
        )
        try:
            scopes = _scope_scopes()
        except Exception:
            scopes = [spage] + list(getattr(spage, "frames", []))

        def _click_add_near(scope, marker: str) -> bool:
            loc = scope.get_by_text(marker, exact=False).first
            if loc.count() == 0:
                return False
            try:
                if not loc.is_visible(timeout=1000):
                    return False
            except Exception:
                return False
            for xpath in (
                "xpath=ancestor::div[.//span[contains(normalize-space(.),'添') and contains(normalize-space(.),'加')] or .//button[contains(normalize-space(.),'添')]][1]",
                "xpath=ancestor::li[.//span[contains(normalize-space(.),'添')] or .//button[contains(normalize-space(.),'添')]][1]",
                "xpath=ancestor::*[contains(.,'添') and contains(.,'加')][1]",
            ):
                try:
                    card = loc.locator(xpath)
                    if card.count() == 0:
                        continue
                    ctxt = card.first.inner_text(timeout=600) or ""
                    if "小额贷款" in ctxt or ("前置许可" in ctxt and "一般事项" not in ctxt):
                        return False
                    btn = card.first.get_by_role("button", name=_ADD_BTN_ROLE_RE).first
                    if btn.count() == 0:
                        btn = card.first.locator("button, a, span").filter(has_text=_ADD_BTN_RE).first
                    if btn.count() > 0:
                        btn.click(force=True, timeout=3000)
                        return True
                except Exception:
                    continue
            return False

        for marker in prefer_codes + prefer_names:
            if info["after"] >= min_total or info["clicked"] >= limit:
                break
            for scope in scopes:
                try:
                    cur = info["after"]
                    if not _click_add_near(scope, marker):
                        continue
                    info["clicked"] += 1
                    info["picked"].append(marker)
                    page.wait_for_timeout(700)
                    try:
                        _handle_naics_modal()
                    except Exception:
                        pass
                    info["after"] = _added_count(spage)
                    if info["after"] > cur:
                        break
                except Exception:
                    continue
        if info["after"] < min_total:
            for scope in scopes:
                try:
                    js_res = scope.evaluate(_js_add_scope_items(), max(1, min_total - info["after"]))
                    info["js"] = js_res
                    page.wait_for_timeout(800)
                    try:
                        _handle_naics_modal()
                    except Exception:
                        pass
                    info["after"] = _added_count(spage)
                except Exception as e:
                    info["jsError"] = str(e)
        info["after"] = _added_count(spage)
        return info

    sp = _scope_page()
    try:
        sp.bring_to_front()
        sp.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(800)
    # 等 jyfwyun 业务帧挂载；未就绪绝不在 master 空壳上操作
    wf: Page | Frame | None = None
    for _ in range(40):
        wf = _scope_work_frame(sp)
        if wf is None:
            page.wait_for_timeout(400)
            continue
        try:
            snip = (_body(wf).inner_text(timeout=600) or "")[:120]
        except Exception:
            snip = ""
        if snip and "doesn't work properly without JavaScript" not in snip:
            if any(x in snip for x in ("生成结果", "选好了", "已添加", "经营范围", "搜索", "规范表述")):
                break
            if _is_scope_plugin_url(getattr(wf, "url", "") or ""):
                # 插件域名已挂上，即使文案稍慢也可用
                page.wait_for_timeout(600)
                break
        page.wait_for_timeout(400)
    if wf is None:
        result["error"] = "经营范围插件帧未就绪（避免在 master 壳页误操作）"
        result["closedScopeTabs"] = _close_scope_tabs(page)
        _shot(page, case_id, "scope-plugin-missing")
        return result
    result["scopeWorkUrl"] = getattr(wf, "url", "") or ""
    result["scopeWorkName"] = getattr(wf, "name", "") or ""
    _shot_host("scope-opened")

    def _scope_scopes() -> list:
        """只操作 jyfwyun 业务帧，避免空壳/nginx/核名壳干扰。"""
        primary = _scope_work_frame(sp)
        if primary is None:
            return []
        u = getattr(primary, "url", "") or ""
        if _is_scope_plugin_url(u) or _scope_ui_ready(primary):
            return [primary]
        for fr in sp.frames:
            if _is_scope_plugin_url(fr.url or ""):
                return [fr]
        return [primary] if _scope_ui_ready(primary) else []

    # 跳过引导（只在经营范围插件帧）
    for scope in _scope_scopes():
        if _click_text(scope, ["跳过"], exact=True):
            result["skipped"] = True
            page.wait_for_timeout(700)

    def _handle_naics_modal() -> dict[str, Any]:
        """处理「国民经济行业分类选择」：点选对应行业后弹窗才会消失，否则生成结果无法回填。"""
        prefer = (
            "7519",
            "其他技术推广服务",
            "7512",
            "生物技术推广服务",
            "7515",
            "新能源技术推广服务",
            "7516",
            "环保技术推广服务",
            "7513",
            "新材料技术推广服务",
            "7514",
            "节能技术推广服务",
        )
        info: dict[str, Any] = {"seen": False, "picked": None}
        js = """(prefer) => {
          const deepAll = (selector) => {
            const out = [];
            const walk = (root) => {
              if (!root || !root.querySelectorAll) return;
              try { out.push(...root.querySelectorAll(selector)); } catch (e) {}
              let all = [];
              try { all = root.querySelectorAll('*'); } catch (e) { return; }
              for (const el of all) { if (el.shadowRoot) walk(el.shadowRoot); }
            };
            walk(document);
            return out;
          };
          const clickEl = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return false;
            const x = r.left + r.width/2, y = r.top + r.height/2;
            for (const type of ['pointerdown','mousedown','mouseup','pointerup','click']) {
              el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, composed:true, clientX:x, clientY:y, view:window}));
            }
            if (typeof el.click === 'function') el.click();
            return true;
          };
          const bodyText = (document.body && document.body.innerText) || '';
          if (!bodyText.includes('国民经济行业分类')) {
            return {ok:false, reason:'no-modal', snippet: bodyText.slice(0,120)};
          }
          const labels = deepAll('label, li, div, span, p').filter(el => {
            const t = (el.innerText || '').replace(/\\s+/g,'');
            const r = el.getBoundingClientRect();
            return r.width > 40 && r.height > 14 && r.height < 80 && /\\(\\d{4}\\)/.test(t) && t.length < 80;
          });
          const scored = labels.map(el => {
            const t = (el.innerText || '').replace(/\\s+/g,'');
            let score = 0;
            for (let i = 0; i < prefer.length; i++) {
              if (t.includes(String(prefer[i]).replace(/\\s+/g,''))) score += 100 - i;
            }
            if (t.includes('其他技术推广')) score += 50;
            return {el, t, score};
          }).filter(x => x.score > 0).sort((a,b) => b.score - a.score);
          let target = scored[0] && scored[0].el;
          if (!target && labels.length) target = labels[labels.length - 1];
          if (!target) return {ok:false, reason:'no-option', labelCount: labels.length};
          // 优先点内部 radio
          const radio = target.querySelector && (target.querySelector('input[type=radio]') || target.querySelector('.el-radio__inner'));
          clickEl(radio || target);
          return {ok:true, picked: (target.innerText||'').replace(/\\s+/g,' ').trim().slice(0,60), labelCount: labels.length};
        }"""
        for scope in _scope_scopes():
            try:
                if scope.get_by_text("国民经济行业分类", exact=False).count() == 0:
                    continue
                info["seen"] = True
            except Exception:
                continue
            try:
                js_res = scope.evaluate(js, list(prefer))
                info["js"] = js_res
                if isinstance(js_res, dict) and js_res.get("ok"):
                    info["picked"] = js_res.get("picked")
                    page.wait_for_timeout(800)
                    break
            except Exception as e:
                info["jsError"] = str(e)
            # Playwright 兜底：按优先文案点 radio/label
            for marker in prefer:
                try:
                    loc = scope.get_by_text(marker, exact=False).first
                    if loc.count() == 0 or not loc.is_visible(timeout=600):
                        continue
                    # 点 radio 圆点或整行
                    radio = loc.locator(
                        "xpath=ancestor-or-self::*[.//input[@type='radio'] or contains(@class,'el-radio')][1]"
                    ).first
                    if radio.count() > 0:
                        radio.click(force=True, timeout=2500)
                    else:
                        loc.click(force=True, timeout=2500)
                    info["picked"] = marker
                    page.wait_for_timeout(800)
                    break
                except Exception:
                    continue
            if info.get("picked"):
                break
        # 确认类按钮（若有）
        if info.get("seen"):
            for scope in _scope_scopes():
                for txt in ("确定", "确认", "保存", "完成"):
                    try:
                        btn = scope.get_by_role("button", name=re.compile(rf"^\s*{txt}\s*$")).first
                        if btn.count() > 0 and btn.is_visible(timeout=400):
                            btn.click(force=True, timeout=2000)
                            info["confirmed"] = txt
                            page.wait_for_timeout(500)
                            break
                    except Exception:
                        continue
        return info

    def _js_handle_recommend_modal() -> str:
        return """() => {
          const deepAll = (selector) => {
            const out = [];
            const walk = (root) => {
              if (!root || !root.querySelectorAll) return;
              try { out.push(...root.querySelectorAll(selector)); } catch (e) {}
              let all = [];
              try { all = root.querySelectorAll('*'); } catch (e) { return; }
              for (const el of all) { if (el.shadowRoot) walk(el.shadowRoot); }
            };
            walk(document);
            return out;
          };
          const clickEl = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) return false;
            const x = r.left + r.width/2, y = r.top + r.height/2;
            for (const type of ['pointerdown','mousedown','mouseup','pointerup','click']) {
              el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, composed:true, clientX:x, clientY:y, view:window}));
            }
            if (typeof el.click === 'function') el.click();
            return true;
          };
          const nodes = deepAll('*');
          const done = nodes.find(el => {
            const t = (el.innerText || '').replace(/\\s/g,'');
            const r = el.getBoundingClientRect();
            return t === '选好了' && r.width > 40 && r.height > 16 && r.width < 400;
          });
          if (!done) {
            const any = (document.body && document.body.innerText) || '';
            return {ok:false, reason:'no-选好了', textSnippet: any.slice(0,200), nodeCount: nodes.length};
          }
          let root = done;
          for (let i = 0; i < 16 && root; i++) {
            const t = (root.innerText || '').replace(/\\s+/g,'').replace(/\\u00a0/g,'');
            if (t.includes('选好了') && t.includes('添加') && (t.includes('推荐') || t.includes('科技') || t.includes('主营'))) break;
            root = root.parentElement;
          }
          root = root || document.body;
          // 弹层内所有可见「添加」（文本尽量短，避免点到整卡）
          const addBtns = deepAll('button,a,span,div').filter(el => {
            if (!root.contains(el) && root !== document.body) {
              // root 可能跨 shadow，放宽：只要在选好了附近纵向范围内
            }
            const t = (el.innerText || '').trim();
            const r = el.getBoundingClientRect();
            const dr = done.getBoundingClientRect();
            const nearModal = r.top < dr.top && r.bottom > dr.top - 520 && Math.abs(r.left - dr.left) < 700;
            const norm = t.replace(/\\s+/g,'').replace(/\\u00a0/g,'');
            return (norm === '添加' || norm === '+添加') && r.width >= 36 && r.width < 160 && r.height >= 18 && nearModal;
          });
          // 优先含 M1002 / M2070 / 技术服务 的卡片上的添加
          const scored = addBtns.map(btn => {
            let p = btn, text = '';
            for (let i = 0; i < 6 && p; i++) { text = p.innerText || text; p = p.parentElement; }
            let score = 0;
            if (text.includes('M1002') || text.includes('工程和技术研究')) score += 50;
            if (text.includes('M2070') || text.includes('技术服务')) score += 40;
            if (text.includes('主')) score += 10;
            if (text.includes('前置许可')) score -= 100;
            return {btn, score, title: text.split('\\n')[0].slice(0,40)};
          }).sort((a,b) => b.score - a.score);
          let added = 0;
          const picked = [];
          for (const c of scored) {
            if (c.score < 0) continue;
            if (clickEl(c.btn)) {
              added++;
              picked.push(c.title || '添加');
            }
            if (added >= 2) break;
          }
          // 没点到优先项时，点弹层内前两个添加
          if (added === 0) {
            for (const btn of addBtns) {
              if (clickEl(btn)) { added++; picked.push('添加'); }
              if (added >= 2) break;
            }
          }
          if (added === 0) {
            return {ok:false, reason:'no-add-clicked', addBtnCount: addBtns.length, done:false};
          }
          clickEl(done);
          return {ok:true, added, picked, done:true, addBtnCount: addBtns.length};
        }"""

    # 等推荐弹窗出现（行业「科技」会自动弹出）
    found_rec = False
    end_rec = time.time() + 15
    while time.time() < end_rec:
        found_rec = False
        for scope in _scope_scopes():
            try:
                if scope.get_by_text("选好了", exact=True).count() > 0:
                    found_rec = True
                    break
                if scope.get_by_text("推荐的主营业务", exact=False).count() > 0:
                    found_rec = True
                    break
            except Exception:
                pass
        if found_rec:
            break
        # JS 探测（含 shadow）
        for scope in _scope_scopes():
            try:
                hit = scope.evaluate(
                    """() => {
                      const walk = (root, acc=[]) => {
                        if (!root) return acc;
                        try { if ((root.innerText||'').includes('选好了')) acc.push(true); } catch(e){}
                        const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                        for (const el of all) if (el.shadowRoot) walk(el.shadowRoot, acc);
                        return acc;
                      };
                      return walk(document).length > 0;
                    }"""
                )
                if hit:
                    found_rec = True
                    break
            except Exception:
                continue
        if found_rec:
            break
        page.wait_for_timeout(400)
    result["sawRecommendModal"] = bool(found_rec)

    # 推荐弹窗：仅当真有「选好了/推荐的主营业务」时处理（避免把国民经济行业分类弹窗误当成推荐）
    added_rec = 0
    if not found_rec:
        # 二次确认可见文案，避免 count>0 但不可见的假阳性
        for scope in _scope_scopes():
            try:
                if _visible(scope.get_by_text("选好了", exact=True).first, timeout=400):
                    found_rec = True
                    break
                if _visible(scope.get_by_text("推荐的主营业务", exact=False).first, timeout=400):
                    found_rec = True
                    break
            except Exception:
                continue
        result["sawRecommendModal"] = bool(found_rec)

    for attempt in range(8 if found_rec else 0):
        js_last = None
        for scope in _scope_scopes():
            try:
                js_last = scope.evaluate(_js_handle_recommend_modal())
                if isinstance(js_last, dict) and js_last.get("ok") and int(js_last.get("added") or 0) > 0:
                    added_rec += int(js_last.get("added") or 0)
                    result["recommendJs"] = js_last
                    result["recommendDone"] = True
                    break
                if isinstance(js_last, dict):
                    result["recommendJs"] = js_last
            except Exception as e:
                js_last = {"ok": False, "error": str(e)}
                result["recommendJs"] = js_last

        # Playwright：弹层内先加后关；未加到条目绝不点「选好了」
        before_cnt = _added_count(sp)
        for scope in _scope_scopes():
            try:
                for title in (
                    "技术服务、技术开发、技术咨询、技术交流、技术转让、技术推广",
                    "工程和技术研究和试验发展",
                ):
                    loc = scope.get_by_text(title, exact=False).first
                    if loc.count() == 0:
                        continue
                    try:
                        parent = loc.locator(
                            "xpath=ancestor::div[.//button or .//span[contains(normalize-space(.),'添')]][1]"
                        )
                        btn = parent.get_by_role("button", name=_ADD_BTN_ROLE_RE).first
                        if btn.count() == 0:
                            btn = parent.locator("button, a, span").filter(has_text=_ADD_BTN_RE).first
                        if btn.count() > 0 and btn.is_visible(timeout=800):
                            btn.click(force=True, timeout=3000)
                            added_rec += 1
                            page.wait_for_timeout(500)
                            continue
                    except Exception:
                        pass
                    box = loc.bounding_box()
                    if box:
                        # 卡片右侧蓝色「添加」
                        sp.mouse.click(min(box["x"] + box["width"] + 120, box["x"] + 520), box["y"] + 28)
                        added_rec += 1
                        page.wait_for_timeout(500)
                after_cnt = _added_count(sp)
                if after_cnt > before_cnt or added_rec > 0:
                    done = scope.get_by_text("选好了", exact=True).first
                    if done.count() > 0:
                        try:
                            done.click(force=True, timeout=4000)
                        except Exception:
                            box = done.bounding_box()
                            if box:
                                sp.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        result["recommendDone"] = True
            except Exception:
                continue

        page.wait_for_timeout(900)
        _shot_host(f"scope-recommend-try-{attempt}")
        still = False
        for scope in _scope_scopes():
            try:
                if scope.get_by_text("推荐的主营业务", exact=False).count() > 0:
                    still = True
                    break
                if scope.get_by_text("选好了", exact=True).count() > 0 and scope.get_by_text(
                    "科技(", exact=False
                ).count() > 0:
                    still = True
                    break
            except Exception:
                continue
        # 仅当弹窗关闭且已有选中条目才算成功
        cnt_now = _added_count(sp)
        if not still and cnt_now > 0:
            result["recommendDone"] = True
            break
        if result.get("recommendDone") and not still and cnt_now > 0:
            break
        # 弹窗已关但没选中：退出推荐阶段，交给后面主列表添加
        if not still and attempt >= 2:
            result["recommendDone"] = False
            result["recommendClosedEmpty"] = True
            break
    result["recommendAdded"] = added_rec
    # 行业「科技」常直接带出主营条目并弹出国民经济行业分类，优先处理该弹窗
    naics0 = _handle_naics_modal()
    result["naicsAfterRecommend"] = naics0
    _shot_host("scope-after-recommend")

    scopes = _scope_scopes()
    # 即使已有主营推荐条目，仍搜行业词（默认「科技」）补齐与主营一致的科技类一般事项
    selected_after_rec = _added_count(sp)
    scopes = _scope_scopes()
    result["countAfterRecommend"] = selected_after_rec
    result["scopeKeyword"] = keyword

    searched = False
    for scope in scopes:
        try:
            # 避开仍含「选好了」的弹层内搜索框
            if _visible(scope.get_by_text("选好了", exact=True).first, timeout=300):
                if _visible(scope.get_by_text("推荐的主营业务", exact=False).first, timeout=200):
                    continue
            box = scope.get_by_placeholder("请输入搜索关键字", exact=False).first
            if not _visible(box):
                box = scope.locator("input[placeholder*='搜索'], input[placeholder*='关键字']").first
            if not _visible(box):
                continue
            box.click(timeout=3000)
            box.fill("")
            box.fill(keyword)
            page.wait_for_timeout(300)
            clicked_search = False
            for sel in (
                "button:has(i.el-icon-search)",
                ".el-input-group__append button",
                "button:has-text('搜索')",
                ".el-icon-search",
            ):
                btn = scope.locator(sel).first
                if _visible(btn):
                    btn.click(timeout=3000)
                    clicked_search = True
                    break
            if not clicked_search:
                box.press("Enter")
            searched = True
            page.wait_for_timeout(2000)
            break
        except Exception:
            continue
    result["searched"] = searched
    _shot_host(f"scope-search-{keyword}")

    # 搜索「科技」后：必须添加结果列表前 5 条
    add_info = _add_top_n_from_search(sp, n=5)
    if int(add_info.get("added") or 0) < 5:
        page.wait_for_timeout(800)
        add_info2 = _add_top_n_from_search(sp, n=5 - int(add_info.get("added") or 0))
        add_info = {
            **add_info,
            "retry": add_info2,
            "added": int(add_info.get("added") or 0) + int(add_info2.get("added") or 0),
            "after": add_info2.get("after", add_info.get("after")),
            "picked": list(add_info.get("picked") or []) + list(add_info2.get("picked") or []),
            "clicked": int(add_info.get("clicked") or 0) + int(add_info2.get("clicked") or 0),
        }
    if int(add_info.get("after") or 0) < max(5, selected_after_rec + 1):
        # 仍不足时按偏好码兜底补齐
        fill = _add_verified(sp, limit=5, min_total=5)
        add_info = {**add_info, "fallback": fill, "after": fill.get("after", add_info.get("after"))}
    result["addInfo"] = add_info
    result["addedTop5"] = int(add_info.get("added") or 0)
    result["selectedCount"] = int(add_info.get("after") or 0)
    # 添加后通常再次弹出国民经济行业分类
    naics1 = _handle_naics_modal()
    result["naicsAfterAdd"] = naics1
    _shot_host("scope-after-add")

    gen = False
    selected_n = int(add_info.get("after") or selected_after_rec or 0)
    if selected_n > 0:
        # 生成前再确保行业分类已选
        naics2 = _handle_naics_modal()
        result["naicsBeforeGenerate"] = naics2
        scopes = _scope_scopes()
        for scope in scopes:
            # 明确点右上角「生成结果」
            btn = scope.locator("button, a, span, div").filter(
                has_text=re.compile(r"^\s*生成结果\s*$")
            ).first
            if _visible(btn):
                try:
                    btn.click(force=True, timeout=4000)
                    gen = True
                    break
                except Exception:
                    pass
            if _click_text(scope, ["生成结果"], exact=True):
                gen = True
                break
        if not gen:
            # 只在经营范围相关页补点，禁止扫全部标签误点核名「下一步/生成」
            for p in _find_scope_tabs(page):
                plugin = _find_scope_plugin_frame(page)
                if plugin is not None and _click_text(plugin, ["生成结果"], exact=True):
                    gen = True
                    break
                if _click_text(p, ["生成结果"], exact=True):
                    gen = True
                    break
        page.wait_for_timeout(1500)
        # 生成后若又弹出行业分类，再选一次并重试生成
        naics3 = _handle_naics_modal()
        result["naicsAfterGenerate"] = naics3
        if naics3.get("picked"):
            for scope in _scope_scopes():
                if _click_text(scope, ["生成结果"], exact=True):
                    gen = True
                    page.wait_for_timeout(1200)
                    break
        # 仅点弹层内明确确认，避免误点「取消并退出」旁的其它按钮
        for scope in _scope_scopes():
            for txt in ("确定", "确认", "提交", "完成"):
                try:
                    btn = scope.get_by_role("button", name=re.compile(rf"^\s*{txt}\s*$")).first
                    if btn.count() > 0 and btn.is_visible(timeout=500):
                        btn.click(force=True, timeout=2500)
                        result["generateConfirm"] = txt
                        page.wait_for_timeout(800)
                        break
                except Exception:
                    continue
        # 等插件页关闭或回填完成
        end_wait = time.time() + 12
        while time.time() < end_wait:
            try:
                page.bring_to_front()
            except Exception:
                pass
            frame_chk = _frame_basic(page, timeout_ms=3000) or frame
            try:
                body_chk = _body(frame_chk).inner_text(timeout=1200) or ""
            except Exception:
                body_chk = ""
            if body_chk and "尚未添加经营范围" not in body_chk and "经营范围" in body_chk:
                break
            # 插件是否还在
            still_plugin = False
            for p in page.context.pages:
                u = p.url or ""
                if "jyfwyun.com" in u or "sourceType=STRD" in u or "flag=trd" in u:
                    still_plugin = True
                    break
            if not still_plugin:
                page.wait_for_timeout(800)
                break
            page.wait_for_timeout(600)
    else:
        result["generateSkipped"] = "未选中任何条目"
    result["generated"] = gen
    _shot_host("scope-after-generate")

    # 回到设立页核对
    try:
        page.bring_to_front()
    except Exception:
        pass
    page.wait_for_timeout(1500)
    frame2 = _frame_basic(page, timeout_ms=30000) or frame
    try:
        body = _body(frame2).inner_text(timeout=2000)
        result["stillEmpty"] = "尚未添加经营范围" in body
        result["ok"] = not result.get("stillEmpty", True)
        if not result["ok"] and selected_n > 0:
            # 末次补救：若插件仍开着，再选行业分类并生成
            for p in page.context.pages:
                u = p.url or ""
                if "jyfwyun.com" in u or "sourceType=STRD" in u or "flag=trd" in u:
                    try:
                        p.bring_to_front()
                    except Exception:
                        pass
                    result["naicsRescue"] = _handle_naics_modal()
                    for scope in _scope_scopes():
                        if _click_text(scope, ["生成结果"], exact=True):
                            result["generatedRescue"] = True
                            page.wait_for_timeout(2500)
                            break
                    break
            try:
                page.bring_to_front()
            except Exception:
                pass
            page.wait_for_timeout(1200)
            frame2 = _frame_basic(page, timeout_ms=15000) or frame2
            body = _body(frame2).inner_text(timeout=2000)
            result["stillEmpty"] = "尚未添加经营范围" in body
            result["ok"] = not result.get("stillEmpty", True)
    except Exception:
        result["ok"] = bool(gen and selected_n > 0)
    # 关掉经营范围标签，避免残留 master 壳页被后续步骤当成主流程
    result["closedScopeTabs"] = _close_scope_tabs(page)
    try:
        page.bring_to_front()
    except Exception:
        pass
    return result


def run_basic_info_flow(case_id: str, page: Page, payload: dict[str, Any]) -> dict[str, Any]:
    store.set_status(case_id, statuses.FILLING_ESTABLISHMENT, customer_message="正在处理核名后弹窗与基本信息…")

    dialogs = handle_post_name_dialogs(page, case_id)
    if _notice_dialog_visible(page):
        notice = _force_dismiss_notice(page, case_id)
        dialogs["noticeRetry"] = notice
        dialogs["noticeConfirm"] = bool(notice.get("ok"))
        _shot(page, case_id, "notice-retry-before-basic")

    if _dialog_visible(page, "我已知晓上述内容") or _dialog_visible(page, "温馨提示"):
        for _ in range(4):
            _eval_all_frames(page, _js_handle_warm())
            if _click_text_any(page, ["我已知晓上述内容"], exact=False):
                dialogs["warmAck"] = True
                break
            page.wait_for_timeout(600)

    if _notice_dialog_visible(page):
        _shot(page, case_id, "basic-blocked-by-notice")
        dialogs["framesDebugAfter"] = _debug_frames(page)
        return {"ok": False, "reason": "告知书弹窗未关闭", "dialogs": dialogs}

    frame = _frame_basic(page, timeout_ms=90000)
    if frame is None:
        _shot(page, case_id, "basic-frame-missing")
        dialogs["framesDebugAfter"] = _debug_frames(page)
        return {"ok": False, "reason": "未进入基本信息页", "dialogs": dialogs}

    _shot(page, case_id, "basic-info-page")
    result: dict[str, Any] = {"dialogs": dialogs, "ok": False}

    # 身份证：优先匹配法人姓名的已上传图
    lr = (payload.get("personnel") or {}).get("legalRepresentative") or {}
    front, back = store.resolve_id_card_images(
        case_id,
        payload,
        person_name=str(lr.get("name") or "").strip() or None,
        fallback_front=DEFAULT_ID_FRONT,
        fallback_back=DEFAULT_ID_BACK,
    )

    id_upload: dict[str, Any] = {
        "front": False,
        "back": False,
        "frontPath": str(front) if front else None,
        "backPath": str(back) if back else None,
    }
    if front:
        r = _upload_id(frame, "人像面", front, 0)
        id_upload["front"] = bool(r.get("ok"))
        id_upload["frontError"] = r.get("error")
        page.wait_for_timeout(1200)
    if back:
        r = _upload_id(frame, "国徽面", back, 1)
        id_upload["back"] = bool(r.get("ok"))
        id_upload["backError"] = r.get("error")
        page.wait_for_timeout(1200)
    result["idUpload"] = id_upload
    _shot(page, case_id, "id-uploaded")

    # 关键：双面传完后等 OCR 识别结束（姓名+证件号回填）再填后续
    ocr = _wait_ocr(frame, timeout_sec=90)
    result["ocr"] = ocr
    _shot(page, case_id, "id-ocr-done" if ocr.get("ok") else "id-ocr-timeout")
    if not ocr.get("ok"):
        # OCR 未完成时不抢填，避免把半成品表单弄乱
        result["ok"] = False
        result["missing"] = ["身份证OCR识别"]
        result["reason"] = "身份证上传后 OCR 未回填姓名/证件号，已停止后续填写"
        _dump(case_id, "fill-basic-info-result.json", result)
        return result

    mobile = "13984439335"
    inv0 = (payload.get("investors") or [{}])[0]
    lr = (payload.get("personnel") or {}).get("legalRepresentative") or {}
    mobile = lr.get("mobile") or inv0.get("mobile") or mobile

    result["mobile"] = _fill_label(frame, "移动电话", mobile)
    result["agentType"] = _pick_select(frame, "经办人类型", ["法定代表人"])
    page.wait_for_timeout(400)

    # 企业信息
    authority_opts = _authority_candidates(payload)
    result["authorityCandidates"] = authority_opts
    result["authority"] = _pick_select(frame, "申请机关", authority_opts)
    page.wait_for_timeout(500)
    # 受理点：选可见第一项
    try:
        acc = frame.get_by_placeholder("请选择受理点").first
        if _visible(acc):
            acc.click(timeout=3000)
            page.wait_for_timeout(400)
            first = page.locator(".el-select-dropdown:visible .el-select-dropdown__item").first
            if not _visible(first):
                first = frame.locator(".el-select-dropdown:visible .el-select-dropdown__item").first
            if _visible(first):
                first.click(timeout=3000)
                result["acceptPoint"] = True
    except Exception:
        result["acceptPoint"] = False

    result["companyType"] = _pick_select(
        frame,
        "企业类型",
        [
            "有限责任公司(自然人独资)",
            "有限责任公司（自然人独资）",
            "有限责任公司(自然人投资或控股)",
        ],
    )
    result["term"] = _pick_select(frame, "经营期限", ["不约定期限"])

    # 住所弹窗
    addr_result = _fill_address_dialog(page, frame, payload, case_id)
    _shot(page, case_id, "address-filled")
    result["address"] = addr_result

    result["contact"] = _fill_label(frame, "联系电话", mobile)

    today = date.today().isoformat()
    result["resolutionDate"] = _fill_label(frame, "章程决议日期", today)
    if not result["resolutionDate"]:
        try:
            di = frame.get_by_placeholder("请选择章程决议日期").first
            if _visible(di):
                di.click(timeout=3000)
                page.wait_for_timeout(400)
                cell = frame.locator("td.available.today, td.today").first
                if not _visible(cell):
                    cell = page.locator("td.available.today, td.today").first
                if _visible(cell):
                    cell.click(timeout=3000)
                    result["resolutionDate"] = True
                else:
                    di.fill(today)
                    result["resolutionDate"] = True
        except Exception:
            pass

    # 暂存（之后页面常短暂白屏，需等经营范围区恢复）
    result["saved"] = _click_text(frame, ["暂存"], exact=True)
    if not result["saved"]:
        result["saved"] = _click_text_any(page, ["暂存"], exact=True)
    page.wait_for_timeout(1200)
    frame = _wait_basic_stable(page, timeout_sec=25) or frame
    _shot(page, case_id, "after-draft-save")

    # 经营范围：按名称申报行业（默认科技）搜索并添加与主营一致的条目
    industry = ((payload.get("name") or {}).get("industry") or "科技").strip() or "科技"
    scope = _fill_business_scope_flow(page, frame, case_id, industry=industry)
    if not scope.get("ok"):
        # 若上次已开过经营范围页，先关掉再重试，避免叠多个 master 窗口
        _close_scope_tabs(page)
        frame = _frame_basic(page, timeout_ms=20000) or frame
        scope2 = _fill_business_scope_flow(page, frame, case_id, industry=industry)
        scope = {**scope, "retry": scope2}
        if scope2.get("ok"):
            scope = scope2
    result["businessScope"] = scope
    # 确保回到设立页（经营范围标签已关）
    _close_scope_tabs(page)
    frame = _frame_basic(page, timeout_ms=20000) or frame

    # 下拉到底 → 下一步（经营范围未填则不点，避免空提交）
    try:
        frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        try:
            page.mouse.wheel(0, 2000)
        except Exception:
            pass
    page.wait_for_timeout(500)
    if scope.get("ok"):
        result["nextClicked"] = _click_text(frame, ["下一步"], exact=True)
        if not result["nextClicked"]:
            result["nextClicked"] = _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(1500)
    else:
        result["nextClicked"] = False
        result["nextSkippedReason"] = "经营范围未完成"
    _shot(page, case_id, "basic-after-next")

    missing = []
    if not id_upload.get("front") or not id_upload.get("back"):
        missing.append("身份证上传")
    if not addr_result.get("ok"):
        missing.append("住所")
    if not scope.get("ok"):
        missing.append("经营范围")
    if not result.get("nextClicked"):
        missing.append("下一步")
    result["missing"] = missing
    result["ok"] = not missing
    _dump(case_id, "fill-basic-info-result.json", result)
    return result
