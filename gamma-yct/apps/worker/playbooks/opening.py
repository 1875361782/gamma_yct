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


def _deep_click_radio_js(scope: Page | Frame, needle: str) -> dict[str, Any]:
    """在 document / open shadow 内深度查找并真实点击 radio（含 Vue/Element）。"""
    try:
        return (
            scope.evaluate(
                """(needle) => {
                  const norm = (s) => (s || '').replace(/\\s+/g, '');
                  const n = norm(needle);
                  const roots = [];
                  const walk = (root) => {
                    if (!root) return;
                    roots.push(root);
                    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                    for (const el of all) {
                      if (el.shadowRoot) walk(el.shadowRoot);
                    }
                  };
                  walk(document);
                  const app = document.querySelector('wujie-app');
                  if (app && app.shadowRoot) walk(app.shadowRoot);

                  const cands = [];
                  for (const root of roots) {
                    const nodes = root.querySelectorAll
                      ? root.querySelectorAll('label.el-radio, label, .el-radio, [role="radio"]')
                      : [];
                    for (const lab of nodes) {
                      const t = norm(lab.innerText || lab.textContent || '');
                      if (!t.includes(n)) continue;
                      if (t.length > 80) continue;
                      const r = lab.getBoundingClientRect();
                      if (r.width < 8 || r.height < 8 || r.bottom < 0 || r.top > innerHeight) continue;
                      const disabled =
                        (lab.className || '').includes('is-disabled') ||
                        lab.getAttribute('aria-disabled') === 'true' ||
                        !!(lab.querySelector && lab.querySelector('input[disabled]'));
                      cands.push({lab, len: t.length, y: r.y, disabled, t: t.slice(0, 40)});
                    }
                  }
                  cands.sort((a, b) => a.len - b.len || a.y - b.y);
                  const hit = cands.find(c => !c.disabled) || cands[0];
                  if (!hit) return {ok:false, reason:'not-found', cand: cands.length};

                  const lab = hit.lab;
                  lab.scrollIntoView({block:'center'});
                  const fire = (el) => {
                    if (!el) return;
                    try { el.focus && el.focus(); } catch (e) {}
                    for (const type of ['pointerdown','mousedown','mouseup','click']) {
                      el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, view:window}));
                    }
                    try { el.click(); } catch (e) {}
                  };
                  const inner = lab.querySelector && (
                    lab.querySelector('.el-radio__inner') ||
                    lab.querySelector('.el-radio__input') ||
                    lab.querySelector('input[type=radio]')
                  );
                  fire(inner || lab);

                  const inp = lab.querySelector && lab.querySelector('input[type=radio]');
                  if (inp) {
                    try {
                      const desc = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'checked'
                      );
                      if (desc && desc.set) desc.set.call(inp, true);
                      else inp.checked = true;
                      inp.dispatchEvent(new Event('input', {bubbles:true}));
                      inp.dispatchEvent(new Event('change', {bubbles:true}));
                      // ElementUI 常监听 click
                      fire(inp);
                    } catch (e) {}
                  }
                  const checked =
                    (lab.className || '').includes('is-checked') ||
                    !!(inp && inp.checked);
                  return {
                    ok: true,
                    checked,
                    disabled: !!hit.disabled,
                    text: hit.t,
                    cand: cands.length,
                    cls: String(lab.className || '').slice(0, 80),
                  };
                }""",
                needle,
            )
            or {"ok": False}
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _click_radio(page: Page, needles: list[str]) -> bool:
    """点选办理方式等 radio：优先 Playwright 穿透定位，再 JS 深度点击。"""
    for needle in needles:
        for scope in _all_scopes(page):
            # A) Playwright：label.el-radio / .el-radio
            for sel in (
                "label.el-radio",
                ".el-radio",
                "label",
                "[role='radio']",
            ):
                try:
                    labels = scope.locator(sel).filter(has_text=re.compile(re.escape(needle)))
                    n = min(labels.count(), 10)
                    best = None
                    best_len = 10**9
                    for i in range(n):
                        lab = labels.nth(i)
                        if not _visible(lab, timeout=250):
                            continue
                        try:
                            cls = lab.get_attribute("class") or ""
                            if "is-disabled" in cls:
                                continue
                        except Exception:
                            pass
                        txt = " ".join((lab.inner_text(timeout=300) or "").split())
                        if needle not in txt or len(txt) > 100:
                            continue
                        if len(txt) < best_len:
                            best = lab
                            best_len = len(txt)
                    if best is None:
                        continue
                    try:
                        best.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass
                    # 依次：inner → input.check → label → 坐标点圆点
                    clicked = False
                    for sub in (".el-radio__inner", ".el-radio__input", "input[type='radio']"):
                        try:
                            el = best.locator(sub).first
                            if el.count() == 0:
                                continue
                            if sub.startswith("input"):
                                el.check(force=True, timeout=2000)
                            else:
                                el.click(force=True, timeout=2500)
                            clicked = True
                            break
                        except Exception:
                            continue
                    if not clicked:
                        try:
                            best.click(force=True, timeout=3000)
                            clicked = True
                        except Exception:
                            box = best.bounding_box()
                            if box:
                                page.mouse.click(box["x"] + 10, box["y"] + box["height"] / 2)
                                clicked = True
                    page.wait_for_timeout(350)
                    if _radio_checked(page, needle):
                        return True
                    if clicked:
                        # 坐标再点一次圆点区域
                        box = best.bounding_box()
                        if box:
                            page.mouse.click(box["x"] + 8, box["y"] + box["height"] / 2)
                            page.wait_for_timeout(300)
                            if _radio_checked(page, needle):
                                return True
                except Exception:
                    continue

            # B) 从文案向上找 radio 祖先
            try:
                loc = scope.get_by_text(needle, exact=False).first
                if _visible(loc, timeout=400):
                    anc = loc.locator(
                        "xpath=ancestor-or-self::*[contains(@class,'el-radio') or "
                        ".//input[@type='radio'] or @role='radio'][1]"
                    ).first
                    target = anc if _visible(anc, timeout=200) else loc
                    try:
                        target.scroll_into_view_if_needed(timeout=1200)
                    except Exception:
                        pass
                    try:
                        inner = target.locator(".el-radio__inner, input[type='radio']").first
                        if _visible(inner, timeout=200):
                            if inner.evaluate("e => e.tagName==='INPUT'"):
                                inner.check(force=True, timeout=2000)
                            else:
                                inner.click(force=True, timeout=2500)
                        else:
                            target.click(force=True, timeout=2500)
                    except Exception:
                        box = target.bounding_box()
                        if box:
                            page.mouse.click(box["x"] + 8, box["y"] + box["height"] / 2)
                    page.wait_for_timeout(350)
                    if _radio_checked(page, needle):
                        return True
            except Exception:
                pass

            # C) JS 深度点击
            js = _deep_click_radio_js(scope, needle)
            if js.get("ok"):
                page.wait_for_timeout(350)
                if _radio_checked(page, needle) or js.get("checked"):
                    return True
    return False


def _radio_checked(page: Page, needle: str) -> bool:
    for scope in _all_scopes(page):
        try:
            js = scope.evaluate(
                """(needle) => {
                  const norm = (s) => (s || '').replace(/\\s+/g, '');
                  const n = norm(needle);
                  const roots = [];
                  const walk = (root) => {
                    if (!root) return;
                    roots.push(root);
                    const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
                    for (const el of all) {
                      if (el.shadowRoot) walk(el.shadowRoot);
                    }
                  };
                  walk(document);
                  const app = document.querySelector('wujie-app');
                  if (app && app.shadowRoot) walk(app.shadowRoot);
                  for (const root of roots) {
                    const nodes = root.querySelectorAll
                      ? root.querySelectorAll('label.el-radio, label, .el-radio, [role="radio"]')
                      : [];
                    for (const lab of nodes) {
                      const t = norm(lab.innerText || '');
                      if (!t.includes(n) || t.length > 80) continue;
                      if ((lab.className || '').includes('is-checked')) return true;
                      const inp = lab.querySelector && lab.querySelector('input[type=radio]');
                      if (inp && inp.checked) return true;
                      if (lab.getAttribute && lab.getAttribute('aria-checked') === 'true') return true;
                    }
                  }
                  return false;
                }""",
                needle,
            )
            if js:
                return True
        except Exception:
            pass
        try:
            lab = scope.locator("label.el-radio, .el-radio, label").filter(
                has_text=re.compile(re.escape(needle))
            ).first
            if not _visible(lab, timeout=350):
                continue
            cls = lab.get_attribute("class") or ""
            if "is-checked" in cls:
                return True
            if (lab.get_attribute("aria-checked") or "") == "true":
                return True
            inp = lab.locator("input[type='radio']").first
            if inp.count() > 0 and inp.is_checked():
                return True
        except Exception:
            continue
    return False


def _probe_radios(page: Page) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, scope in enumerate(_all_scopes(page)):
        try:
            rows = scope.evaluate(
                """() => {
                  const rows = [];
                  const nodes = document.querySelectorAll(
                    'label.el-radio, .el-radio, [role="radio"]'
                  );
                  for (const lab of [...nodes].slice(0, 16)) {
                    const r = lab.getBoundingClientRect();
                    rows.push({
                      text: (lab.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 50),
                      cls: String(lab.className || '').slice(0, 60),
                      disabled: (lab.className || '').includes('is-disabled'),
                      checked: (lab.className || '').includes('is-checked') ||
                        !!(lab.querySelector('input[type=radio]') &&
                           lab.querySelector('input[type=radio]').checked),
                      w: Math.round(r.width), h: Math.round(r.height),
                    });
                  }
                  return rows;
                }"""
            )
            if rows:
                out.append({"scope": idx, "rows": rows})
        except Exception as e:
            out.append({"scope": idx, "error": str(e)})
    return out


def _option_present(page: Page, needle: str) -> bool:
    return _page_has(page, needle)


def _wait_radio_enabled(page: Page, needle: str, timeout_sec: float = 8) -> bool:
    import time

    end = time.time() + timeout_sec
    while time.time() < end:
        for scope in _all_scopes(page):
            try:
                ok = scope.evaluate(
                    """(needle) => {
                      const norm = (s) => (s || '').replace(/\\s+/g, '');
                      const n = norm(needle);
                      for (const lab of document.querySelectorAll(
                        'label.el-radio, .el-radio, label, [role="radio"]'
                      )) {
                        const t = norm(lab.innerText || '');
                        if (!t.includes(n) || t.length > 80) continue;
                        if ((lab.className || '').includes('is-disabled')) return false;
                        const inp = lab.querySelector('input[type=radio]');
                        if (inp && inp.disabled) return false;
                        return true;
                      }
                      return false;
                    }""",
                    needle,
                )
                if ok:
                    return True
            except Exception:
                continue
        page.wait_for_timeout(300)
    return _option_present(page, needle)


def _ensure_radio(page: Page, needles: list[str], *, retries: int = 5) -> bool:
    for _ in range(retries):
        for n in needles:
            if _radio_checked(page, n):
                return True
        _click_radio(page, needles)
        page.wait_for_timeout(450)
        for n in needles:
            if _radio_checked(page, n):
                return True
        page.wait_for_timeout(350)
    return any(_radio_checked(page, n) for n in needles)


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


def _on_material_preview_page(page: Page) -> bool:
    """开办后材料预览页（个人独资常跳过五险/刻章直接落到此页）。"""
    if _page_has(page, "系统已自动为您生成"):
        return True
    if _page_has(page, "材料预览") and (
        _page_has(page, "重新生成")
        or _page_has(page, "申请书")
        or _page_has(page, "住所使用相关文件")
    ):
        return True
    if _page_has(page, "重新生成") and _page_has(page, "预览") and (
        _page_has(page, "个人独资") or _page_has(page, "登记")
    ):
        return True
    return False


def _mark_opening_done(result: dict[str, Any], *, reason: str) -> None:
    """材料预览/开办已越过：标成成功，供流水线回写 READY_FOR_USER_SUBMIT。"""
    result["ok"] = True
    result["missing"] = []
    result["error"] = None
    result["completedVia"] = reason
    for k in (
        "fullOnline",
        "smartDoc",
        "noMaterialGuide",
        "noPaperLicense",
        "next1",
        "next2",
        "next3",
        "next4",
        "selfPayNoSeal",
        "saved",
    ):
        result[k] = True
    result["savedVia"] = result.get("savedVia") or reason


def _finish_opening_done(
    case_id: str, page: Page, result: dict[str, Any], *, reason: str
) -> dict[str, Any]:
    _mark_opening_done(result, reason=reason)
    try:
        result["currentUrl"] = getattr(page, "url", "") or ""
    except Exception:
        pass
    _shot(page, case_id, "opening-material-preview")
    _dump(case_id, "fill-opening-result.json", result)
    return result


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
    """人员完成后进入办理方式；已在办理方式/材料预览则跳过。"""
    if _on_material_preview_page(page):
        result["enterSkipped"] = "already-on-material-preview"
        return page
    if _on_process_page(page):
        result["enterSkipped"] = "already-on-opening"
        return page
    for i in range(4):
        if _on_material_preview_page(page):
            result["enterSkipped"] = "landed-material-preview"
            return page
        hit = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
        result[f"enterNext{i}"] = hit
        page.wait_for_timeout(1200)
        _wait_any(
            page,
            ["后续办理方式", "全程网办", "文书产生方式", "材料预览", "系统已自动为您生成"],
            timeout_sec=10,
        )
        if _on_process_page(page):
            result["enteredOpening"] = True
            _shot(page, case_id, "opening-page")
            return page
        if _on_material_preview_page(page):
            result["enteredOpening"] = True
            result["enterSkipped"] = "landed-material-preview"
            return page
        _click_text_any(page, ["已完成，进入下一步", "确定", "确认"], exact=False)
        page.wait_for_timeout(800)
        if _on_process_page(page) or _on_material_preview_page(page):
            result["enteredOpening"] = True
            _shot(page, case_id, "opening-page")
            return page
    result["enteredOpening"] = _on_process_page(page) or _on_material_preview_page(page)
    _shot(page, case_id, "opening-page" if result["enteredOpening"] else "opening-enter-miss")
    return page


def run_opening_flow(case_id: str, page: Page, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from apps.worker.playbooks.retry_gate import dismiss_system_busy

    _ = payload
    store.set_status(
        case_id,
        statuses.CHOOSING_PROCESS,
        customer_message="正在填写企业开办 / 后续办理方式…",
    )
    if dismiss_system_busy(page):
        page.wait_for_timeout(1200)
    result: dict[str, Any] = {"ok": False}

    # 已到材料预览：开办完成，直接回写成功
    if _on_material_preview_page(page):
        return _finish_opening_done(
            case_id, page, result, reason="already-on-material-preview"
        )

    page = _enter_opening(page, case_id, result)
    if _on_material_preview_page(page):
        return _finish_opening_done(
            case_id, page, result, reason="enter-landed-material-preview"
        )

    mid_flow = (
        _on_social_page(page)
        or _on_seal_page(page)
        or _page_has(page, "本次同步办理预约银行开户")
        or _page_has(page, "涉税事项")
    )
    if not _on_process_page(page) and not mid_flow:
        result["error"] = "未进入企业开办 / 后续办理方式页"
        result["missing"] = ["进入企业开办"]
        result["currentUrl"] = getattr(page, "url", "") or ""
        _dump(case_id, "fill-opening-result.json", result)
        return result

    if _on_process_page(page):
        result["radioProbeBefore"] = _probe_radios(page)
        result["fullOnlineEnabled"] = _wait_radio_enabled(page, "全程网办", timeout_sec=10)
        result["fullOnline"] = _ensure_radio(page, ["全程网办"])
        page.wait_for_timeout(600)
        if _option_present(page, "不需要材料辅导") or _option_present(
            page, "无需登记机关对登记材料进行辅导"
        ):
            result["noMaterialGuide"] = _ensure_radio(
                page, ["不需要材料辅导", "无需登记机关对登记材料进行辅导"]
            )
        else:
            result["noMaterialGuide"] = True
            result["noMaterialGuideSkipped"] = "option-absent"
        page.wait_for_timeout(400)
        result["smartDocEnabled"] = _wait_radio_enabled(page, "智慧文书", timeout_sec=10)
        if not result["smartDocEnabled"] and result["fullOnline"]:
            _ensure_radio(page, ["全程网办"])
            page.wait_for_timeout(500)
            result["smartDocEnabled"] = _wait_radio_enabled(page, "智慧文书", timeout_sec=8)
        result["smartDoc"] = _ensure_radio(page, ["智慧文书"])
        page.wait_for_timeout(300)
        if _option_present(page, "无需领取纸质营业执照"):
            result["noPaperLicense"] = _ensure_radio(page, ["无需领取纸质营业执照"])
        else:
            result["noPaperLicense"] = True
            result["noPaperLicenseSkipped"] = "option-absent"
        page.wait_for_timeout(400)
        if not result["fullOnline"] or not result["smartDoc"]:
            result["fullOnlineRetry"] = _ensure_radio(page, ["全程网办"])
            page.wait_for_timeout(400)
            result["smartDocRetry"] = _ensure_radio(page, ["智慧文书"])
            result["fullOnline"] = bool(result["fullOnline"] or result["fullOnlineRetry"])
            result["smartDoc"] = bool(result["smartDoc"] or result["smartDocRetry"])
        result["radioProbeAfter"] = _probe_radios(page)
        _shot(page, case_id, "opening-process-selected")

        if not result["fullOnline"] or not result["smartDoc"]:
            result["error"] = "办理方式单选未生效"
            result["missing"] = [
                k
                for k, ok in (
                    ("全程网办", result["fullOnline"]),
                    ("智慧文书", result["smartDoc"]),
                )
                if not ok
            ]
            _dump(case_id, "fill-opening-result.json", result)
            return result

        leave_ok = [
            "本次是否办理五险一金",
            "自费刻章",
            "设立时暂不刻章",
            "设立时候暂不刻章",
            "材料预览",
            "系统已自动为您生成",
            "重新生成",
        ]
        result["next1"] = _click_next_and_wait(
            page,
            leave_ok,
            timeout_sec=25,
            leave_markers=["后续办理方式", "文书产生方式"],
        )
        if not result["next1"] and _on_process_page(page):
            _ensure_radio(page, ["全程网办"])
            _ensure_radio(page, ["智慧文书"])
            page.wait_for_timeout(400)
            result["next1Retry"] = _click_next_and_wait(
                page,
                leave_ok,
                timeout_sec=20,
                leave_markers=["后续办理方式", "文书产生方式"],
            )
            result["next1"] = bool(result["next1"] or result["next1Retry"])
        _shot(page, case_id, "opening-after-next1")
        if _on_material_preview_page(page):
            return _finish_opening_done(
                case_id, page, result, reason="after-process-to-material-preview"
            )
        result["afterNext1"] = (
            "social"
            if _on_social_page(page)
            else ("seal" if _on_seal_page(page) else "other")
        )
    else:
        result["processSkipped"] = "mid-flow-resume"
        result["fullOnline"] = True
        result["smartDoc"] = True
        result["noMaterialGuide"] = True
        result["noPaperLicense"] = True
        result["next1"] = True

    if _on_material_preview_page(page):
        return _finish_opening_done(
            case_id, page, result, reason="before-seal-material-preview"
        )
    if not _on_seal_page(page) and not _on_material_preview_page(page):
        result["next2"] = _click_next_and_wait(
            page,
            [
                "自费刻章",
                "设立时候暂不刻章",
                "设立时暂不刻章",
                "暂不刻章",
                "材料预览",
                "系统已自动为您生成",
                "重新生成",
                "涉税",
                "开户",
            ],
            timeout_sec=25,
            leave_markers=["本次是否办理五险一金"],
        )
    else:
        result["next2"] = True
        result["next2Skipped"] = "already-on-seal-or-preview"
    _shot(page, case_id, "opening-after-next2")
    if _on_material_preview_page(page):
        return _finish_opening_done(
            case_id, page, result, reason="after-social-to-material-preview"
        )

    seal_needles = [
        "自费刻章，设立时暂不刻章（设立后自行办理）",
        "自费刻章，设立时候暂不刻章",
        "自费刻章，设立时暂不刻章",
        "设立时候暂不刻章",
        "设立时暂不刻章",
        "自费刻章",
    ]
    if _on_seal_page(page):
        result["selfPayNoSeal"] = _ensure_radio(page, seal_needles)
        if not result["selfPayNoSeal"]:
            for n in seal_needles:
                if _radio_checked(page, n):
                    result["selfPayNoSeal"] = True
                    break
        page.wait_for_timeout(400)
        _shot(page, case_id, "opening-seal-selected")
        if not result["selfPayNoSeal"]:
            result["error"] = "未找到自费刻章/暂不刻章选项"
            result["missing"] = ["自费刻章暂不刻章"]
            result["currentUrl"] = getattr(page, "url", "") or ""
            _dump(case_id, "fill-opening-result.json", result)
            return result
        result["next3"] = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(1500)
        _wait_any(
            page,
            ["涉税", "开户", "暂存", "材料预览", "系统已自动为您生成", "下一步"],
            timeout_sec=15,
        )
        _shot(page, case_id, "opening-after-next3")
        if _on_material_preview_page(page):
            return _finish_opening_done(
                case_id, page, result, reason="after-seal-to-material-preview"
            )
        result["next4"] = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(1500)
        _wait_any(
            page,
            [
                "本次同步办理预约银行开户",
                "开户预约",
                "暂存",
                "材料预览",
                "系统已自动为您生成",
                "重新生成",
            ],
            timeout_sec=18,
        )
        _shot(page, case_id, "opening-after-next4")
    else:
        result["selfPayNoSeal"] = True
        result["selfPayNoSealSkipped"] = "page-past-seal"
        result["next3"] = True
        result["next4"] = True
        for i in range(3):
            if _on_material_preview_page(page):
                break
            if _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True):
                page.wait_for_timeout(1200)
                result[f"pastSealNext{i}"] = True
            else:
                break
        _shot(page, case_id, "opening-after-next4")

    if _on_material_preview_page(page):
        return _finish_opening_done(
            case_id, page, result, reason="landed-material-preview"
        )

    result["saved"] = _click_save_draft(page)
    page.wait_for_timeout(1200)
    if not result["saved"]:
        page.wait_for_timeout(1000)
        result["saved"] = _click_save_draft(page)
        page.wait_for_timeout(1200)
    if _page_has(page, "暂存成功") or _page_has(page, "数据暂存成功"):
        result["saved"] = True
        result["savedAck"] = True
    if not result["saved"] and _on_material_preview_page(page):
        result["saved"] = True
        result["savedVia"] = "material-preview-no-draft-btn"
    _shot(page, case_id, "opening-after-save")

    if _on_material_preview_page(page):
        return _finish_opening_done(
            case_id, page, result, reason="final-material-preview"
        )

    missing = []
    if not result.get("fullOnline"):
        missing.append("全程网办")
    if not result.get("noMaterialGuide") and not result.get("noMaterialGuideSkipped"):
        missing.append("不需要材料辅导")
    if not result.get("smartDoc"):
        missing.append("智慧文书")
    if not result.get("noPaperLicense") and not result.get("noPaperLicenseSkipped"):
        missing.append("无需领取纸质营业执照")
    if not result.get("next1") or not result.get("next2"):
        missing.append("办理方式下一步")
    if not result.get("selfPayNoSeal") and not result.get("selfPayNoSealSkipped"):
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
