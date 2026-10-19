"""核名之后的基本信息板块（新增，不改核名代码）。

流程：
1. 比对结果：勾选「已知晓」→ 下一步
2. 告知书/承诺书：勾选两个「已详细阅读」→ 确定
3. 温馨提示：我已知晓上述内容
4. 经办人：上传身份证正反面 → OCR → 移动电话 / 经办人类型
5. 企业信息：申请机关 / 企业类型 / 经营期限 / 住所弹窗 / 联系电话 / 章程决议日期 → 暂存
6. 经营范围：按用户勾选条目名称搜索添加（无勾选时回退行业词前 5 条）→ 生成结果
   - 生成结果若弹出「国民经济行业分类选择」：多条则轮换添加顺序（第2条起、第1条置末，再第3条起…）后重试；
     仅 1 条时默认点选弹窗第一项类目
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


def _notice_ack_visible(page: Page) -> bool:
    """告知/承诺勾选文案仍可见。"""
    return _any_visible_phrase(page, "我已详细阅读并知晓")


_NOTICE_ACK_TEXTS = (
    "我已详细阅读并知晓《上海市企业名称登记告知书》内容",
    "我已详细阅读并知晓《上海市企业名称登记承诺书》内容",
)


def _find_notice_dialog(scope: Page | Frame):
    """定位告知书/承诺书弹层（用告知书特征，避免正文里偶然出现的「确定」字误匹配）。"""
    for sel in (".el-dialog", "[role='dialog']", ".el-overlay-dialog .el-dialog"):
        try:
            dlg = (
                scope.locator(sel)
                .filter(has_text="企业名称登记告知书")
                .filter(has_text="我已详细阅读")
                .first
            )
            if _visible(dlg, timeout=400):
                return dlg
        except Exception:
            continue
    for sel in (".el-dialog", "[role='dialog']"):
        try:
            dlg = scope.locator(sel).filter(has_text="我已详细阅读并知晓").first
            if _visible(dlg, timeout=400):
                return dlg
        except Exception:
            continue
    return None


def _notice_scope_and_dialog(page: Page):
    for scope in _all_scopes(page):
        dlg = _find_notice_dialog(scope)
        if dlg is not None:
            return scope, dlg
    return None, None


def _notice_dialog_visible(page: Page) -> bool:
    if _notice_ack_visible(page):
        return True
    return _notice_scope_and_dialog(page)[1] is not None


def _click_point(page: Page, box: dict[str, float], *, dx: float = 0, dy: float = 0) -> None:
    x = box["x"] + dx
    y = box["y"] + (box["height"] / 2 if dy == 0 else dy)
    page.mouse.click(x, y)


def _ack_label_checked(lab) -> bool:
    try:
        cls = lab.get_attribute("class") or ""
        if "is-checked" in cls:
            return True
    except Exception:
        pass
    try:
        return lab.locator(".el-checkbox__input.is-checked, input[type=checkbox]:checked").count() > 0
    except Exception:
        return False


def _ensure_notice_acks(page: Page, dlg) -> int:
    """在同一弹层内勾选两框：已勾的跳过，未勾的只点一次方框。"""
    checked = 0
    for text in _NOTICE_ACK_TEXTS:
        lab = dlg.locator("label.el-checkbox").filter(has_text=text).first
        if not _visible(lab, timeout=500):
            short = "告知书》内容" if "告知书" in text else "承诺书》内容"
            lab = dlg.locator("label.el-checkbox").filter(has_text=short).first
        if not _visible(lab, timeout=400):
            continue
        if _ack_label_checked(lab):
            checked += 1
            continue
        try:
            lab.locator(".el-checkbox__inner").first.click(timeout=1500)
        except Exception:
            box = lab.bounding_box()
            if box:
                _click_point(page, box, dx=8)
        page.wait_for_timeout(150)
        if _ack_label_checked(lab):
            checked += 1
        else:
            # Vue 受控：原生勾上
            try:
                inp = lab.locator("input.el-checkbox__original, input[type=checkbox]").first
                inp.evaluate(
                    """el => {
                      el.checked = true;
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      const wrap = el.closest('.el-checkbox');
                      if (wrap) {
                        wrap.classList.add('is-checked');
                        const inputWrap = wrap.querySelector('.el-checkbox__input');
                        if (inputWrap) inputWrap.classList.add('is-checked');
                      }
                    }"""
                )
                if _ack_label_checked(lab):
                    checked += 1
            except Exception:
                pass
    return checked


def _click_notice_confirm_now(page: Page, dlg, scope: Page | Frame | None = None) -> bool:
    """勾选后立刻点本弹层「确定」，不点取消/其它区域。"""
    roots = [dlg]
    if scope is not None:
        roots.append(scope)

    def _try_btn(btn) -> bool:
        if btn is None:
            return False
        try:
            if btn.count() == 0:
                return False
        except Exception:
            return False
        try:
            if not btn.is_visible(timeout=500):
                return False
        except Exception:
            return False
        # 禁用态也尝试强点（有的主题未解除 disabled class）
        try:
            btn.evaluate(
                """el => {
                  el.disabled = false;
                  el.classList.remove('is-disabled');
                  el.removeAttribute('disabled');
                  el.click();
                }"""
            )
            return True
        except Exception:
            pass
        box = None
        try:
            box = btn.bounding_box()
        except Exception:
            box = None
        if box and box.get("width", 0) > 2:
            _click_point(page, box, dx=box["width"] / 2)
            return True
        try:
            btn.click(timeout=2000, force=True)
            return True
        except Exception:
            return False

    confirm_re = re.compile(r"^\s*确\s*定\s*$")
    for root in roots:
        finders = (
            lambda r=root: r.locator(".el-dialog__footer button.el-button--primary").filter(
                has_text=confirm_re
            ).last,
            lambda r=root: r.locator(".dialog-footer button.el-button--primary, footer button.el-button--primary").filter(
                has_text=confirm_re
            ).last,
            lambda r=root: r.locator("button.el-button--primary").filter(has_text=confirm_re).last,
            lambda r=root: r.get_by_role("button", name=confirm_re).last,
            lambda r=root: r.locator("button").filter(has_text=confirm_re).last,
        )
        for finder in finders:
            try:
                if _try_btn(finder()):
                    return True
            except Exception:
                continue

    # 全 frame 兜底：与「取消」同级的「确定」
    for sc in _all_scopes(page):
        try:
            cancel = sc.locator("button").filter(has_text=re.compile(r"^\s*取\s*消\s*$")).last
            if cancel.count() == 0 or not cancel.is_visible(timeout=300):
                continue
            parent = cancel.locator("xpath=..")
            btn = parent.locator("button").filter(has_text=confirm_re).last
            if _try_btn(btn):
                return True
        except Exception:
            continue
    return False


def _dismiss_know_all_tip(page: Page) -> bool:
    """仅当出现校验提示时关掉，便于下一轮重新「勾选→确定」。"""
    if not _any_visible_phrase(page, "请确认已知晓所有内容", "已知晓所有内容"):
        return False
    for scope in _all_scopes(page):
        try:
            tip = scope.locator(".el-message-box").filter(has_text="已知晓所有内容").first
            if not _visible(tip, timeout=300):
                continue
            btn = tip.locator("button.el-button--primary").filter(has_text="确定").first
            if _visible(btn):
                box = btn.bounding_box()
                if box:
                    _click_point(page, box, dx=box["width"] / 2)
                else:
                    btn.click(timeout=1500)
                page.wait_for_timeout(300)
                return True
        except Exception:
            continue
    return False


def _force_dismiss_notice(page: Page, case_id: str) -> dict[str, Any]:
    """流程：勾选两框 → 立刻点确定（中间不点别处）。比对通过后主路径。"""
    info: dict[str, Any] = {"ok": False, "checked": 0, "clicks": 0}
    for attempt in range(12):
        if not _notice_dialog_visible(page):
            info["ok"] = True
            info["attempt"] = attempt
            return info

        # 若上次确定触发了校验提示，先关掉再按「勾选→确定」重来
        if _dismiss_know_all_tip(page):
            info["dismissedKnowAllTip"] = True

        # 优先 JS（ElementUI 在 iframe/传送门里对 Playwright 定位不稳）
        res = _eval_all_frames(page, _js_handle_notice())
        info["js"] = res
        if isinstance(res, dict):
            if res.get("checked"):
                info["checked"] = max(info["checked"], int(res.get("checked") or 0))
            if res.get("confirm") or res.get("tipDismissed"):
                info["clicks"] += 1
                page.wait_for_timeout(700)
                _shot(page, case_id, f"after-notice-try-{attempt}")
                if not _notice_ack_visible(page):
                    info["ok"] = True
                    info["attempt"] = attempt
                    return info
                continue

        scope, dlg = _notice_scope_and_dialog(page)
        if dlg is None:
            for scope2 in _all_scopes(page):
                try:
                    if not scope2.get_by_text("我已详细阅读并知晓", exact=False).count():
                        continue
                    dlg = scope2.locator("body")
                    scope = scope2
                    break
                except Exception:
                    continue
        if dlg is None:
            page.wait_for_timeout(400)
            continue

        checked = _ensure_notice_acks(page, dlg)
        info["checked"] = max(info["checked"], checked)
        # 勾满后立刻点确定；不点取消、不按 Esc
        if checked >= 2 or _ensure_notice_acks(page, dlg) >= 2:
            if _click_notice_confirm_now(page, dlg, scope):
                info["clicks"] += 1
            else:
                # 再跑一遍 JS 点确定
                res2 = _eval_all_frames(page, _js_handle_notice())
                info["js2"] = res2
                if isinstance(res2, dict) and res2.get("confirm"):
                    info["clicks"] += 1
        page.wait_for_timeout(700)
        _shot(page, case_id, f"after-notice-try-{attempt}")

        if not _notice_ack_visible(page):
            info["ok"] = True
            info["attempt"] = attempt
            return info
        if _any_visible_phrase(page, "温馨提示", "我已知晓上述内容", "经办人信息", "请上传身份证人像面"):
            if not _notice_ack_visible(page):
                info["ok"] = True
                info["attempt"] = attempt
                return info
        if attempt in (2, 5, 8):
            try:
                _dump(
                    case_id,
                    f"notice-probe-{attempt}.json",
                    {"checked": info["checked"], "clicks": info["clicks"], "attempt": attempt, "js": info.get("js")},
                )
            except Exception:
                pass
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


def _any_visible_phrase(page: Page, *texts: str) -> bool:
    """可见文案（避免已关闭弹窗残留 DOM 误判）。"""
    for scope in _all_scopes(page):
        for t in texts:
            if not t:
                continue
            try:
                loc = scope.get_by_text(t, exact=False)
                n = min(loc.count(), 10)
            except Exception:
                continue
            for i in range(n):
                try:
                    if loc.nth(i).is_visible(timeout=350):
                        return True
                except Exception:
                    continue
    return False


def _compare_pass_visible(page: Page) -> bool:
    """比对结果：计算机比对通过（需勾选已知晓→下一步）。"""
    if _any_visible_phrase(page, "计算机比对通过"):
        return True
    if _any_visible_phrase(page, "已知晓该名称存在") and _any_visible_phrase(page, "比对结果"):
        # 通过窗是「下一步」，失败备用字号是「继续申请」
        if not _any_visible_phrase(page, "继续申请", "备用字号1", "可进行备用字号"):
            return True
    return False


def _compare_fail_visible(page: Page) -> bool:
    """备用字号 / 比对未通过（可见弹窗）。通过弹窗优先。"""
    if _compare_pass_visible(page):
        return False
    if _any_visible_phrase(
        page, "备用字号1", "备用字号2", "可进行备用字号", "备用拟设名称"
    ):
        return True
    if _any_visible_phrase(page, "继续申请") and _any_visible_phrase(page, "返回修改"):
        return True
    if _any_visible_phrase(page, "计算机比对未通过"):
        return True
    if _any_visible_phrase(page, "比对未通过") and not _any_visible_phrase(page, "比对通过"):
        return True
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
  // 可见通过弹窗优先；有「计算机比对通过」时绝不当失败
  if (has('计算机比对通过') || has('已知晓该名称') || has('比对结果')) {
    /* continue */
  } else {
    return {ok:false, reason:'no-compare'};
  }
  if (has('备用字号1') || has('可进行备用字号') || (has('继续申请') && has('返回修改') && !has('计算机比对通过'))) {
    return {ok:false, reason:'beiyong-fail'};
  }
  // 已进入告知书则交由 notice 处理，绝不可回退到 document.body 乱点
  if ((root.innerText || '').includes('我已详细阅读并知晓') && (root.innerText || '').includes('企业名称登记告知书')) {
    return {ok:false, reason:'notice-dialog-open'};
  }
  const candidates = [...document.querySelectorAll('.el-dialog, .el-message-box, .el-overlay-dialog, [role=dialog]')]
    .filter(n => {
      const t = n.innerText || '';
      const r = n.getBoundingClientRect();
      const st = getComputedStyle(n);
      if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
      if (t.includes('我已详细阅读并知晓')) return false;
      const pass = t.includes('计算机比对通过') || (t.includes('已知晓该名称') && t.includes('下一步'));
      return pass && t.includes('下一步') && n.offsetParent !== null
        && r.width > 280 && r.height > 180 && r.width < 1200;
    })
    .sort((a, b) => (a.innerText||'').length - (b.innerText||'').length);
  if (!candidates.length) return {ok:false, reason:'no-compare-dialog'};
  const dialog = candidates[0];
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
  if (btns.length && isChecked) {
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
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity) !== 0;
  };
  const clickEl = (el) => {
    if (!el) return;
    el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, composed:true, view:window}));
    el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, composed:true, view:window}));
    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, composed:true, view:window}));
    if (typeof el.click === 'function') el.click();
  };
  const isChecked = (lab) => {
    if (!lab) return false;
    if ((lab.className || '').toString().includes('is-checked')) return true;
    const wrap = lab.querySelector && lab.querySelector('.el-checkbox__input.is-checked');
    if (wrap) return true;
    const inp = lab.querySelector && lab.querySelector('input[type=checkbox], input.el-checkbox__original');
    return !!(inp && inp.checked);
  };
  // 不依赖 dialog class：直接找两句勾选文案对应的 checkbox
  let labels = deepQueryAll('label.el-checkbox, label, .el-checkbox').filter(lab => {
    const t = (lab.innerText || '').replace(/\\s+/g, '');
    return visible(lab) && t.includes('我已详细阅读并知晓') && (t.includes('告知书') || t.includes('承诺书'));
  });
  // 去重：按文案
  const seen = new Set();
  labels = labels.filter(lab => {
    const t = (lab.innerText || '').replace(/\\s+/g, '').slice(0, 40);
    if (seen.has(t)) return false;
    seen.add(t);
    return true;
  });
  if (!labels.length) {
    // 再按短文案找，向上找 label
    const tips = deepQueryAll('span, div, p, label').filter(el => {
      const t = (el.innerText || '').trim();
      return visible(el) && t.includes('我已详细阅读并知晓') && t.length < 60;
    });
    for (const tip of tips) {
      let p = tip;
      for (let i = 0; i < 6 && p; i++) {
        if ((p.className || '').toString().includes('checkbox') || p.tagName === 'LABEL') {
          labels.push(p);
          break;
        }
        p = p.parentElement;
      }
    }
  }
  let n = 0;
  for (const lab of labels) {
    if (isChecked(lab)) { n++; continue; }
    const inner = lab.querySelector ? lab.querySelector('.el-checkbox__inner') : null;
    const inp = lab.querySelector ? lab.querySelector('input.el-checkbox__original, input[type=checkbox]') : null;
    // 只点一次，避免连点取消勾选；不伪造 is-checked
    clickEl(inner || lab);
    if (isChecked(lab)) n++;
  }
  const both = labels.length >= 2 && labels.every(isChecked);
  // 校验提示优先关掉
  const tipBox = deepQueryAll('.el-message-box, .el-dialog, [role=dialog]').find(d =>
    visible(d) && (d.innerText || '').includes('已知晓所有内容')
  );
  if (tipBox) {
    const tipBtn = [...tipBox.querySelectorAll('button')].find(b => {
      const t = (b.innerText || '').replace(/\\s/g,'');
      return visible(b) && (t === '确定' || t === '确 定');
    });
    if (tipBtn) clickEl(tipBtn);
    return {ok:true, checked:n, both, confirm:false, tipDismissed:true, labelCount: labels.length};
  }
  // 找确定按钮：优先靠近勾选文案的容器；兼容无 footer class
  const dialogs = deepQueryAll('.el-dialog, [role=dialog], .el-overlay-dialog, .el-dialog__wrapper');
  let dialog = dialogs.find(d => visible(d) && (d.innerText || '').includes('我已详细阅读') && ((d.innerText || '').includes('告知书') || (d.innerText || '').includes('承诺书')));
  if (!dialog && labels[0]) {
    let p = labels[0].parentElement;
    for (let i = 0; i < 14 && p; i++) {
      const t = p.innerText || '';
      if (t.includes('取消') && t.includes('告知书') && /确\\s*定/.test(t)) { dialog = p; break; }
      p = p.parentElement;
    }
  }
  const btnRoot = dialog || document.body;
  let btn = [...(btnRoot.querySelectorAll ? btnRoot.querySelectorAll('button.el-button--primary, button.el-button, button') : [])]
    .find(b => {
      const t = (b.innerText || '').replace(/\\s/g,'');
      return visible(b) && (t === '确定' || t === '确 定');
    });
  // 与取消同级兜底
  if (!btn) {
    const cancel = [...(btnRoot.querySelectorAll ? btnRoot.querySelectorAll('button') : [])]
      .find(b => visible(b) && (b.innerText || '').replace(/\\s/g,'') === '取消');
    if (cancel && cancel.parentElement) {
      btn = [...cancel.parentElement.querySelectorAll('button')]
        .find(b => visible(b) && (b.innerText || '').replace(/\\s/g,'') === '确定');
    }
  }
  // 两勾后点确定；必要时解除 disabled
  if ((both || n >= 2) && btn) {
    try {
      btn.disabled = false;
      btn.classList.remove('is-disabled');
      btn.removeAttribute('disabled');
    } catch (e) {}
    clickEl(btn);
    return {ok:true, checked:n, both, confirm:true, labelCount: labels.length, dialogClass: (dialog && dialog.className) || ''};
  }
  return {ok:n > 0, checked:n, both, confirm:false, labelCount: labels.length, dialogCount: dialogs.length, hasBtn: !!btn};
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
    from apps.worker.playbooks.retry_gate import dismiss_system_busy

    return dismiss_system_busy(page)


def handle_post_name_dialogs(page: Page, case_id: str) -> dict[str, Any]:
    """比对结果 → 告知/承诺 → 温馨提示。"""
    out: dict[str, Any] = {"compareNext": False, "noticeConfirm": False, "warmAck": False}
    out["framesDebug"] = _debug_frames(page)
    _shot(page, case_id, "dialogs-start")

    if _dialog_visible(page, "经办人信息") or _dialog_visible(page, "请上传身份证人像面"):
        out["skippedAlreadyBasic"] = True
        return out

    # 已到告知书：跳过比对等待，直接勾选确定（与核名通过后路径一致）
    already_notice = _notice_dialog_visible(page)
    out["alreadyNotice"] = already_notice

    # 名称下一步偶发系统繁忙：先关掉再等比对（比对结果在时不要点页面下一步）
    if (not already_notice) and _dismiss_system_busy(page):
        out["dismissedBusy"] = True
        page.wait_for_timeout(1500)
        if not (
            _dialog_visible(page, "比对结果")
            or _dialog_visible(page, "已知晓该名称")
            or _notice_dialog_visible(page)
        ):
            _click_text_any(page, ["下一步"], exact=True)
            page.wait_for_timeout(1200)

    # 1) 比对结果（放宽等待；检测失败也尝试直接点）
    if not already_notice and not _notice_dialog_visible(page):
        saw_compare = _wait_text(
            page,
            [
                "比对结果",
                "计算机比对通过",
                "已知晓该名称",
                "已知晓该名称存在审核",
                "企业名称登记告知书",
                "我已详细阅读",
            ],
            timeout_sec=20,
        )
        out["sawCompare"] = saw_compare
        # 等待中途已弹出告知书：转交 notice
        if _notice_dialog_visible(page):
            out["compareNext"] = True
        # 仅当「可见」失败弹窗时交回换字号；残留 DOM「备用字号」文案不得盖过「计算机比对通过」
        elif _compare_fail_visible(page) and not _compare_pass_visible(page):
            out["compareFailed"] = True
            _shot(page, case_id, "dialog-compare-or-alt-failed")
            return out
        else:
            _shot(page, case_id, "dialog-compare")
            for attempt in range(8):
                if _notice_dialog_visible(page):
                    out["compareNext"] = True
                    break
                # 中途若真正弹出备用字号：交回换字号，勿点继续申请
                if _compare_fail_visible(page) and not _compare_pass_visible(page):
                    out["compareFailed"] = True
                    _shot(page, case_id, "dialog-compare-or-alt-failed")
                    return out
                res = _eval_all_frames(page, _js_handle_compare())
                out["compareJs"] = res
                checked = bool(
                    isinstance(res, dict)
                    and (res.get("isChecked") or res.get("checked") or res.get("next"))
                )
                for fr in page.frames:
                    try:
                        if _notice_dialog_visible(page):
                            out["compareNext"] = True
                            break
                        # 通过弹窗：比对结果 + 已知晓；也兼容仅「计算机比对通过」
                        dlg = fr.locator(".el-dialog, .el-message-box, [role='dialog']").filter(
                            has_text="计算机比对通过"
                        ).first
                        if not _visible(dlg, timeout=400):
                            dlg = fr.locator("div").filter(has_text="比对结果").filter(
                                has_text="已知晓该名称"
                            ).first
                        if not _visible(dlg, timeout=400):
                            dlg = fr.locator(".el-dialog, .el-message-box").filter(
                                has_text="已知晓该名称"
                            ).first
                        if not _visible(dlg, timeout=500):
                            continue
                        # 勾选「已知晓」
                        for sel in (
                            "label.el-checkbox",
                            ".el-checkbox__inner",
                            "input.el-checkbox__original",
                            "input[type='checkbox']",
                            ".el-checkbox",
                        ):
                            try:
                                el = dlg.locator(sel).first
                                if el.count() == 0:
                                    continue
                                if "input" in sel:
                                    try:
                                        el.check(force=True, timeout=1500)
                                    except Exception:
                                        el.click(force=True, timeout=1500)
                                else:
                                    el.click(force=True, timeout=1500)
                                checked = True
                                break
                            except Exception:
                                continue
                        tip = dlg.get_by_text("已知晓该名称存在审核不通过", exact=False).first
                        if not _visible(tip):
                            tip = dlg.get_by_text("已知晓该名称", exact=False).first
                        if _visible(tip):
                            try:
                                tip.click(timeout=1500, force=True)
                                checked = True
                            except Exception:
                                box = tip.bounding_box()
                                if box:
                                    # 点文字左侧方框
                                    page.mouse.click(box["x"] - 28, box["y"] + box["height"] / 2)
                                    checked = True
                        # 确认已勾选
                        try:
                            if dlg.locator(
                                ".el-checkbox.is-checked, .el-checkbox__input.is-checked, input[type=checkbox]:checked"
                            ).count():
                                checked = True
                        except Exception:
                            pass
                        if not checked:
                            continue
                        btn = dlg.locator("button, .el-button").filter(
                            has_text=re.compile(r"^\s*下一步\s*$")
                        ).last
                        if not _visible(btn):
                            btn = dlg.get_by_role("button", name=re.compile(r"下一步")).last
                        if _visible(btn):
                            btn.click(timeout=4000)
                            out["compareNext"] = True
                            out["compareChecked"] = True
                        break
                    except Exception:
                        continue
                out["compareChecked"] = checked
                if isinstance(res, dict) and res.get("next"):
                    out["compareNext"] = True
                page.wait_for_timeout(900)
                _shot(page, case_id, f"after-compare-try-{attempt}")
                if _notice_dialog_visible(page):
                    out["compareNext"] = True
                    break
                if _compare_fail_visible(page) and not _compare_pass_visible(page):
                    out["compareFailed"] = True
                    _shot(page, case_id, "dialog-compare-or-alt-failed")
                    return out
                if out["compareNext"] and not _compare_pass_visible(page):
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
    if _wait_text(page, ["企业名称登记告知书", "已详细阅读", "承诺书", "我已详细阅读并知晓"], timeout_sec=15) or _notice_dialog_visible(
        page
    ):
        _shot(page, case_id, "dialog-notice")
        notice = _force_dismiss_notice(page, case_id)
        out["noticeDetail"] = notice
        out["noticeJs"] = notice.get("js")
        out["noticeCheckedCount"] = notice.get("checked")
        out["noticeConfirm"] = bool(notice.get("ok"))
        page.wait_for_timeout(800)

    # 3) 温馨提示（告知书未关时不要空等）
    if not _notice_dialog_visible(page) and _wait_text(page, ["温馨提示", "我已知晓上述内容"], timeout_sec=15):
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

    if not out["warmAck"] and not _notice_dialog_visible(page):
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
            _force_dismiss_notice(page, case_id)
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


def _normalize_district_name(text: str) -> str:
    """下拉选项一般为「虹口区」，去掉「上海市/上海」前缀，避免匹配成「上海市虹口区」。"""
    s = re.sub(r"\s+", "", (text or "").strip())
    if not s:
        return ""
    s = re.sub(r"^上海市?", "", s)
    m = re.search(r"([\u4e00-\u9fff]{1,3}区)", s)
    if m:
        return m.group(1)
    if s.endswith("区") and 1 < len(s) <= 6:
        return s
    return ""


def _district_from_payload(payload: dict[str, Any]) -> str:
    reg = payload.get("registration") or {}
    addr = reg.get("address") or {}
    for raw in (addr.get("district"), reg.get("district"), addr.get("detail"), reg.get("authority")):
        d = _normalize_district_name(str(raw or ""))
        if d:
            return d
    return ""


def _authority_candidates(payload: dict[str, Any]) -> list[str]:
    """申请机关按前端所选区县选对应市场监督管理局，不再固定市局。"""
    from apps.worker.playbooks.org_fill_profile import profile_for

    profile = profile_for(payload)
    out: list[str] = []
    reg = payload.get("registration") or {}
    explicit = str(reg.get("authority") or "").strip()
    district = _district_from_payload(payload)

    def _add(name: str) -> None:
        name = (name or "").strip()
        if name and name not in out:
            out.append(name)

    # 1) payload 已写好的机关（前端：{区县}市场监督管理局）
    _add(explicit)
    # 2) 按区县拼装
    if district:
        _add(f"{district}市场监督管理局")
        _add(f"{district}市场监管局")
        _add(district)
    # 3) 仅作兜底：市局（无区县时）
    for x in profile.get("authority_options") or []:
        _add(str(x or ""))
    return out


def _pick_odd_even_mode(dialog, page: Page, want: str = "不连号") -> str | None:
    """住所填报：选择连号模式（不连号/单号连号/双号连号/单双号）。"""
    want = (want or "不连号").strip() or "不连号"
    try:
        sels = dialog.locator(".el-select")
        for i in range(min(sels.count(), 8)):
            sel = sels.nth(i)
            inp = sel.locator("input").first
            if not _visible(inp, timeout=200):
                continue
            val = (inp.input_value(timeout=300) or "").strip()
            if want in val or val == want:
                return want
    except Exception:
        pass

    for text in (want,):
        try:
            lab = dialog.locator("label.el-radio, .el-radio").filter(has_text=text).first
            if _visible(lab):
                lab.click(force=True, timeout=2500)
                return text
        except Exception:
            pass
    try:
        sels = dialog.locator(".el-select")
        for i in range(min(sels.count(), 8) - 1, -1, -1):
            sel = sels.nth(i)
            if not _visible(sel, timeout=200):
                continue
            try:
                val = (sel.locator("input").first.input_value(timeout=200) or "").strip()
                if val.endswith("区") or "街道" in val or val.endswith("路"):
                    continue
            except Exception:
                pass
            _close_open_select_dropdowns(page, dialog)
            trig = sel.locator(".el-input__inner, input").first
            (trig if _visible(trig, timeout=200) else sel).click(timeout=2500)
            page.wait_for_timeout(300)
            opts = _wait_select_options(page, dialog, min_count=1, timeout_ms=2000)
            texts = [t for _, t in opts]
            matched = _match_option_text(want, texts)
            if not matched and any(want in t for t in texts):
                matched = next(t for t in texts if want in t)
            if matched and _click_matched_option(page, dialog, matched):
                page.wait_for_timeout(250)
                _close_open_select_dropdowns(page, dialog)
                return want
            _close_open_select_dropdowns(page, dialog)
    except Exception:
        pass
    return None

def _parse_shanghai_address(detail: str) -> dict[str, str]:
    """从详细地址拆出区/街道/路/弄/号/幢/室。

    例：上海市虹口区四川北路街道四川北路1318号4楼室
    → district=虹口区 street=四川北路街道 road=四川北路 number=1318 room=4楼
    """
    text = re.sub(r"\s+", "", (detail or "").strip())
    out = {
        "district": "",
        "street": "",
        "road": "",
        "lane": "",
        "number": "",
        "building": "",
        "room": "",
    }
    if not text:
        return out
    text = re.sub(r"^上海市", "", text)
    m_dist = re.match(r"([\u4e00-\u9fff]{1,6}区)", text)
    if m_dist:
        out["district"] = m_dist.group(1)
        text = text[m_dist.end() :]
    m_street = re.match(r"([\u4e00-\u9fff0-9]+(?:街道|镇|乡))", text)
    if m_street:
        out["street"] = m_street.group(1)
        text = text[m_street.end() :]
    # 路名：路/街/道/巷；「N弄」另作弄号，不把整段当路名
    m_road = re.match(r"([\u4e00-\u9fff0-9]+(?:路|街|道|巷))", text)
    if m_road:
        out["road"] = m_road.group(1)
        text = text[m_road.end() :]
    m_lane = re.match(r"(\d+)弄", text)
    if m_lane:
        out["lane"] = m_lane.group(1)
        text = text[m_lane.end() :]
    m_num = re.match(r"(\d+)号", text)
    if m_num:
        out["number"] = m_num.group(1)
        text = text[m_num.end() :]
    # 幢/栋/座：支持 C幢、1幢、甲座 等（不仅数字）
    m_bld = re.match(r"([A-Za-z0-9\u4e00-\u9fff]+)(?:幢|栋|座)", text)
    if m_bld:
        out["building"] = m_bld.group(1)
        text = text[m_bld.end() :]
    room = text.strip()
    if room.endswith("室") and len(room) > 1:
        room = room[:-1]
    out["room"] = room
    return out


def _road_stem(road: str) -> str:
    r = re.sub(r"\s+", "", (road or "").strip())
    return re.sub(r"(路|街|道|巷)$", "", r)


def _street_candidates(district: str, street: str, road: str) -> list[str]:
    """无显式街道时，从路名推导候选（金桥路→金桥镇/街道）；不设区县演示默认值。"""
    out: list[str] = []
    if (street or "").strip():
        out.append(street.strip())
    stem = _road_stem(road)
    stems = [stem] if stem else []
    if stem.startswith("新") and len(stem) > 2:
        stems.append(stem[1:])
    for s in stems:
        for suf in ("街道", "镇", "乡"):
            out.append(f"{s}{suf}")
    seen: set[str] = set()
    res: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return res


def _pick_street_by_road(
    dialog, page: Page, *, road: str, index: int = 1
) -> str | None:
    """打开街道下拉，按路名词干模糊选最佳项（如 金桥路 → 金桥镇）。"""
    stem = _road_stem(road)
    if not stem:
        return None
    stems = [stem]
    if stem.startswith("新") and len(stem) > 2:
        stems.append(stem[1:])
    if not _open_select_nth(dialog, page, index):
        return None
    opts = _wait_select_options(page, dialog, min_count=1, timeout_ms=5000)
    if not opts:
        _close_open_select_dropdowns(page, dialog)
        return None
    best: str | None = None
    best_score = 0
    for _, t in opts:
        c = re.sub(r"\s+", "", t or "")
        if not c or any(k in c for k in ("请选择", "不连号", "单号", "双号")):
            continue
        score = 0
        for s in stems:
            if not s:
                continue
            if c == f"{s}街道" or c == f"{s}镇" or c == f"{s}乡":
                score = max(score, 200 + len(s))
            elif s in c:
                score = max(score, 100 + len(s))
        if score > best_score:
            best_score = score
            best = t
    if best and best_score > 0 and _click_matched_option(page, dialog, best):
        page.wait_for_timeout(350)
        _close_open_select_dropdowns(page, dialog)
        return best
    _close_open_select_dropdowns(page, dialog)
    return None


def _dialog_pick_select(
    dialog, page: Page, placeholder_or_hint: str, option_text: str
) -> bool:
    """住所弹窗内点开 el-select 并选选项（下拉层常 teleport 到 iframe body）。"""
    if not option_text:
        return False
    try:
        _close_open_select_dropdowns(page, dialog)
        opened = False
        # 按 placeholder 点开（实测：请选择区县 / 请选择街道/乡镇）
        inp = dialog.locator(
            f".el-select input[placeholder*='{placeholder_or_hint}']"
        ).first
        if not _visible(inp, timeout=700):
            inp = dialog.locator(f"input[placeholder*='{placeholder_or_hint}']").first
        if _visible(inp, timeout=700):
            wrap = dialog.locator(
                f".el-select:has(input[placeholder*='{placeholder_or_hint}'])"
            ).first
            if _visible(wrap, timeout=400):
                caret = wrap.locator(".el-select__caret, .el-input__suffix").first
                try:
                    if _visible(caret, timeout=200):
                        caret.click(timeout=2500)
                        opened = True
                except Exception:
                    pass
                if not opened:
                    wrap.click(timeout=3000)
                    opened = True
            else:
                inp.click(timeout=3000)
                opened = True
        if not opened:
            sel = dialog.locator(".el-select").filter(
                has_text=re.compile(re.escape(placeholder_or_hint))
            ).first
            if _visible(sel, timeout=600):
                sel.click(timeout=3000)
                opened = True
        if not opened:
            return False

        page.wait_for_timeout(400)
        opts = _wait_select_options(page, dialog, min_count=1, timeout_ms=5000)
        texts = [t for _, t in opts]
        matched = _match_option_text(option_text, texts)
        if not matched:
            return False
        if _click_matched_option(page, dialog, matched):
            page.wait_for_timeout(350)
            try:
                wrap = dialog.locator(
                    f".el-select:has(input[placeholder*='{placeholder_or_hint}'])"
                ).first
                if _visible(wrap, timeout=300):
                    shown = re.sub(r"\s+", "", wrap.inner_text(timeout=500) or "")
                    if matched in shown or option_text in shown:
                        _close_open_select_dropdowns(page, dialog)
                        return True
                    vin = wrap.locator("input").first
                    if _visible(vin, timeout=200):
                        vv = (vin.input_value(timeout=400) or "").strip()
                        if matched in vv or option_text in vv or vv in option_text:
                            _close_open_select_dropdowns(page, dialog)
                            return True
            except Exception:
                pass
            _close_open_select_dropdowns(page, dialog)
            return True
    except Exception:
        return False
    return False


def _close_open_select_dropdowns(page: Page, dialog=None) -> None:
    """关掉残留下拉。禁止 Escape：会关掉整个「住所填报」弹窗。"""
    try:
        # 优先点弹窗标题/空白，收起下拉且不关 dialog
        if dialog is not None:
            try:
                if _visible(dialog, timeout=250):
                    hdr = dialog.locator(".el-dialog__header, .el-dialog__title").first
                    if _visible(hdr, timeout=200):
                        hdr.click(timeout=1200, position={"x": 8, "y": 8})
                        page.wait_for_timeout(100)
                        return
                    body = dialog.locator(".el-dialog__body").first
                    if _visible(body, timeout=200):
                        body.click(timeout=1200, position={"x": 12, "y": 12})
                        page.wait_for_timeout(100)
                        return
            except Exception:
                pass
        # 兜底：用 JS 隐藏可见下拉层（不触发 dialog 关闭）
        for scope in _all_scopes(page):
            try:
                scope.evaluate(
                    """() => {
                      document.querySelectorAll(
                        '.el-select-dropdown, .el-popper[x-placement], .el-autocomplete-suggestion'
                      ).forEach((el) => {
                        el.style.display = 'none';
                        el.classList.remove('el-zoom-in-top-enter-active');
                      });
                    }"""
                )
            except Exception:
                continue
        page.wait_for_timeout(80)
    except Exception:
        pass


def _find_address_dialog(page: Page):
    for scope in _all_scopes(page):
        try:
            for title in ("住所填报", "经营场所填报", "主要经营场所", "住所地址"):
                d = scope.locator(".el-dialog:visible, .el-drawer:visible").filter(
                    has_text=title
                ).first
                if _visible(d, timeout=400):
                    return d
            d = scope.locator(".el-dialog:visible").filter(has_text="邮政编码").first
            if _visible(d, timeout=300):
                return d
        except Exception:
            continue
    return None


def _click_open_address_field(page: Page, frame: Frame) -> bool:
    """点表单「住所 / 主要经营场所」触发弹窗（只读 input 需点 wrapper / force）。

    有限公司多为「住所」；普通/有限合伙为「主要经营场所」。
    """
    scopes = []
    try:
        scopes.append(frame)
    except Exception:
        pass
    scopes.extend(_all_scopes(page))
    label_re = re.compile(r"主要经营场所|经营场所|住所")
    seen = set()
    for scope in scopes:
        try:
            sid = id(scope)
            if sid in seen:
                continue
            seen.add(sid)
        except Exception:
            pass
        try:
            # 优先精确匹配「住所」「主要经营场所」标签，避免误点其它含「住所」的表单项
            items = scope.locator(".el-form-item").filter(has_text=label_re)
            n = min(items.count(), 8)
            best = None
            best_score = 10**9
            for i in range(n):
                it = items.nth(i)
                if not _visible(it, timeout=300):
                    continue
                try:
                    lab = it.locator(".el-form-item__label").first
                    lab_t = " ".join((lab.inner_text(timeout=200) or "").split()) if lab.count() else ""
                except Exception:
                    lab_t = ""
                # 分数：标签越短且等于目标越好
                if lab_t in ("住所", "主要经营场所", "经营场所"):
                    score = len(lab_t)
                elif "住所" in lab_t or "经营场所" in lab_t:
                    score = 50 + len(lab_t)
                else:
                    continue
                if score < best_score:
                    best = it
                    best_score = score
            item = best
            if item is None:
                item = scope.locator(".el-form-item").filter(has_text=label_re).first
            if item is None or not _visible(item, timeout=500):
                continue
            # 1) 点整个表单项里的 input/textarea
            inp = item.locator("input, textarea").first
            if _visible(inp, timeout=400):
                try:
                    inp.click(timeout=3000)
                    return True
                except Exception:
                    try:
                        inp.click(timeout=3000, force=True)
                        return True
                    except Exception:
                        pass
            # 2) 点 el-input 外壳
            wrap = item.locator(".el-input, .el-textarea").first
            if _visible(wrap, timeout=300):
                try:
                    wrap.click(timeout=3000)
                    return True
                except Exception:
                    try:
                        wrap.click(timeout=3000, force=True)
                        return True
                    except Exception:
                        pass
            # 3) JS 点击（绕过遮挡）
            try:
                clicked = item.evaluate(
                    """(el) => {
                      const t = el.querySelector('input, textarea, .el-input__inner');
                      if (!t) return false;
                      t.focus();
                      t.click();
                      t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                      return true;
                    }"""
                )
                if clicked:
                    return True
            except Exception:
                pass
        except Exception:
            continue
    return _click_text(
        frame,
        ["主要经营场所", "经营场所", "住所"],
        exact=False,
    )


def _close_address_dialog(page: Page) -> bool:
    """关闭已打开的住所弹窗（优先右上角叉），便于卡住后重开重试。"""
    dialog = _find_address_dialog(page)
    if dialog is None:
        return True
    try:
        # 1) 右上角关闭（卡住时 footer 常只有「确定」）
        for sel in (
            ".el-dialog__headerbtn",
            "button.el-dialog__headerbtn",
            ".el-dialog__close",
            "[aria-label='Close']",
            "[aria-label='关闭']",
        ):
            close = dialog.locator(sel).first
            if _visible(close, timeout=250):
                try:
                    close.click(timeout=2000, force=True)
                except Exception:
                    try:
                        close.click(timeout=2000)
                    except Exception:
                        continue
                page.wait_for_timeout(500)
                if _find_address_dialog(page) is None:
                    return True
        # 2) footer 取消/关闭
        footer = dialog.locator(".el-dialog__footer").first
        if _visible(footer, timeout=300):
            for name in ("取消", "关闭"):
                btn = footer.locator("button").filter(
                    has_text=re.compile(rf"^\s*{name}\s*$")
                ).first
                if _visible(btn, timeout=250):
                    try:
                        btn.click(timeout=2500, force=True)
                    except Exception:
                        btn.click(timeout=2500)
                    page.wait_for_timeout(500)
                    if _find_address_dialog(page) is None:
                        return True
    except Exception:
        pass
    # 禁止 Escape（会误关其它层）；JS 点叉/取消
    try:
        dialog.evaluate(
            """(root) => {
              const x = root.querySelector(
                '.el-dialog__headerbtn, .el-dialog__close, button.el-dialog__headerbtn'
              );
              if (x) { x.click(); return true; }
              const foot = root.querySelector('.el-dialog__footer') || root;
              const btn = [...foot.querySelectorAll('button')].find(b => {
                const t = (b.innerText || '').replace(/\\s+/g, '');
                return t === '取消' || t === '关闭';
              });
              if (btn) { btn.click(); return true; }
              return false;
            }"""
        )
        page.wait_for_timeout(500)
    except Exception:
        pass
    # 遮罩层偶发残留：再扫全页右上角叉
    if _find_address_dialog(page) is not None:
        try:
            for scope in _all_scopes(page):
                x = scope.locator(
                    ".el-dialog__wrapper:visible .el-dialog__headerbtn, "
                    ".el-overlay-dialog:visible .el-dialog__headerbtn"
                ).first
                if _visible(x, timeout=200):
                    x.click(timeout=1500, force=True)
                    page.wait_for_timeout(400)
                    break
        except Exception:
            pass
    return _find_address_dialog(page) is None


def _ensure_address_dialog(page: Page, frame: Frame, *, retries: int = 3, fresh: bool = False):
    """确保住所/经营场所填报弹窗打开；关掉后会重新点击入口字段。

    fresh=True：先关掉已有弹窗再重开（基本信息重试时避免半成品状态）。
    """
    if fresh:
        _close_address_dialog(page)
        page.wait_for_timeout(400)
    dialog = _find_address_dialog(page)
    if dialog is not None and not fresh:
        return dialog
    for _ in range(max(1, retries)):
        if not _click_open_address_field(page, frame):
            # 合伙企业常见 placeholder：请输入经营场所
            try:
                ph = frame.get_by_placeholder(re.compile(r"经营场所|住所")).first
                if _visible(ph, timeout=400):
                    ph.click(timeout=3000, force=True)
            except Exception:
                pass
        page.wait_for_timeout(800)
        dialog = _find_address_dialog(page)
        if dialog is not None:
            return dialog
        page.wait_for_timeout(400)
    return None


def _visible_select_options(page: Page, dialog=None) -> list:
    """收集可见选项；下拉常 teleport 到 iframe/body，须扫全部 frame。"""
    items = []
    for scope in _all_scopes(page):
        try:
            opts = scope.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled), "
                ".el-popper:visible .el-select-dropdown__item:not(.is-disabled), "
                "div[x-placement] .el-select-dropdown__item:not(.is-disabled), "
                ".el-select-dropdown__list:visible .el-select-dropdown__item:not(.is-disabled)"
            )
            n = min(opts.count(), 100)
            for i in range(n):
                try:
                    loc = opts.nth(i)
                    if not loc.is_visible(timeout=300):
                        continue
                    t = (loc.inner_text(timeout=400) or "").strip()
                    if t:
                        items.append((loc, t))
                except Exception:
                    continue
            if items:
                break
        except Exception:
            continue
    return items


def _wait_select_options(page: Page, dialog=None, *, min_count: int = 1, timeout_ms: int = 4000) -> list:
    deadline = time.time() + timeout_ms / 1000.0
    last: list = []
    while time.time() < deadline:
        last = _visible_select_options(page, dialog)
        if len(last) >= min_count:
            return last
        page.wait_for_timeout(200)
    return last


def _match_option_text(option_text: str, candidates: list[str]) -> str | None:
    """精确 → 去空白相等 → 包含 → 被包含。"""
    want = re.sub(r"\s+", "", (option_text or "").strip())
    if not want:
        return None
    cleaned = [(c, re.sub(r"\s+", "", c)) for c in candidates if c]
    for raw, c in cleaned:
        if c == want:
            return raw
    for raw, c in cleaned:
        if want in c or c in want:
            return raw
    want2 = re.sub(r"(路|街|道|巷|区|街道)$", "", want)
    if want2 and want2 != want:
        for raw, c in cleaned:
            c2 = re.sub(r"(路|街|道|巷|区|街道)$", "", c)
            if c2 == want2 or want2 in c or c2 in want2:
                return raw
    return None


def _click_matched_option(page: Page, dialog, matched_text: str) -> bool:
    """只点可见下拉项（扫全部 frame），禁止点已选触发器。"""
    for scope in _all_scopes(page):
        try:
            opt = scope.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled), "
                ".el-popper:visible .el-select-dropdown__item:not(.is-disabled), "
                "div[x-placement] .el-select-dropdown__item:not(.is-disabled)"
            ).filter(has_text=re.compile(rf"^\s*{re.escape(matched_text)}\s*$")).first
            if _visible(opt, timeout=600):
                opt.click(timeout=3000)
                return True
            opt2 = scope.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled), "
                ".el-popper:visible .el-select-dropdown__item:not(.is-disabled)"
            ).filter(has_text=re.compile(re.escape(matched_text))).first
            if _visible(opt2, timeout=500):
                opt2.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def _pick_region_by_placeholder(
    dialog, page: Page, *, placeholders: list[str], option_text: str, retries: int = 3
) -> bool:
    """按真实 placeholder 选择区县/街道（主路径）。"""
    want = (option_text or "").strip()
    if not want:
        return False
    for ph in placeholders:
        try:
            wrap = dialog.locator(f".el-select:has(input[placeholder*='{ph}'])").first
            if _visible(wrap, timeout=400):
                shown = re.sub(r"\s+", "", wrap.inner_text(timeout=400) or "")
                if "请选择" not in shown and want in shown:
                    return True
        except Exception:
            pass
        for _ in range(retries):
            if _dialog_pick_select(dialog, page, ph, want):
                return True
            page.wait_for_timeout(300)
    return False


def _address_row_selects(dialog):
    """定位区县/街道 el-select（路名是 input；排除不连号）。"""
    keep = []
    try:
        for ph in ("区县", "街道"):
            sel = dialog.locator(f".el-select:has(input[placeholder*='{ph}'])").first
            if _visible(sel, timeout=400):
                keep.append(sel)
        if keep:
            return keep
        item = dialog.locator(".el-form-item").filter(
            has_text=re.compile(r"住址|住所地址|详细地址")
        ).first
        root = item if _visible(item, timeout=500) else dialog
        sels = root.locator(".el-select")
        n = min(sels.count(), 8)
        for i in range(n):
            sel = sels.nth(i)
            try:
                val = ""
                ph = ""
                inp = sel.locator("input").first
                if _visible(inp, timeout=200):
                    val = (inp.input_value(timeout=300) or "").strip()
                    ph = inp.get_attribute("placeholder") or ""
                txt = re.sub(r"\s+", "", (sel.inner_text(timeout=300) or "") + val + ph)
                if any(k in txt for k in ("不连号", "单号", "双号", "单双号", "路名", "村名")):
                    continue
                keep.append(sel)
                if len(keep) >= 2:
                    break
            except Exception:
                continue
    except Exception:
        return []
    return keep


def _open_select_nth(dialog, page: Page, index: int) -> bool:
    sels = _address_row_selects(dialog)
    if not sels or index >= len(sels):
        return False
    try:
        sel = sels[index]
        if not _visible(sel, timeout=800):
            return False
        _close_open_select_dropdowns(page, dialog)
        page.wait_for_timeout(120)
        caret = sel.locator(".el-select__caret, .el-input__suffix").first
        if _visible(caret, timeout=250):
            try:
                caret.click(timeout=2500)
                page.wait_for_timeout(280)
                return True
            except Exception:
                pass
        trig = sel.locator(".el-input__inner, input, .el-input").first
        if _visible(trig, timeout=400):
            trig.click(timeout=3000)
        else:
            sel.click(timeout=3000)
        page.wait_for_timeout(280)
        return True
    except Exception:
        return False


def _read_select_nth_value(dialog, index: int) -> str:
    sels = _address_row_selects(dialog)
    if not sels or index >= len(sels):
        return ""
    try:
        sel = sels[index]
        inp = sel.locator("input").first
        if _visible(inp, timeout=300):
            v = (inp.input_value(timeout=500) or "").strip()
            if v:
                return v
        txt = re.sub(r"\s+", "", (sel.inner_text(timeout=500) or ""))
        return "" if "请选择" in txt else txt
    except Exception:
        return ""


def _pick_region_select(
    dialog,
    page: Page,
    *,
    index: int,
    option_text: str,
    hints: list[str] | None = None,
    retries: int = 3,
) -> bool:
    """区域选择：先 placeholder，再下标回落。"""
    want = (option_text or "").strip()
    if not want:
        return False
    hints = [h for h in (hints or []) if h and h != "请选择"]

    if hints and _pick_region_by_placeholder(
        dialog, page, placeholders=hints, option_text=want, retries=retries
    ):
        return True

    cur = _read_select_nth_value(dialog, index)
    if cur and (want in cur or cur in want or re.sub(r"\s+", "", cur) == re.sub(r"\s+", "", want)):
        return True

    for attempt in range(max(1, retries)):
        if not _open_select_nth(dialog, page, index):
            page.wait_for_timeout(250)
            continue
        opts = _wait_select_options(page, dialog, min_count=1, timeout_ms=5000)
        if not opts:
            _open_select_nth(dialog, page, index)
            opts = _wait_select_options(page, dialog, min_count=1, timeout_ms=3500)
        texts = [t for _, t in opts]
        if texts and all(any(k in t for k in ("不连号", "单号", "双号")) for t in texts[:5]):
            _close_open_select_dropdowns(page, dialog)
            return False
        matched = _match_option_text(want, texts)
        if matched and _click_matched_option(page, dialog, matched):
            page.wait_for_timeout(350)
            _close_open_select_dropdowns(page, dialog)
            return True
        got = _read_select_nth_value(dialog, index)
        if got and (want in got or got in want):
            _close_open_select_dropdowns(page, dialog)
            return True
        _close_open_select_dropdowns(page, dialog)
        page.wait_for_timeout(280 + attempt * 200)
    return False


def _fill_road_field(dialog, page: Page, frame: Frame, road: str) -> bool:
    """路名是输入框（placeholder=请输入汉字路名/村名），常为 autocomplete，须触发 input 并点联想。"""
    road = (road or "").strip()
    if not road:
        return False
    _close_open_select_dropdowns(page, dialog)
    page.wait_for_timeout(250)

    def _locate_road_input():
        for sel in (
            "input[placeholder*='汉字路名']",
            "input[placeholder*='路名/村名']",
            "input[placeholder*='路名']",
            "input[placeholder*='村名']",
            "input[placeholder*='请输入路']",
        ):
            loc = dialog.locator(sel).first
            if _visible(loc, timeout=500):
                return loc
        return None

    def _read_val(inp) -> str:
        try:
            return (inp.input_value(timeout=500) or "").strip()
        except Exception:
            return ""

    def _ok(val: str) -> bool:
        v = re.sub(r"\s+", "", val or "")
        w = re.sub(r"\s+", "", road)
        return bool(v) and (w in v or v in w or v.rstrip("路村街") == w.rstrip("路村街"))

    def _list_suggestions() -> list[tuple[Any, str]]:
        """收集可见联想项：(locator, text)。"""
        items: list[tuple[Any, str]] = []
        seen: set[str] = set()
        sel = (
            ".el-autocomplete-suggestion:visible li, "
            ".el-autocomplete-suggestion__list:visible li, "
            ".el-popper:visible li.el-autocomplete-suggestion__item, "
            ".el-popper:visible .el-autocomplete-suggestion li, "
            ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled)"
        )
        for scope in _all_scopes(page):
            try:
                opts = scope.locator(sel)
                n = min(opts.count(), 40)
                for i in range(n):
                    try:
                        loc = opts.nth(i)
                        if not loc.is_visible(timeout=200):
                            continue
                        t = re.sub(r"\s+", "", (loc.inner_text(timeout=300) or "").strip())
                        if not t or t in seen:
                            continue
                        seen.add(t)
                        items.append((loc, t))
                    except Exception:
                        continue
                if items:
                    break
            except Exception:
                continue
        return items

    def _score_road(want: str, option: str) -> int:
        """用户路名与选项匹配分；越高越优先。无匹配返回 0。"""
        w = re.sub(r"\s+", "", (want or "").strip())
        o = re.sub(r"\s+", "", (option or "").strip())
        if not w or not o:
            return 0
        if o == w:
            return 300
        w2 = w.rstrip("路村街巷道")
        o2 = o.rstrip("路村街巷道")
        if w2 and o2 and o2 == w2:
            return 280
        if w in o:
            return 200 + len(w)
        if o in w:
            return 180 + len(o)
        if w2 and o2 and (w2 in o2 or o2 in w2):
            return 150 + min(len(w2), len(o2))
        return 0

    def _pick_suggestion() -> bool:
        """优先点选与用户路名匹配的联想项；列表无匹配时再选第一项。"""
        page.wait_for_timeout(200)
        items = _list_suggestions()
        # 等联想弹出
        if not items:
            for _ in range(8):
                page.wait_for_timeout(150)
                items = _list_suggestions()
                if items:
                    break
        if items:
            best_loc = None
            best_score = 0
            for loc, t in items:
                sc = _score_road(road, t)
                if sc > best_score:
                    best_score = sc
                    best_loc = loc
            # 有匹配 → 点用户路名；无匹配 → 点第一项
            target = best_loc if best_score > 0 else items[0][0]
            try:
                target.click(timeout=3000)
                page.wait_for_timeout(250)
                return True
            except Exception:
                try:
                    target.click(timeout=3000, force=True)
                    page.wait_for_timeout(250)
                    return True
                except Exception:
                    pass
        # 最后键盘回落：仅当上面都失败
        try:
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(120)
            page.keyboard.press("Enter")
            page.wait_for_timeout(200)
            return True
        except Exception:
            return False

    def _js_set(inp, value: str) -> bool:
        try:
            return bool(
                inp.evaluate(
                    """(el, v) => {
                      const root = el.closest('.el-dialog') || document;
                      const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                      )?.set;
                      el.focus();
                      if (setter) setter.call(el, '');
                      else el.value = '';
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      if (setter) setter.call(el, v);
                      else el.value = v;
                      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: v, inputType: 'insertText' }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                      // Element autocomplete 有时靠 composition/keydown
                      el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Unidentified' }));
                      el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Unidentified' }));
                      return (el.value || '') === v;
                    }""",
                    value,
                )
            )
        except Exception:
            return False

    road_inp = _locate_road_input()
    if road_inp is None:
        return False
    if _ok(_read_val(road_inp)):
        return True

    # 策略1：清空后逐字输入（最容易弹出标准路名联想）
    try:
        road_inp.click(timeout=2500)
        page.wait_for_timeout(150)
        try:
            road_inp.fill("")
        except Exception:
            pass
        try:
            road_inp.press("Control+A")
            road_inp.press("Backspace")
        except Exception:
            pass
        # Playwright 1.40+ press_sequentially；旧版用 type
        typed = False
        try:
            road_inp.press_sequentially(road, delay=60)
            typed = True
        except Exception:
            try:
                road_inp.type(road, delay=60)
                typed = True
            except Exception:
                pass
        if typed:
            page.wait_for_timeout(700)
            _pick_suggestion()
            page.wait_for_timeout(200)
            if _ok(_read_val(road_inp)):
                return True
            # 再点一次联想 / Enter 确认
            _pick_suggestion()
            if _ok(_read_val(road_inp)):
                return True
    except Exception:
        pass

    # 策略2：JS 写入 + 再点联想
    road_inp = _locate_road_input() or road_inp
    try:
        road_inp.click(timeout=2000)
        if _js_set(road_inp, road):
            page.wait_for_timeout(500)
            _pick_suggestion()
            page.wait_for_timeout(200)
            if _ok(_read_val(road_inp)):
                return True
    except Exception:
        pass

    # 策略3：Playwright fill
    try:
        road_inp = _locate_road_input() or road_inp
        road_inp.click(timeout=2000)
        road_inp.fill(road)
        page.wait_for_timeout(500)
        _pick_suggestion()
        if _ok(_read_val(road_inp)):
            return True
    except Exception:
        pass

    # 最终回读（有时联想写入后 input_value 短暂空，看 DOM value）
    try:
        road_inp = _locate_road_input() or road_inp
        val = _read_val(road_inp)
        if _ok(val):
            return True
        # 检查校验错误是否消失：父级无 is-error 且无「请输入路」
        wrap = dialog.locator(
            ".el-form-item, .el-input, .el-autocomplete"
        ).filter(has=dialog.locator("input[placeholder*='路名'], input[placeholder*='村名']")).first
        txt = ""
        try:
            txt = wrap.inner_text(timeout=400) or ""
        except Exception:
            pass
        if val and "请输入路" not in txt and "请输入汉字" not in txt:
            return True
    except Exception:
        pass
    return False



def _fill_address_units(
    dialog,
    *,
    lane: str,
    number: str,
    building: str,
    room: str,
    number_end: str = "",
) -> dict[str, Any]:
    """按后缀「弄/号/幢/室」定位；选填——有值才写入，无值则清空（含表单默认 0）。

    连号时「号」有两个输入框：起号 / 止号。
    """
    lane = (lane or "").strip()
    number = (number or "").strip()
    number_end = (number_end or "").strip()
    building = (building or "").strip()
    room = (room or "").strip()
    fields = {
        "弄": [lane],
        "号": [number, number_end] if number_end else [number],
        "幢": [building],
        "室": [room],
    }
    info: dict[str, Any] = {"ok": False, "values": {}, "bySuffix": {}}

    def _emptyish(v: str) -> bool:
        return (v or "").strip() in ("", "0")

    try:
        js_res = dialog.evaluate(
            """(dialogEl, fields) => {
              const root = dialogEl || document;
              const bad = (inp) => {
                if (!inp) return true;
                if (inp.closest && inp.closest('.el-select, .el-textarea')) return true;
                const t = (inp.type || '').toLowerCase();
                return t === 'radio' || t === 'checkbox' || t === 'hidden' || t === 'file';
              };
              const setVal = (inp, v) => {
                inp.focus();
                const setter = Object.getOwnPropertyDescriptor(
                  window.HTMLInputElement.prototype, 'value'
                )?.set;
                const write = (x) => {
                  if (setter) setter.call(inp, x);
                  else inp.value = x;
                  inp.dispatchEvent(new InputEvent('input', { bubbles: true, data: x }));
                  inp.dispatchEvent(new Event('change', { bubbles: true }));
                };
                write('');
                if (v !== '') write(String(v));
                inp.blur();
              };
              const findInputs = (suffix) => {
                const seen = new Set();
                const list = [];
                const push = (inp) => {
                  if (!inp || bad(inp) || seen.has(inp)) return;
                  seen.add(inp);
                  list.push(inp);
                };
                // Element UI 后缀/前缀
                for (const a of root.querySelectorAll(
                  '.el-input-group__append, .el-input-group__prepend, .el-input__suffix-inner, span'
                )) {
                  const txt = (a.textContent || '').replace(/\\s+/g, '');
                  if (txt !== suffix) continue;
                  const wrap =
                    a.closest('.el-input') ||
                    a.closest('.el-input-group') ||
                    a.parentElement;
                  push(wrap && wrap.querySelector('input'));
                  // 兄弟 input
                  const prev = a.previousElementSibling;
                  if (prev) {
                    push(prev.tagName === 'INPUT' ? prev : prev.querySelector && prev.querySelector('input'));
                  }
                }
                return list;
              };
              const out = {};
              for (const [suffix, raw] of Object.entries(fields || {})) {
                const vals = Array.isArray(raw) ? raw : [raw];
                const inps = findInputs(suffix);
                if (!inps.length) {
                  out[suffix] = { found: false, value: '', values: [] };
                  continue;
                }
                const written = [];
                for (let i = 0; i < inps.length; i++) {
                  const v = i < vals.length ? (vals[i] == null ? '' : String(vals[i])) : '';
                  setVal(inps[i], v);
                  written.push(inps[i].value || '');
                }
                out[suffix] = { found: true, value: written[0] || '', values: written };
              }
              return out;
            }""",
            fields,
        )
        if isinstance(js_res, dict):
            info["bySuffix"] = js_res
            num_vals = (js_res.get("号") or {}).get("values") or []
            info["values"] = {
                "lane": (js_res.get("弄") or {}).get("value") or "",
                "number": (num_vals[0] if num_vals else (js_res.get("号") or {}).get("value"))
                or "",
                "numberEnd": (num_vals[1] if len(num_vals) > 1 else "") or "",
                "building": (js_res.get("幢") or {}).get("value") or "",
                "room": (js_res.get("室") or {}).get("value") or "",
            }
            info["found"] = {
                k: bool((js_res.get(s) or {}).get("found"))
                for k, s in (
                    ("lane", "弄"),
                    ("number", "号"),
                    ("building", "幢"),
                    ("room", "室"),
                )
            }
    except Exception as e:
        info["error"] = str(e)

    # Playwright 回落：逐项补填（无论 JS 是否部分成功）
    try:
        want_map = {"弄": lane, "幢": building, "室": room}
        for suffix, val in want_map.items():
            got_now = _read_address_unit(dialog, suffix)
            need = (val or "").strip()
            if need:
                if got_now != need and got_now != f"{need}{suffix}":
                    _fill_address_unit_input(dialog, suffix, need)
            elif not _emptyish(got_now):
                _fill_address_unit_input(dialog, suffix, "")
        nums = [number, number_end] if number_end else [number]
        labs = dialog.locator(
            ".el-input-group__append, .el-input-group__prepend"
        ).filter(has_text=re.compile(r"^\s*号\s*$"))
        n_lab = labs.count()
        for i, val in enumerate(nums):
            if i >= n_lab:
                # 无 append 时试相邻文本
                if val:
                    _fill_address_unit_input(dialog, "号", val)
                break
            lab = labs.nth(i)
            if not _visible(lab, timeout=300):
                continue
            wrap = lab.locator("xpath=ancestor::*[contains(@class,'el-input')][1]")
            inp = wrap.locator("input").first
            if not _visible(inp, timeout=300):
                continue
            cur = ""
            try:
                cur = (inp.input_value(timeout=300) or "").strip()
            except Exception:
                pass
            if (val or "") == cur or (not val and _emptyish(cur)):
                continue
            inp.click(timeout=1500)
            inp.fill("")
            if val:
                inp.fill(val)
        info["values"] = {
            "lane": _read_address_unit(dialog, "弄"),
            "number": _read_address_unit(dialog, "号") or number,
            "numberEnd": number_end,
            "building": _read_address_unit(dialog, "幢"),
            "room": _read_address_unit(dialog, "室"),
        }
        # 连号止号：第二个「号」
        if number_end and n_lab >= 2:
            try:
                wrap2 = labs.nth(1).locator(
                    "xpath=ancestor::*[contains(@class,'el-input')][1]"
                )
                info["values"]["numberEnd"] = (
                    wrap2.locator("input").first.input_value(timeout=400) or ""
                ).strip()
            except Exception:
                pass
        # JS 已写入但 Playwright 回读为空时，保留 bySuffix 值（幢/室常见）
        by = info.get("bySuffix") or {}
        vals = info["values"]
        for key, suf in (
            ("lane", "弄"),
            ("number", "号"),
            ("building", "幢"),
            ("room", "室"),
        ):
            js_v = str((by.get(suf) or {}).get("value") or "").strip()
            if js_v and not str(vals.get(key) or "").strip():
                vals[key] = js_v
            if key == "number":
                js_nums = (by.get("号") or {}).get("values") or []
                if js_nums and not str(vals.get("number") or "").strip():
                    vals["number"] = str(js_nums[0] or "").strip()
                if len(js_nums) > 1 and number_end and not str(vals.get("numberEnd") or "").strip():
                    vals["numberEnd"] = str(js_nums[1] or "").strip()
        info["found"] = {
            "lane": True,
            "number": True,
            "building": True,
            "room": True,
        }
        info["via"] = (info.get("via") or "") + "+playwright"
    except Exception as e:
        info["fallbackError"] = str(e)

    got = info.get("values") or {}
    # 再兜一层：回读空则用 bySuffix
    by = info.get("bySuffix") or {}
    for key, suf in (
        ("lane", "弄"),
        ("building", "幢"),
        ("room", "室"),
    ):
        if not str(got.get(key) or "").strip():
            js_v = str((by.get(suf) or {}).get("value") or "").strip()
            if js_v:
                got[key] = js_v
    if not str(got.get("number") or "").strip():
        js_nums = (by.get("号") or {}).get("values") or []
        if js_nums:
            got["number"] = str(js_nums[0] or "").strip()
    info["values"] = got

    got_num = str(got.get("number") or "").strip()
    got_room = str(got.get("room") or "").strip()
    got_lane = str(got.get("lane") or "").strip()
    got_bld = str(got.get("building") or "").strip()
    got_num_end = str(got.get("numberEnd") or "").strip()

    # 选填：用户未填时接受空或表单默认「0」
    info["numberOk"] = (
        (got_num == number)
        if number
        else _emptyish(got_num)
    )
    info["numberEndOk"] = (
        True if not number_end else got_num_end == number_end
    )
    info["roomOk"] = (
        (_emptyish(got_room) if not room else False)
        or got_room == room
        or got_room == f"{room}室"
        or (bool(room) and room in got_room)
    )
    info["laneOk"] = (got_lane == lane) if lane else _emptyish(got_lane)
    info["buildingOk"] = (
        (got_bld == building or building in got_bld)
        if building
        else _emptyish(got_bld)
    )
    # 仅当「用户填写了却没写上」才算失败；未填字段不阻塞
    info["ok"] = bool(
        info["numberOk"]
        and info["numberEndOk"]
        and info["roomOk"]
        and info["laneOk"]
        and info["buildingOk"]
    )
    return info


def _fill_address_unit_input(dialog, suffix: str, value: str) -> bool:
    """按后缀 append 定位单个框；无值则清空。"""
    value = "" if value is None else str(value).strip()
    try:
        lab = dialog.locator(
            ".el-input-group__append, .el-input-group__prepend"
        ).filter(has_text=re.compile(rf"^\s*{re.escape(suffix)}\s*$")).first
        if not _visible(lab, timeout=400):
            return False
        wrap = lab.locator("xpath=ancestor::*[contains(@class,'el-input')][1]")
        inp = wrap.locator("input").first
        if not _visible(inp, timeout=400):
            return False
        inp.click(timeout=1500)
        inp.fill("")
        if value:
            inp.fill(value)
        return True
    except Exception:
        return False


def _read_address_unit(dialog, suffix: str) -> str:
    try:
        return str(
            dialog.evaluate(
                """(suffix) => {
                  for (const a of document.querySelectorAll(
                    '.el-input-group__append, .el-input-group__prepend'
                  )) {
                    if ((a.textContent || '').replace(/\\s+/g,'') !== suffix) continue;
                    const wrap = a.closest('.el-input') || a.parentElement;
                    const inp = wrap && wrap.querySelector('input');
                    if (inp) return inp.value || '';
                  }
                  return '';
                }""",
                suffix,
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _fill_address_dialog(
    page: Page, frame: Frame, payload: dict[str, Any], case_id: str
) -> dict[str, Any]:
    """住所填报：失败或卡住时关弹窗（右上角叉）后最多再试 2 次。"""
    rounds: list[dict[str, Any]] = []
    last: dict[str, Any] = {"opened": False, "ok": False}
    for attempt in range(3):
        if attempt > 0:
            closed = _close_address_dialog(page)
            page.wait_for_timeout(700)
            _shot(page, case_id, f"address-stuck-closed-{attempt}")
            rounds.append({"attempt": attempt, "closedBeforeRetry": closed})
        last = _fill_address_dialog_once(page, frame, payload, case_id)
        last["attempt"] = attempt + 1
        rounds.append(
            {
                "attempt": attempt + 1,
                "ok": bool(last.get("ok")),
                "complete": bool(last.get("complete")),
                "error": last.get("error"),
                "district": last.get("district"),
                "street": last.get("street"),
                "road": last.get("road"),
            }
        )
        if last.get("ok"):
            last["retryRounds"] = rounds
            return last
        # 未成功：关掉卡住的弹窗，再重开重试
        _close_address_dialog(page)
        page.wait_for_timeout(600)
    last["retryRounds"] = rounds
    last["retriedAfterStuck"] = True
    if not last.get("error"):
        last["error"] = "住所填报多次重试仍失败"
    return last


def _fill_address_dialog_once(
    page: Page, frame: Frame, payload: dict[str, Any], case_id: str
) -> dict[str, Any]:
    reg = payload.get("registration") or {}
    addr = reg.get("address") or {}
    result: dict[str, Any] = {"opened": False, "ok": False}

    # 每次尝试先关掉半成品弹窗再打开，避免区县/街道级联卡死
    dialog = _ensure_address_dialog(page, frame, retries=4, fresh=True)
    if dialog is None:
        result["error"] = "住所填报弹窗未出现"
        _shot(page, case_id, "address-dialog-missing")
        return result
    result["opened"] = True
    result["freshOpen"] = True
    _shot(page, case_id, "address-dialog")

    detail = str(addr.get("detail") or "").strip()
    parsed = _parse_shanghai_address(detail)
    # 优先用户结构化字段；无则回落 detail 解析；不设默认值
    district = (
        _normalize_district_name(str(addr.get("district") or ""))
        or _normalize_district_name(parsed.get("district") or "")
        or _district_from_payload(payload)
        or ""
    )
    street = (str(addr.get("street") or "").strip() or (parsed.get("street") or "").strip())
    road = (str(addr.get("road") or "").strip() or (parsed.get("road") or "").strip())
    lane = (str(addr.get("lane") or "").strip() or (parsed.get("lane") or "").strip())
    number = (str(addr.get("number") or "").strip() or (parsed.get("number") or "").strip())
    number_end = (str(addr.get("numberEnd") or "").strip())
    building = (str(addr.get("building") or "").strip() or (parsed.get("building") or "").strip())
    room = (str(addr.get("room") or "").strip() or (parsed.get("room") or "").strip())
    odd_even = (str(addr.get("oddEvenMode") or "").strip() or "不连号")
    postal = (addr.get("postalCode") or "").strip()
    street_try = _street_candidates(district, street, road) if (street or road) else []
    if street and street not in street_try:
        street_try = [street] + [c for c in street_try if c != street]
    result["detailSource"] = detail or "(structured)"
    result["parsed"] = parsed
    result["streetCandidates"] = street_try
    result["wanted"] = {
        "district": district,
        "street": street,
        "road": road,
        "lane": lane,
        "number": number,
        "numberEnd": number_end,
        "oddEvenMode": odd_even,
        "building": building,
        "room": room,
        "postal": postal,
    }

    if not district:
        # 结构化字段缺失时再尝试从 detail 解析（兼容旧草稿）
        if not detail:
            result["error"] = "住所缺少区县/街道/路名（请在客户端重新选择住所后提交）"
            result["customerHint"] = "请返回「企业信息」按区县→街道→路→号→室填写完整住所，确认页应能看到完整地址"
            _shot(page, case_id, "address-incomplete")
            return result
        district = _normalize_district_name(parsed.get("district") or "") or ""
        if not district:
            result["error"] = "住所缺少区县"
            _shot(page, case_id, "address-incomplete")
            return result

    # 先选「是否自贸区地址」= 其他非自贸区域，否则区县下拉可能不加载选项
    try:
        for label in ("其他非自贸区域", "非自贸区", "其他非自贸"):
            lab = dialog.locator("label.el-radio, .el-radio").filter(has_text=label).first
            if _visible(lab, timeout=400):
                try:
                    lab.click(timeout=2500)
                except Exception:
                    lab.click(timeout=2500, force=True)
                result["ftz"] = label
                page.wait_for_timeout(400)
                break
    except Exception as e:
        result["ftzError"] = str(e)

    dialog = _ensure_address_dialog(page, frame) or dialog
    if _pick_region_select(
        dialog,
        page,
        index=0,
        option_text=district,
        hints=["请选择区县", "区县"],
        retries=4,
    ):
        result["district"] = district
    else:
        result["districtError"] = f"区县未选中: {district}"
    page.wait_for_timeout(600)

    dialog = _ensure_address_dialog(page, frame) or dialog
    street_picked: str | None = None
    for cand in street_try:
        if not cand:
            continue
        if _pick_region_select(
            dialog,
            page,
            index=1,
            option_text=cand,
            hints=["请选择街道/乡镇", "街道/乡镇", "街道"],
            retries=2,
        ):
            street_picked = cand
            break
        dialog = _ensure_address_dialog(page, frame) or dialog
    if not street_picked and street:
        if _pick_region_select(
            dialog,
            page,
            index=1,
            option_text=street,
            hints=["请选择街道/乡镇", "街道/乡镇", "街道"],
            retries=3,
        ):
            street_picked = street
    if not street_picked and road and not street:
        street_picked = _pick_street_by_road(dialog, page, road=road, index=1)
        dialog = _ensure_address_dialog(page, frame) or dialog
    if street_picked:
        result["street"] = street_picked
    else:
        result["streetError"] = f"街道未选中: {street or (street_try[0] if street_try else '(空)')}"
    page.wait_for_timeout(900)

    dialog = _ensure_address_dialog(page, frame) or dialog
    if road:
        road_ok = False
        for _road_try in range(3):
            if _fill_road_field(dialog, page, frame, road):
                road_ok = True
                break
            page.wait_for_timeout(400)
            dialog = _ensure_address_dialog(page, frame) or dialog
        if road_ok:
            result["road"] = road
        else:
            page_txt = ""
            try:
                page_txt = re.sub(r"\s+", "", dialog.inner_text(timeout=800) or "")
            except Exception:
                pass
            if result.get("district") and result.get("street") and road in page_txt:
                result["road"] = road
                result["roadInferred"] = True
            else:
                result["roadError"] = f"路名未填选: {road}"
                _shot(page, case_id, "address-road-failed")
    else:
        result["roadError"] = "路名为空"

    dialog = _ensure_address_dialog(page, frame) or dialog
    _close_open_select_dropdowns(page, dialog)
    page.wait_for_timeout(200)
    result["oddEven"] = _pick_odd_even_mode(dialog, page, want=odd_even)
    page.wait_for_timeout(350)

    dialog = _ensure_address_dialog(page, frame) or dialog
    _close_open_select_dropdowns(page, dialog)
    page.wait_for_timeout(200)

    units = _fill_address_units(
        dialog,
        lane=lane,
        number=number,
        number_end=number_end if odd_even != "不连号" else "",
        building=building,
        room=room,
    )
    result["units"] = units
    got = units.get("values") or {}
    # 选填：用户填了才要求写上；未填不阻塞
    result["number"] = bool(units.get("numberOk")) if number else True
    result["numberValue"] = got.get("number") or ""
    result["numberEndValue"] = got.get("numberEnd") or ""
    result["numberEndOk"] = bool(units.get("numberEndOk"))
    result["room"] = bool(units.get("roomOk")) if room else True
    result["roomValue"] = got.get("room") or ""
    result["laneValue"] = got.get("lane") or ""
    result["buildingValue"] = got.get("building") or ""
    if number and not units.get("numberOk"):
        result["unitMisplaced"] = True
    if room and not units.get("roomOk"):
        result["unitMisplaced"] = True
    if lane and not units.get("laneOk"):
        result["unitMisplaced"] = True
    if building and not units.get("buildingOk"):
        result["unitMisplaced"] = True

    # 邮编
    try:
        if postal:
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
                    for i in range(dialog.locator("input.el-input__inner").count()):
                        inp = dialog.locator("input.el-input__inner").nth(i)
                        ph = inp.get_attribute("placeholder") or ""
                        if "邮" in ph or "编码" in ph:
                            inp.fill(postal)
                            result["postal"] = postal
                            break
        else:
            result["postalError"] = "邮编为空"
    except Exception as e:
        result["postalError"] = str(e)

    need_end = odd_even != "不连号" and bool(number_end or number)
    # bySuffix 已写入则以 JS 结果为准，避免 Playwright 回读空误杀
    by = (units.get("bySuffix") or {}) if isinstance(units, dict) else {}
    if building and not result.get("buildingValue"):
        js_b = str((by.get("幢") or {}).get("value") or "").strip()
        if js_b and (js_b == building or building in js_b):
            result["buildingValue"] = js_b
            result["unitMisplaced"] = False
    if room and not result.get("roomValue"):
        js_r = str((by.get("室") or {}).get("value") or "").strip()
        if js_r and (js_r == room or room in js_r):
            result["roomValue"] = js_r
            result["room"] = True
            result["unitMisplaced"] = False
    # 用户填了幢/室且 bySuffix 已写上 → 视作 units.ok
    units_ok = bool(units.get("ok"))
    if not units_ok:
        b_ok = (not building) or (
            str((by.get("幢") or {}).get("value") or "").strip() in (building, f"{building}幢")
            or building in str((by.get("幢") or {}).get("value") or "")
        )
        r_ok = (not room) or (
            str((by.get("室") or {}).get("value") or "").strip() in (room, f"{room}室")
            or room in str((by.get("室") or {}).get("value") or "")
        )
        n_ok = (not number) or (
            str(((by.get("号") or {}).get("values") or [None])[0] or (by.get("号") or {}).get("value") or "").strip()
            == number
        )
        if b_ok and r_ok and n_ok:
            units_ok = True
            result["unitMisplaced"] = False

    complete = bool(
        result.get("district")
        and result.get("street")
        and result.get("road")
        and (result.get("number") if number else True)
        and (result.get("numberEndOk") if need_end and number else True)
        and (result.get("room") if room else True)
        and units_ok
        and not result.get("unitMisplaced")
        and result.get("postal")
    )
    result["complete"] = complete
    if not complete:
        result["ok"] = False
        result["error"] = "住所未填完整（区县/街道/路名/邮编，或已填的弄号幢室未写入）"
        _shot(page, case_id, "address-incomplete")
        # 不点确定，避免写入残缺地址
        return result

    # 生成地址后点弹窗底部「确定」
    result["generateAddress"] = _click_text(dialog, ["生成地址"], exact=False)
    page.wait_for_timeout(600)
    result["ok"] = _confirm_address_dialog(page, dialog)
    if not result["ok"]:
        result["ok"] = _click_text(dialog, ["确定", "确 定"], exact=False)
    if not result["ok"]:
        result["ok"] = _click_text_any(page, ["确定"], exact=True)
    page.wait_for_timeout(800)
    if result["ok"]:
        _shot(page, case_id, "address-filled")
    else:
        result["error"] = "住所已填但未点到确定"
        _shot(page, case_id, "address-confirm-missed")
    return result


def _confirm_address_dialog(page: Page, dialog) -> bool:
    """点住所填报弹窗 footer 主按钮「确定」（避免点到示意图等其它链接）。"""
    try:
        footer = dialog.locator(".el-dialog__footer").first
        if _visible(footer, timeout=600):
            btn = footer.locator(
                "button.el-button--primary, button.el-button--primary span"
            ).filter(has_text=re.compile(r"^\s*确\s*定\s*$")).first
            if _visible(btn, timeout=500):
                try:
                    btn.click(timeout=4000)
                except Exception:
                    btn.click(timeout=4000, force=True)
                page.wait_for_timeout(400)
                # 弹窗关掉即成功
                try:
                    if not dialog.is_visible(timeout=800):
                        return True
                except Exception:
                    return True
                return True
    except Exception:
        pass
    # JS 兜底：弹窗内 footer 主按钮
    try:
        ok = bool(
            dialog.evaluate(
                """(root) => {
                  const foot = root.querySelector('.el-dialog__footer') || root;
                  const btns = [...foot.querySelectorAll('button, .el-button')];
                  const btn = btns.find(b => {
                    const t = (b.innerText || b.textContent || '').replace(/\\s+/g, '');
                    return t === '确定' && !b.disabled && !b.classList.contains('is-disabled');
                  });
                  if (!btn) return false;
                  btn.click();
                  return true;
                }"""
            )
        )
        if ok:
            page.wait_for_timeout(400)
            return True
    except Exception:
        pass
    return False


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


def _scope_items_from_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """用户勾选的经营范围：名称 + 规范码（如 I3002），与前端目录对齐。"""
    company = payload.get("company") or {}
    names = [
        str(x).strip()
        for x in (company.get("businessScopeNames") or [])
        if str(x).strip()
    ]
    codes_raw = [str(x or "").strip() for x in (company.get("businessScopeCodes") or [])]
    codes: list[str] = []
    for c in codes_raw:
        if re.fullmatch(r"[A-Za-z]+\d+", c):
            codes.append(c)
        else:
            codes.append("")
    items: list[dict[str, str]] = []
    if names:
        for i, n in enumerate(names):
            items.append({"name": n, "code": codes[i] if i < len(codes) else ""})
    elif any(codes):
        for c in codes:
            if c:
                items.append({"name": c, "code": c})
    else:
        text = str(company.get("businessScope") or "").strip()
        if text:
            text = re.sub(r"^一般项目\s*[：:]\s*", "", text)
            text = re.sub(r"[（(]除依法.*$", "", text).strip()
            for part in re.split(r"[；;]", text):
                p = part.strip().strip("、").strip()
                if len(p) >= 2:
                    items.append({"name": p, "code": ""})
    # 去重（按 name）
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for it in items:
        key = it["name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _scope_names_from_payload(payload: dict[str, Any]) -> list[str]:
    return [it["name"] for it in _scope_items_from_payload(payload)]


def _fill_business_scope_flow(
    page: Page,
    frame: Frame,
    case_id: str,
    industry: str = "科技",
    scope_names: list[str] | None = None,
    scope_items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """选择经营范围。优先按用户勾选名称/编码逐项搜索添加；无勾选时回退行业词前 5 条。"""
    result: dict[str, Any] = {"ok": False, "opened": False}
    keyword = (industry or "科技").strip() or "科技"
    items: list[dict[str, str]] = []
    if scope_items:
        items = [
            {"name": str(it.get("name") or "").strip(), "code": str(it.get("code") or "").strip()}
            for it in scope_items
            if str(it.get("name") or "").strip()
        ]
    elif scope_names:
        items = [{"name": str(n).strip(), "code": ""} for n in scope_names if str(n).strip()]
    # 去重保序
    seen_n: set[str] = set()
    uniq_items: list[dict[str, str]] = []
    for it in items:
        if it["name"] in seen_n:
            continue
        seen_n.add(it["name"])
        uniq_items.append(it)
    items = uniq_items
    user_names = [it["name"] for it in items]
    result["userScopeNames"] = user_names[:]
    result["userScopeItems"] = items[:]

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

    # 跳过引导（多步教程，直到没有「跳过」）
    skipped_n = 0
    for _ in range(8):
        hit = False
        for scope in _scope_scopes():
            if _click_text(scope, ["跳过"], exact=True):
                hit = True
                skipped_n += 1
                page.wait_for_timeout(450)
                break
        if not hit:
            break
    if skipped_n:
        result["skipped"] = True
        result["skippedSteps"] = skipped_n

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
          // 先尝试弹层内 radio
          const radios = deepAll(
            '.el-dialog input[type=radio], label.el-radio input[type=radio], input[type=radio]'
          ).filter(el => {
            const lab = el.closest('label') || el.closest('.el-radio') || el;
            const r = lab.getBoundingClientRect();
            return r.width > 4 && r.height > 4 && r.top > 0;
          });
          if (radios.length) {
            const scoredR = radios.map(inp => {
              const lab = inp.closest('label') || inp.closest('.el-radio') || inp;
              const t = (lab.innerText || '').replace(/\\s+/g,'');
              let score = 0;
              for (let i = 0; i < prefer.length; i++) {
                if (t.includes(String(prefer[i]).replace(/\\s+/g,''))) score += 100 - i;
              }
              return {inp, lab, t, score};
            }).sort((a,b) => b.score - a.score || a.lab.getBoundingClientRect().top - b.lab.getBoundingClientRect().top);
            const hit = scoredR[0];
            const inner = (hit.lab.querySelector && hit.lab.querySelector('.el-radio__inner')) || hit.inp;
            clickEl(inner);
            try {
              const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');
              if (desc && desc.set) desc.set.call(hit.inp, true); else hit.inp.checked = true;
              hit.inp.dispatchEvent(new Event('change', {bubbles:true}));
            } catch (e) {}
            return {ok:true, picked: hit.t.slice(0,60), via:'prefer-radio'};
          }
          const labels = deepAll('label, li, div, span, p').filter(el => {
            const t = (el.innerText || '').replace(/\\s+/g,'');
            const r = el.getBoundingClientRect();
            return r.width > 40 && r.height > 14 && r.height < 100 && /\\(\\d{4}\\)/.test(t) && t.length < 100;
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
          if (!target && labels.length) {
            labels.sort((a,b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
            target = labels[0];
          }
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

    def _naics_modal_present() -> bool:
        """是否仍有「国民经济行业分类选择」弹层。"""
        for scope in _scope_scopes():
            try:
                loc = scope.get_by_text("国民经济行业分类", exact=False).first
                if loc.count() > 0 and loc.is_visible(timeout=400):
                    return True
            except Exception:
                continue
            try:
                hit = scope.evaluate(
                    """() => {
                      const t = (document.body && document.body.innerText) || '';
                      return t.includes('国民经济行业分类选择') || t.includes('国民经济行业分类');
                    }"""
                )
                if hit:
                    return True
            except Exception:
                continue
        return False

    def _close_naics_modal() -> bool:
        """关闭分类弹窗（不选类目），用于多条经营范围轮换重试。"""
        js = """() => {
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
          if (!bodyText.includes('国民经济行业分类')) return {ok:false, reason:'no-modal'};
          // 优先点弹层右上角关闭
          const closes = deepAll(
            '.el-dialog__headerbtn, .el-message-box__headerbtn, .el-dialog__close, button[aria-label="Close"], .el-icon-close, i.el-icon-close, .el-dialog__headerbtn i'
          );
          for (const el of closes) {
            const r = el.getBoundingClientRect();
            if (r.width >= 8 && r.height >= 8 && r.top < window.innerHeight * 0.55) {
              if (clickEl(el)) return {ok:true, via:'close-icon'};
            }
          }
          // 文案「关闭 / 取消」（勿点确定）
          const btns = deepAll('button, a, span, div').filter(el => {
            const t = (el.innerText || '').replace(/\\s+/g,'');
            const r = el.getBoundingClientRect();
            return (t === '关闭' || t === '取消') && r.width > 24 && r.height > 14 && r.width < 200;
          });
          if (btns.length && clickEl(btns[0])) return {ok:true, via:'cancel'};
          return {ok:false, reason:'no-close'};
        }"""
        closed = False
        for scope in _scope_scopes():
            try:
                if not _naics_modal_present():
                    return True
                res = scope.evaluate(js)
                if isinstance(res, dict) and res.get("ok"):
                    closed = True
                    page.wait_for_timeout(500)
                    break
            except Exception:
                continue
        # Playwright 兜底
        if _naics_modal_present():
            for scope in _scope_scopes():
                try:
                    btn = scope.locator(
                        ".el-dialog__headerbtn, .el-dialog__close, button[aria-label='Close']"
                    ).first
                    if btn.count() > 0 and btn.is_visible(timeout=400):
                        btn.click(force=True, timeout=2000)
                        closed = True
                        page.wait_for_timeout(400)
                        break
                except Exception:
                    continue
        return closed or (not _naics_modal_present())

    def _pick_first_naics_option() -> dict[str, Any]:
        """仅一条经营范围时：默认点选弹窗第一条类目。"""
        info: dict[str, Any] = {"ok": False, "picked": None}
        js = """() => {
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
            try { el.scrollIntoView({block:'center', inline:'nearest'}); } catch (e) {}
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return false;
            const x = r.left + Math.min(16, r.width/2), y = r.top + r.height/2;
            for (const type of ['pointerdown','mousedown','mouseup','pointerup','click']) {
              el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, composed:true, clientX:x, clientY:y, view:window}));
            }
            if (typeof el.click === 'function') el.click();
            return true;
          };
          const bodyText = (document.body && document.body.innerText) || '';
          if (!bodyText.includes('国民经济行业分类')) {
            return {ok:false, reason:'no-modal'};
          }
          // 优先点弹层内第一个可见 radio（比扫 label 更稳）
          const radios = deepAll(
            '.el-dialog input[type=radio], .el-overlay-dialog input[type=radio], ' +
            'label.el-radio input[type=radio], .el-radio__original, input[type=radio]'
          ).filter(el => {
            const r = el.getBoundingClientRect();
            const lab = el.closest && (el.closest('label') || el.closest('.el-radio'));
            const rr = (lab || el).getBoundingClientRect();
            return rr.width > 4 && rr.height > 4 && rr.top > 0 && rr.bottom < innerHeight + 100;
          });
          radios.sort((a,b) => {
            const ra = (a.closest('label')||a).getBoundingClientRect();
            const rb = (b.closest('label')||b).getBoundingClientRect();
            return ra.top - rb.top;
          });
          if (radios.length) {
            const inp = radios[0];
            const lab = inp.closest('label.el-radio') || inp.closest('.el-radio') || inp.closest('label');
            const inner = (lab && lab.querySelector('.el-radio__inner')) || inp;
            clickEl(inner);
            try {
              const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');
              if (desc && desc.set) desc.set.call(inp, true); else inp.checked = true;
              inp.dispatchEvent(new Event('input', {bubbles:true}));
              inp.dispatchEvent(new Event('change', {bubbles:true}));
              clickEl(inp);
            } catch (e) {}
            return {
              ok:true,
              via:'radio0',
              picked: ((lab && lab.innerText) || '').replace(/\\s+/g,' ').trim().slice(0,60),
              radioCount: radios.length
            };
          }
          const labels = deepAll('label, li, div, span, p').filter(el => {
            const t = (el.innerText || '').replace(/\\s+/g,'');
            const r = el.getBoundingClientRect();
            return r.width > 40 && r.height > 14 && r.height < 100
              && (/\\(\\d{4}\\)/.test(t) || /\\d{4}/.test(t)) && t.length < 100;
          });
          // 按屏幕自上而下，取第一条
          labels.sort((a,b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
          if (!labels.length) return {ok:false, reason:'no-option'};
          const target = labels[0];
          const radio = target.querySelector && (
            target.querySelector('input[type=radio]') || target.querySelector('.el-radio__inner')
          );
          clickEl(radio || target);
          return {
            ok:true,
            via:'label',
            picked: (target.innerText || '').replace(/\\s+/g,' ').trim().slice(0,60),
            labelCount: labels.length
          };
        }"""
        for scope in _scope_scopes():
            try:
                if scope.get_by_text("国民经济行业分类", exact=False).count() == 0:
                    continue
            except Exception:
                continue
            try:
                js_res = scope.evaluate(js)
                info["js"] = js_res
                if isinstance(js_res, dict) and js_res.get("ok"):
                    info["ok"] = True
                    info["picked"] = js_res.get("picked")
                    page.wait_for_timeout(700)
                    break
            except Exception as e:
                info["jsError"] = str(e)
            # Playwright：弹层内第一个 radio
            try:
                dlg = scope.locator(
                    ".el-dialog:visible, .el-overlay-dialog:visible, .el-message-box:visible"
                ).filter(has_text=re.compile(r"国民经济行业分类")).first
                if dlg.count() > 0 and dlg.is_visible(timeout=500):
                    radio = dlg.locator("label.el-radio, .el-radio").first
                    if radio.count() > 0 and radio.is_visible(timeout=400):
                        try:
                            radio.scroll_into_view_if_needed(timeout=1200)
                        except Exception:
                            pass
                        inner = radio.locator(".el-radio__inner, input[type=radio]").first
                        try:
                            if inner.count() > 0 and inner.evaluate("e => e.tagName==='INPUT'"):
                                inner.check(force=True, timeout=2000)
                            elif inner.count() > 0:
                                inner.click(force=True, timeout=2500)
                            else:
                                radio.click(force=True, timeout=2500)
                        except Exception:
                            radio.click(force=True, timeout=2500)
                        info["ok"] = True
                        info["picked"] = (radio.inner_text(timeout=400) or "")[:60] or "dialog-radio-0"
                        info["via"] = "pw-dialog-radio"
                        page.wait_for_timeout(700)
                        break
            except Exception:
                pass
            # 兜底：第一个带 (四位码) 的可见行
            try:
                loc = scope.get_by_text(re.compile(r"\(\d{4}\)"), exact=False).first
                if loc.count() > 0 and loc.is_visible(timeout=600):
                    radio = loc.locator(
                        "xpath=ancestor-or-self::*[.//input[@type='radio'] or contains(@class,'el-radio')][1]"
                    ).first
                    if radio.count() > 0:
                        radio.click(force=True, timeout=2500)
                    else:
                        loc.click(force=True, timeout=2500)
                    info["ok"] = True
                    info["picked"] = (loc.inner_text(timeout=500) or "")[:60]
                    page.wait_for_timeout(700)
                    break
            except Exception:
                continue
        if info.get("ok"):
            for scope in _scope_scopes():
                for txt in ("确定", "确认", "保存", "完成", "选好了"):
                    try:
                        btn = scope.get_by_role("button", name=re.compile(rf"^\s*{txt}\s*$")).first
                        if btn.count() > 0 and btn.is_visible(timeout=400):
                            btn.click(force=True, timeout=2000)
                            info["confirmed"] = txt
                            page.wait_for_timeout(400)
                            break
                    except Exception:
                        continue
                if info.get("confirmed"):
                    break
            if _naics_modal_present():
                info["stillOpen"] = True
        return info

    def _clear_selected_scope_items() -> dict[str, Any]:
        """清空右侧已选经营范围条目，便于按新顺序重新添加。"""
        info: dict[str, Any] = {"cleared": 0}
        js = """() => {
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
            if (r.width < 3 || r.height < 3) return false;
            const x = r.left + r.width/2, y = r.top + r.height/2;
            for (const type of ['pointerdown','mousedown','mouseup','pointerup','click']) {
              el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, composed:true, clientX:x, clientY:y, view:window}));
            }
            if (typeof el.click === 'function') el.click();
            return true;
          };
          let n = 0;
          // 右侧已选区：标签关闭钮
          const closes = deepAll(
            '.el-tag__close, .el-icon-close, i[class*="close"], span[class*="close"], .el-tag .el-icon'
          ).filter(el => {
            const r = el.getBoundingClientRect();
            return r.width >= 6 && r.height >= 6 && r.x > window.innerWidth * 0.42;
          });
          for (const el of closes) {
            if (clickEl(el)) n++;
          }
          // 「删除」文字按钮（已选列表）
          const dels = deepAll('button, a, span, i').filter(el => {
            const t = (el.innerText || '').replace(/\\s+/g,'');
            const r = el.getBoundingClientRect();
            return t === '删除' && r.width > 12 && r.height > 10 && r.x > window.innerWidth * 0.40;
          });
          for (const el of dels) {
            if (clickEl(el)) n++;
          }
          return {ok:true, clicked:n};
        }"""
        for _ in range(12):
            before = _added_count(sp)
            if before <= 0:
                break
            clicked = 0
            for scope in _scope_scopes():
                try:
                    res = scope.evaluate(js)
                    if isinstance(res, dict):
                        clicked += int(res.get("clicked") or 0)
                except Exception:
                    continue
            # Playwright：右侧删除
            for scope in _scope_scopes():
                try:
                    dels = scope.get_by_text("删除", exact=True)
                    cnt = min(dels.count(), 8)
                    for i in range(cnt):
                        try:
                            d = dels.nth(i)
                            if d.is_visible(timeout=200):
                                d.click(force=True, timeout=1500)
                                clicked += 1
                                page.wait_for_timeout(200)
                        except Exception:
                            continue
                except Exception:
                    continue
            page.wait_for_timeout(350)
            after = _added_count(sp)
            info["cleared"] += max(0, before - after)
            if after <= 0 or (clicked == 0 and after == before):
                break
        info["remain"] = _added_count(sp)
        return info

    def _click_generate_result() -> bool:
        """点击「生成结果」。"""
        for scope in _scope_scopes():
            btn = scope.locator("button, a, span, div").filter(
                has_text=re.compile(r"^\s*生成结果\s*$")
            ).first
            if _visible(btn):
                try:
                    btn.click(force=True, timeout=4000)
                    return True
                except Exception:
                    pass
            if _click_text(scope, ["生成结果"], exact=True):
                return True
        for p in _find_scope_tabs(page):
            plugin = _find_scope_plugin_frame(page)
            if plugin is not None and _click_text(plugin, ["生成结果"], exact=True):
                return True
            if _click_text(p, ["生成结果"], exact=True):
                return True
        return False

    def _readd_scope_items_in_order(order_items: list[dict[str, str]]) -> dict[str, Any]:
        """按指定顺序重新搜索添加经营范围。"""
        out: dict[str, Any] = {"target": len(order_items), "ok": 0, "details": []}
        _clear_selected_scope_items()
        page.wait_for_timeout(400)
        for it in order_items:
            name = str(it.get("name") or "").strip()
            code = str(it.get("code") or "").strip()
            if not name:
                continue
            term = code if code else (name if len(name) <= 24 else name[:18])
            searched = _do_search(term)
            page.wait_for_timeout(400)
            one = _add_named_item(name, code)
            if not one.get("ok") and code and term != name:
                searched = _do_search(name if len(name) <= 24 else name[:18]) or searched
                page.wait_for_timeout(350)
                one = _add_named_item(name, code)
            one["searched"] = searched
            one["term"] = term
            out["details"].append(one)
            if one.get("ok"):
                out["ok"] += 1
            # 添加过程中若弹出分类，先关掉，留给「生成结果」阶段按规则处理
            if _naics_modal_present():
                _close_naics_modal()
        out["after"] = _added_count(sp)
        return out

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

    # 推荐弹窗：有用户勾选时只关闭弹层；否则按原逻辑添加推荐项
    added_rec = 0
    if user_names:
        if found_rec:
            for scope in _scope_scopes():
                try:
                    done = scope.get_by_text("选好了", exact=True).first
                    if done.count() > 0 and done.is_visible(timeout=600):
                        done.click(force=True, timeout=3000)
                        result["recommendDismissedForUserScopes"] = True
                        page.wait_for_timeout(600)
                        break
                except Exception:
                    continue
        result["recommendSkipped"] = "user_selected_scopes"
    elif not found_rec:
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

    if not user_names:
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
    # 推荐后若弹出分类：有用户多条勾选时先关掉，交给生成轮换；否则沿用自动点选
    if _naics_modal_present() and user_names and len(user_names) > 1:
        naics0 = {"closed": _close_naics_modal(), "mode": "defer-generate"}
    else:
        naics0 = _handle_naics_modal()
    result["naicsAfterRecommend"] = naics0
    _shot_host("scope-after-recommend")

    selected_after_rec = _added_count(sp)
    result["countAfterRecommend"] = selected_after_rec
    result["scopeKeyword"] = keyword

    def _do_search(term: str) -> bool:
        term = (term or "").strip()
        if not term:
            return False
        for scope in _scope_scopes():
            try:
                if _visible(scope.get_by_text("选好了", exact=True).first, timeout=200):
                    if _visible(scope.get_by_text("推荐的主营业务", exact=False).first, timeout=150):
                        continue
                box = scope.get_by_placeholder("请输入搜索关键字", exact=False).first
                if not _visible(box):
                    box = scope.locator("input[placeholder*='搜索'], input[placeholder*='关键字']").first
                if not _visible(box):
                    continue
                box.click(timeout=3000)
                box.fill("")
                box.fill(term)
                page.wait_for_timeout(250)
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
                page.wait_for_timeout(1600)
                return True
            except Exception:
                continue
        return False

    def _add_named_item(target_name: str, code: str = "") -> dict[str, Any]:
        """在搜索结果中按编码/名称匹配并点「添加」。"""
        info: dict[str, Any] = {"name": target_name, "code": code, "ok": False, "picked": ""}
        before = _added_count(sp)
        needle = re.sub(r"\s+", "", target_name)
        short = needle[:12] if len(needle) > 12 else needle
        code = (code or "").strip()

        # JS：在结果卡中找编码或标题完全匹配的「添加」
        js = """([name, code]) => {
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
            el.scrollIntoView({block:'center', inline:'nearest'});
            const x = r.left + r.width/2, y = r.top + r.height/2;
            for (const type of ['pointerdown','mousedown','mouseup','pointerup','click']) {
              el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, composed:true, clientX:x, clientY:y, view:window}));
            }
            if (typeof el.click === 'function') el.click();
            return true;
          };
          const norm = (s) => (s || '').replace(/\\s+/g,'').replace(/\\u00a0/g,'');
          const wantName = norm(name);
          const wantCode = norm(code);
          const addBtns = deepAll('button, a, span, div').filter(el => {
            const t = norm(el.innerText || '');
            const r = el.getBoundingClientRect();
            return t === '添加' && r.width > 24 && r.height > 14 && r.x < (window.innerWidth * 0.78);
          });
          const scored = [];
          for (const btn of addBtns) {
            let card = btn, text = '';
            for (let i = 0; i < 10 && card; i++) {
              text = card.innerText || text;
              if ((text.includes('一般事项') || text.includes('前置许可') || /[A-Z]\\d{3,}/.test(text)) && text.length > 8) break;
              card = card.parentElement;
            }
            const nt = norm(text);
            if (nt.includes('小额贷款')) continue;
            if (nt.includes('前置许可') && !nt.includes('一般事项')) continue;
            let score = 0;
            if (wantCode && nt.includes(wantCode)) score += 100;
            if (wantName && nt.includes(wantName)) score += 80;
            if (wantName && wantName.length >= 6 && nt.includes(wantName.slice(0, 8))) score += 40;
            if (score > 0) scored.push({btn, score, title: (text.split('\\n')[0] || '').slice(0, 48)});
          }
          scored.sort((a,b) => b.score - a.score);
          if (!scored.length) return {ok:false, btnCount: addBtns.length};
          const top = scored[0];
          clickEl(top.btn);
          return {ok:true, picked: top.title, score: top.score, btnCount: addBtns.length};
        }"""

        for scope in _scope_scopes():
            try:
                js_res = scope.evaluate(js, [target_name, code])
                info["js"] = js_res
                page.wait_for_timeout(800)
                try:
                    _handle_naics_modal()
                except Exception:
                    pass
                after = _added_count(sp)
                if after > before:
                    info["ok"] = True
                    info["picked"] = (js_res or {}).get("picked") or target_name
                    info["after"] = after
                    return info
            except Exception as e:
                info["jsError"] = str(e)

        # Playwright 回落：先码后名
        markers = [m for m in (code, target_name, short) if m]
        for scope in _scope_scopes():
            for marker in markers:
                try:
                    loc = scope.get_by_text(marker, exact=False).first
                    if loc.count() == 0:
                        continue
                    parent = loc.locator(
                        "xpath=ancestor::div[.//button or .//span[contains(normalize-space(.),'添')]][1]"
                    )
                    btn = parent.get_by_role("button", name=_ADD_BTN_ROLE_RE).first
                    if btn.count() == 0:
                        btn = parent.locator("button, a, span").filter(has_text=_ADD_BTN_RE).first
                    if btn.count() == 0:
                        continue
                    try:
                        parent_txt = parent.first.inner_text(timeout=600) or ""
                    except Exception:
                        parent_txt = ""
                    if "小额贷款" in parent_txt or (
                        "前置许可" in parent_txt and "一般事项" not in parent_txt
                    ):
                        continue
                    btn.first.click(force=True, timeout=3000)
                    page.wait_for_timeout(700)
                    try:
                        _handle_naics_modal()
                    except Exception:
                        pass
                    after = _added_count(sp)
                    if after > before:
                        info["ok"] = True
                        info["picked"] = (parent_txt.split("\n")[0] or target_name)[:50]
                        info["after"] = after
                        return info
                except Exception as e:
                    info["error"] = str(e)
                    continue
        info["after"] = _added_count(sp)
        return info

    if user_names:
        # 按用户勾选逐项搜索添加；禁止回退「搜科技前5」以免与用户选择不一致
        _shot_host("scope-before-user-names")
        # 添加前再清一次引导
        for _ in range(4):
            cleared = False
            for scope in _scope_scopes():
                if _click_text(scope, ["跳过"], exact=True):
                    cleared = True
                    page.wait_for_timeout(350)
                    break
            if not cleared:
                break

        add_details: list[dict[str, Any]] = []
        ok_n = 0
        for it in items:
            name = it["name"]
            code = it.get("code") or ""
            term = code if code else (name if len(name) <= 24 else name[:18])
            searched_one = _do_search(term)
            page.wait_for_timeout(500)
            one = _add_named_item(name, code)
            if not one.get("ok") and code and term != name:
                # 编码搜不到时再用名称搜
                searched_one = _do_search(name if len(name) <= 24 else name[:18]) or searched_one
                page.wait_for_timeout(400)
                one = _add_named_item(name, code)
            one["searched"] = searched_one
            one["term"] = term
            add_details.append(one)
            if one.get("ok"):
                ok_n += 1
            _shot_host(f"scope-user-add-{ok_n}")
            # 添加时若弹出分类：多条先关掉，留给「生成结果」按轮换规则处理；单条点第一项
            try:
                if _naics_modal_present():
                    if len(items) <= 1:
                        _pick_first_naics_option()
                    else:
                        _close_naics_modal()
            except Exception:
                pass

        after_cnt = _added_count(sp)
        add_info = {
            "mode": "user_selected",
            "target": len(items),
            "added": ok_n,
            "after": after_cnt,
            "details": add_details,
            "picked": [d.get("picked") or d.get("name") for d in add_details if d.get("ok")],
        }
        result["searched"] = any(d.get("searched") for d in add_details)
        result["addInfo"] = add_info
        result["addedTop5"] = ok_n
        result["selectedCount"] = after_cnt
        result["userScopeOk"] = ok_n
        result["userScopeMiss"] = [d.get("name") for d in add_details if not d.get("ok")]
        # 用户有勾选时：必须全部加上才算成功，绝不回退行业词
        if ok_n < len(items):
            result["error"] = f"用户勾选 {len(items)} 项，仅成功添加 {ok_n} 项"
    else:
        searched = _do_search(keyword)
        result["searched"] = searched
        _shot_host(f"scope-search-{keyword}")

        # 搜索行业词后：必须添加结果列表前 5 条
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
    selected_n = int(result.get("selectedCount") or selected_after_rec or 0)
    # 添加后若仍有分类弹窗：单条默认第一项；多条关掉，交给生成阶段轮换
    if _naics_modal_present():
        if (items and len(items) <= 1) or (not items and selected_n <= 1):
            naics1 = _pick_first_naics_option()
        else:
            naics1 = {"closed": _close_naics_modal(), "mode": "defer-generate"}
    else:
        naics1 = {"seen": False}
    result["naicsAfterAdd"] = naics1
    _shot_host("scope-after-add")

    gen = False
    user_all_ok = (not user_names) or (int(result.get("userScopeOk") or 0) >= len(user_names))
    if selected_n > 0 and user_all_ok:
        # 生成前若仍有分类弹窗：多条先关掉（交给轮换逻辑）；单条直接点第一项
        if _naics_modal_present():
            if selected_n <= 1 or len(items) == 1:
                result["naicsBeforeGenerate"] = _pick_first_naics_option()
            else:
                result["naicsBeforeGenerate"] = {"closed": _close_naics_modal(), "mode": "defer-rotate"}
        else:
            result["naicsBeforeGenerate"] = {"seen": False}

        # 用于轮换重加的条目顺序（用户勾选优先；否则用已成功添加的名称）
        rotate_items: list[dict[str, str]] = []
        if items:
            rotate_items = [
                {"name": str(it.get("name") or "").strip(), "code": str(it.get("code") or "").strip()}
                for it in items
                if str(it.get("name") or "").strip()
            ]
        else:
            picked_names = list((result.get("addInfo") or {}).get("picked") or [])
            rotate_items = [{"name": str(n).strip(), "code": ""} for n in picked_names if str(n).strip()]
        if not rotate_items and user_names:
            rotate_items = [{"name": n, "code": ""} for n in user_names]

        rotation_logs: list[dict[str, Any]] = []
        max_rounds = max(1, len(rotate_items))

        for rot in range(max_rounds):
            round_info: dict[str, Any] = {"rot": rot, "order": []}
            if rot > 0 and len(rotate_items) > 1:
                # 第 rot 轮：从第 rot+1 条开始添加，前面的放到最后
                order = rotate_items[rot:] + rotate_items[:rot]
                round_info["order"] = [x["name"] for x in order]
                if _naics_modal_present():
                    _close_naics_modal()
                readd = _readd_scope_items_in_order(order)
                round_info["readd"] = readd
                result["selectedCount"] = int(readd.get("after") or _added_count(sp))
                _shot_host(f"scope-rotate-{rot}")
            else:
                round_info["order"] = [x["name"] for x in rotate_items]

            clicked = _click_generate_result()
            round_info["clickedGenerate"] = clicked
            if clicked:
                gen = True
            page.wait_for_timeout(1500)

            if not _naics_modal_present():
                round_info["naics"] = {"seen": False}
                rotation_logs.append(round_info)
                break

            # 出现分类弹窗
            if len(rotate_items) <= 1:
                # 仅一条：默认点弹窗第一条类目
                pick = _pick_first_naics_option()
                round_info["naics"] = {"seen": True, "mode": "single-first", **pick}
                rotation_logs.append(round_info)
                result["naicsAfterGenerate"] = pick
                if pick.get("ok"):
                    page.wait_for_timeout(600)
                    if _click_generate_result():
                        gen = True
                        page.wait_for_timeout(1000)
                break

            # 多条：关闭弹窗，下一轮轮换添加顺序
            closed = _close_naics_modal()
            round_info["naics"] = {"seen": True, "mode": "rotate", "closed": closed}
            rotation_logs.append(round_info)
            # 若已是最后一轮仍弹窗：兜底点第一项，避免卡死
            if rot >= max_rounds - 1:
                pick = _pick_first_naics_option()
                result["naicsAfterGenerate"] = {"fallbackFirst": True, **pick}
                if pick.get("ok"):
                    page.wait_for_timeout(500)
                    if _click_generate_result():
                        gen = True
                        page.wait_for_timeout(1000)
                break
            continue

        result["scopeGenerateRotations"] = rotation_logs
        if rotation_logs and "naicsAfterGenerate" not in result:
            last_naics = (rotation_logs[-1] or {}).get("naics") or {}
            result["naicsAfterGenerate"] = last_naics

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
        # 用户勾选模式：必须全部添加成功
        if user_names and int(result.get("userScopeOk") or 0) < len(user_names):
            result["ok"] = False
        if not result["ok"] and selected_n > 0 and (not user_names or user_all_ok):
            # 末次补救：若插件仍开着，再选行业分类并生成
            for p in page.context.pages:
                u = p.url or ""
                if "jyfwyun.com" in u or "sourceType=STRD" in u or "flag=trd" in u:
                    try:
                        p.bring_to_front()
                    except Exception:
                        pass
                    # 末次补救：分类弹窗按同样规则处理
                    if _naics_modal_present():
                        if selected_n <= 1 or len(items) <= 1:
                            result["naicsRescue"] = _pick_first_naics_option()
                        else:
                            # 轮换已穷尽时兜底点第一项，避免卡死
                            result["naicsRescue"] = {
                                "fallbackFirst": True,
                                **_pick_first_naics_option(),
                            }
                    if _click_generate_result():
                        result["generatedRescue"] = True
                        page.wait_for_timeout(2500)
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
    if _dismiss_system_busy(page):
        page.wait_for_timeout(1200)

    dialogs = handle_post_name_dialogs(page, case_id)
    if dialogs.get("compareFailed"):
        store.set_status(
            case_id,
            statuses.NAME_FAILED,
            reason="字号核名未通过（备用字号/比对未通过），需返回修改换下一字号",
            customer_message="当前字号未通过，正在尝试返回修改并换下一字号…",
        )
        return {"ok": False, "nameFailed": True, "reason": "比对未通过/备用字号", "dialogs": dialogs}
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

    from apps.worker.playbooks.org_fill_profile import profile_for

    profile = profile_for(payload)
    result["orgProfile"] = {
        "orgForm": profile.get("orgForm"),
        "skipCompanyType": profile.get("skip_company_type"),
        "agentTypes": profile.get("agent_types"),
    }

    result["mobile"] = _fill_label(frame, "移动电话", mobile)
    result["agentType"] = _pick_select(frame, "经办人类型", list(profile.get("agent_types") or ["法定代表人"]))
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

    if profile.get("skip_company_type"):
        result["companyType"] = "skipped"
        result["companyTypeSkipped"] = True
    else:
        result["companyType"] = _pick_select(
            frame,
            "企业类型",
            list(profile.get("company_types") or []),
        )
    result["term"] = _pick_select(frame, "经营期限", ["不约定期限"])

    # 住所弹窗
    addr_result = _fill_address_dialog(page, frame, payload, case_id)
    _shot(page, case_id, "address-filled")
    result["address"] = addr_result

    result["contact"] = _fill_label(frame, "联系电话", mobile)

    today = date.today().isoformat()
    result["resolutionDate"] = False
    for lab in profile.get("resolution_labels") or ["章程决议日期"]:
        ok = _fill_label(frame, lab, today)
        if ok:
            result["resolutionDate"] = True
            result["resolutionLabel"] = lab
            break
        try:
            di = frame.get_by_placeholder(f"请选择{lab}").first
            if _visible(di):
                di.click(timeout=3000)
                page.wait_for_timeout(400)
                cell = frame.locator("td.available.today, td.today").first
                if not _visible(cell):
                    cell = page.locator("td.available.today, td.today").first
                if _visible(cell):
                    cell.click(timeout=3000)
                    result["resolutionDate"] = True
                    result["resolutionLabel"] = lab
                    break
                di.fill(today)
                result["resolutionDate"] = True
                result["resolutionLabel"] = lab
                break
        except Exception:
            continue

    # 暂存（之后页面常短暂白屏，需等经营范围区恢复）
    result["saved"] = _click_text(frame, ["暂存"], exact=True)
    if not result["saved"]:
        result["saved"] = _click_text_any(page, ["暂存"], exact=True)
    page.wait_for_timeout(1200)
    frame = _wait_basic_stable(page, timeout_sec=25) or frame
    _shot(page, case_id, "after-draft-save")

    # 经营范围：住所未完成则跳过，避免继续推进
    industry = ((payload.get("name") or {}).get("industry") or "科技").strip() or "科技"
    scope_items = _scope_items_from_payload(payload)
    scope_names = [it["name"] for it in scope_items]
    if not addr_result.get("ok"):
        scope = {
            "ok": False,
            "skipped": True,
            "reason": "住所未完成，跳过经营范围",
        }
    else:
        scope = _fill_business_scope_flow(
            page,
            frame,
            case_id,
            industry=industry,
            scope_items=scope_items,
            scope_names=scope_names,
        )
        if not scope.get("ok"):
            # 若上次已开过经营范围页，先关掉再重试，避免叠多个 master 窗口
            _close_scope_tabs(page)
            frame = _frame_basic(page, timeout_ms=20000) or frame
            scope2 = _fill_business_scope_flow(
                page,
                frame,
                case_id,
                industry=industry,
                scope_items=scope_items,
                scope_names=scope_names,
            )
            scope = {**scope, "retry": scope2}
            if scope2.get("ok"):
                scope = scope2
    result["businessScope"] = scope
    result["scopeNamesUsed"] = scope_names
    result["scopeItemsUsed"] = scope_items
    # 确保回到设立页（经营范围标签已关）
    _close_scope_tabs(page)
    frame = _frame_basic(page, timeout_ms=20000) or frame

    # 下拉到底 → 下一步（住所或经营范围未完成则不点）
    try:
        frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        try:
            page.mouse.wheel(0, 2000)
        except Exception:
            pass
    page.wait_for_timeout(500)
    can_next = bool(addr_result.get("ok") and scope.get("ok"))
    if can_next:
        result["nextClicked"] = False
        for _ in range(3):
            _dismiss_system_busy(page)
            clicked = _click_text(frame, ["下一步"], exact=True)
            if not clicked:
                clicked = _click_text_any(page, ["下一步"], exact=True)
            page.wait_for_timeout(1500)
            if _dismiss_system_busy(page):
                page.wait_for_timeout(1200)
                continue
            result["nextClicked"] = bool(clicked)
            break
    else:
        result["nextClicked"] = False
        reasons = []
        if not addr_result.get("ok"):
            reasons.append("住所未完成")
        if not scope.get("ok"):
            reasons.append("经营范围未完成")
        result["nextSkippedReason"] = "；".join(reasons) or "前置步骤未完成"
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
