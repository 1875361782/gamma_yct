"""企业开办 / 后续办理方式（新增，不改核名/基本信息/出资/人员代码）。

人员信息「已完成进入下一步」之后：
1. 若尚未进入开办页 → 点「下一步」进入企业开办
2. 后续办理方式：全程网办 → 不需要材料辅导
3. 文书产生方式：智慧文书
4. 纸质执照：无需领取纸质营业执照
5. 下一步 → 下一步
6. 自费刻章，设立时(候)暂不刻章
7. 下一步 → 下一步 →（开户预约）下一步 → 材料预览
8. 上传「住所使用相关文件」→ 提交 → 「我已阅读上述告知」
9. 等到政务网成功页（含「进度跟踪」）才算开办/自动填报结束（勿在告知弹窗处结束）
不代电子签名（成功页「进度跟踪」按钮留给签名流程）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
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
                  const isGuideChild = n.includes('不需要材料辅导') || n.includes('需要材料辅导')
                    || n.includes('无需登记机关对登记材料进行辅导');
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
                      // 「不需要材料辅导（…长说明）」可达百余字，勿按 80 截断丢弃
                      if (t.length > 220) continue;
                      // 父级「全程网办」innerText 会吞掉子选项，点辅导子项时必须排除父级
                      if (isGuideChild && (t.startsWith('全程网办') || t.startsWith('半程网办'))) continue;
                      const r = lab.getBoundingClientRect();
                      if (r.width < 8 || r.height < 8 || r.bottom < 0 || r.top > innerHeight) continue;
                      const disabled =
                        (lab.className || '').includes('is-disabled') ||
                        lab.getAttribute('aria-disabled') === 'true' ||
                        !!(lab.querySelector && lab.querySelector('input[disabled]'));
                      const starts = t.startsWith(n) ? 0 : 1;
                      cands.push({lab, len: t.length, y: r.y, disabled, starts, t: t.slice(0, 40)});
                    }
                  }
                  cands.sort((a, b) => a.starts - b.starts || a.len - b.len || a.y - b.y);
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
        is_guide = any(
            x in needle
            for x in ("不需要材料辅导", "需要材料辅导", "无需登记机关对登记材料进行辅导")
        )
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
                        compact = txt.replace(" ", "")
                        # 子选项含括号长说明，放宽到 220；优先以 needle 开头的最短文案
                        if needle not in txt or len(txt) > 220:
                            continue
                        if is_guide and (
                            compact.startswith("全程网办") or compact.startswith("半程网办")
                        ):
                            continue
                        score = len(txt) + (
                            0 if compact.startswith(needle.replace(" ", "")) else 50
                        )
                        if score < best_len:
                            best = lab
                            best_len = score
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
                        t_anc = " ".join((target.inner_text(timeout=250) or "").split())
                        if is_guide and (
                            t_anc.replace(" ", "").startswith("全程网办")
                            or t_anc.replace(" ", "").startswith("半程网办")
                        ):
                            raise RuntimeError("skip-parent-guide")
                    except RuntimeError:
                        raise
                    except Exception:
                        pass
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
            except RuntimeError:
                pass
            except Exception:
                pass

            # C) JS 深度点击
            js = _deep_click_radio_js(scope, needle)
            if js.get("ok"):
                page.wait_for_timeout(350)
                if _radio_checked(page, needle):
                    return True
                # 不再信任 js.checked（可能点到父级误标）
    return False


def _radio_checked(page: Page, needle: str) -> bool:
    """是否已勾选：优先「文案以 needle 开头」的最短项，避免父级全程网办吞子文案误判。"""
    for scope in _all_scopes(page):
        try:
            js = scope.evaluate(
                """(needle) => {
                  const norm = (s) => (s || '').replace(/\\s+/g, '');
                  const n = norm(needle);
                  const isGuideChild = n.includes('不需要材料辅导') || n.includes('需要材料辅导')
                    || n.includes('无需登记机关对登记材料进行辅导');
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
                      const t = norm(lab.innerText || '');
                      if (!t.includes(n) || t.length > 220) continue;
                      if (isGuideChild && (t.startsWith('全程网办') || t.startsWith('半程网办'))) continue;
                      const checked =
                        (lab.className || '').includes('is-checked') ||
                        !!(lab.querySelector && lab.querySelector('input[type=radio]') &&
                           lab.querySelector('input[type=radio]').checked) ||
                        (lab.getAttribute && lab.getAttribute('aria-checked') === 'true');
                      cands.push({
                        starts: t.startsWith(n),
                        len: t.length,
                        checked,
                      });
                    }
                  }
                  if (!cands.length) return false;
                  cands.sort((a, b) => (b.starts - a.starts) || (a.len - b.len));
                  const pool = cands.some(c => c.starts) ? cands.filter(c => c.starts) : cands;
                  return !!pool[0].checked;
                }""",
                needle,
            )
            if js:
                return True
        except Exception:
            pass
        try:
            labs = scope.locator("label.el-radio, .el-radio, label").filter(
                has_text=re.compile(re.escape(needle))
            )
            n = min(labs.count(), 12)
            best = None
            best_score = 10**9
            needle_c = needle.replace(" ", "")
            for i in range(n):
                lab = labs.nth(i)
                if not _visible(lab, timeout=200):
                    continue
                txt = " ".join((lab.inner_text(timeout=250) or "").split())
                compact = txt.replace(" ", "")
                if needle not in txt or len(txt) > 220:
                    continue
                if (
                    ("不需要材料辅导" in needle or "需要材料辅导" in needle)
                    and (compact.startswith("全程网办") or compact.startswith("半程网办"))
                ):
                    continue
                score = len(txt) + (0 if compact.startswith(needle_c) else 80)
                if score < best_score:
                    best = lab
                    best_score = score
            if best is None:
                continue
            cls = best.get_attribute("class") or ""
            if "is-checked" in cls:
                return True
            if (best.get_attribute("aria-checked") or "") == "true":
                return True
            inp = best.locator("input[type='radio']").first
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
                        if (!t.includes(n) || t.length > 220) continue;
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


def _click_footer_submit(page: Page) -> bool:
    """点材料预览页脚蓝钮「提交」（避开提示文案里的「提交」、行内按钮）。"""
    pat = re.compile(r"^\s*提交\s*$")
    for scope in _all_scopes(page):
        try:
            # 优先：与「上一步」同排的页脚按钮区
            for sel in (
                ".footer button, .footer .el-button",
                ".el-footer button, .el-footer .el-button",
                ".page-footer button, .page-footer .el-button",
                ".yd-footer button, .yd-footer .el-button",
                ".btn-box button, .btn-box .el-button",
                ".bottom-btn button, .bottom-btn .el-button",
                ".operate-btn button, .operate-btn .el-button",
                "button.el-button--primary",
                ".el-button--primary",
                "button",
                "[role='button']",
            ):
                btns = scope.locator(sel).filter(has_text=pat)
                n = min(btns.count(), 10)
                # 自下而上：页脚「提交」通常是最后一个
                for i in range(n - 1, -1, -1):
                    btn = btns.nth(i)
                    if not _visible(btn, timeout=400):
                        continue
                    try:
                        # 禁用态（上传后短暂 loading）不点
                        if btn.is_disabled():
                            continue
                    except Exception:
                        pass
                    try:
                        cls = (btn.get_attribute("class") or "") + " " + (
                            btn.get_attribute("disabled") or ""
                        )
                        if "is-disabled" in cls or "disabled" == (
                            btn.get_attribute("disabled") or ""
                        ):
                            continue
                    except Exception:
                        pass
                    try:
                        box = btn.bounding_box()
                        # 页脚主按钮通常较宽；过滤掉窄文字链
                        if box and box.get("width", 0) < 48:
                            continue
                    except Exception:
                        pass
                    try:
                        btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    page.wait_for_timeout(200)
                    try:
                        btn.click(timeout=4000)
                        return True
                    except Exception:
                        try:
                            btn.click(timeout=4000, force=True)
                            return True
                        except Exception:
                            continue
        except Exception:
            continue
    # JS 兜底：找文案恰好为「提交」的可见 button/el-button，优先带 primary 的
    for scope in _all_scopes(page):
        try:
            hit = scope.evaluate(
                """() => {
                  const nodes = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], .el-button'
                  ));
                  const cands = nodes.filter((el) => {
                    const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
                    if (t !== '提交') return false;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    if (el.disabled || (el.className || '').includes('is-disabled')) return false;
                    const r = el.getBoundingClientRect();
                    return r.width >= 48 && r.height >= 28;
                  });
                  if (!cands.length) return false;
                  cands.sort((a, b) => {
                    const ap = (a.className || '').includes('primary') ? 1 : 0;
                    const bp = (b.className || '').includes('primary') ? 1 : 0;
                    if (ap !== bp) return bp - ap;
                    return b.getBoundingClientRect().top - a.getBoundingClientRect().top;
                  });
                  cands[0].scrollIntoView({block: 'center', inline: 'nearest'});
                  cands[0].click();
                  return true;
                }"""
            )
            if hit:
                return True
        except Exception:
            continue
    return False


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


def _resolve_address_proof_pdf(
    case_id: str, payload: dict[str, Any] | None
) -> Path | None:
    """从前端 attachments.addressProof 取 PDF；否则扫 uploads 目录。"""
    ids = ((payload or {}).get("attachments") or {}).get("addressProof") or []
    for raw in ids:
        fid = str(raw or "").strip()
        if not fid:
            continue
        try:
            path = store.upload_path(case_id, fid)
            if path.exists():
                return path
        except Exception:
            continue
    uploads = store.case_dir(case_id) / "uploads"
    if not uploads.exists():
        return None
    pdfs = sorted(
        p for p in uploads.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
    )
    return pdfs[0] if pdfs else None


def _upload_address_use_file(
    page: Page, case_id: str, pdf_path: Path | None
) -> dict[str, Any]:
    """材料预览页：点「住所使用相关文件」行的上传，选择前端 PDF。"""
    out: dict[str, Any] = {"ok": False}
    if pdf_path is None or not pdf_path.exists():
        out["error"] = "missing-address-proof-pdf"
        return out
    out["path"] = str(pdf_path)
    try:
        row = page.locator("*").filter(has_text=re.compile(r"住所使用相关文件")).first
        if not _visible(row, timeout=2000):
            for scope in _all_scopes(page):
                row = scope.locator("*").filter(
                    has_text=re.compile(r"住所使用相关文件")
                ).first
                if _visible(row, timeout=800):
                    break
        if not _visible(row, timeout=800):
            out["error"] = "row-not-found"
            return out
        try:
            row.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        # 优先点该行附近的「上传 / 重新上传」
        upload_btn = row.locator("button, a, span").filter(
            has_text=re.compile(r"上传")
        ).first
        if not _visible(upload_btn, timeout=600):
            # 祖先行容器
            parent = row.locator(
                "xpath=ancestor::*[self::tr or self::li or contains(@class,'item') or contains(@class,'row')][1]"
            )
            if _visible(parent, timeout=400):
                upload_btn = parent.locator("button, a, span").filter(
                    has_text=re.compile(r"上传")
                ).first
                row = parent
        clicked = False
        # 优先 filechooser（政务网常见自定义上传按钮）
        if _visible(upload_btn, timeout=600):
            try:
                with page.expect_file_chooser(timeout=5000) as fc_info:
                    upload_btn.click(timeout=2500, force=True)
                chooser = fc_info.value
                chooser.set_files(str(pdf_path.resolve()))
                clicked = True
                out["ok"] = True
                out["via"] = "file-chooser"
            except Exception as e:
                out["chooserError"] = str(e)
                try:
                    upload_btn.click(timeout=2500, force=True)
                    clicked = True
                except Exception as e2:
                    out["clickError"] = str(e2)
        out["clickedUpload"] = clicked
        if out.get("ok"):
            page.wait_for_timeout(1500)
            _shot(page, case_id, "opening-address-proof-uploaded")
            return out

        page.wait_for_timeout(600)
        file_input = None
        for scope in [row, page, *_all_scopes(page)]:
            try:
                cand = scope.locator("input[type='file']").last
                if cand.count() > 0:
                    file_input = cand
                    break
            except Exception:
                continue
        if file_input is None:
            out["error"] = out.get("error") or "file-input-missing"
            return out
        file_input.set_input_files(str(pdf_path.resolve()))
        out["ok"] = True
        out["via"] = "set-input-files"
        page.wait_for_timeout(1500)
        _shot(page, case_id, "opening-address-proof-uploaded")
    except Exception as e:
        out["error"] = str(e)
    return out


def _ack_dialog_visible(page: Page) -> bool:
    markers = (
        "我已阅读上述告知",
        "环境可持续",
        "Environmental Sustainability",
    )
    return any(_page_has(page, m) for m in markers)


def _on_submit_success_page(page: Page) -> bool:
    """材料提交后的成功页（#/success），含中央「进度跟踪」入口。

    以此页为准结束自动填报；告知弹窗出现不算结束。
    """
    try:
        url = (page.url or "").lower()
        if "#/success" in url or "/success" in url:
            return True
    except Exception:
        pass
    # 成功页正文：「请根据短信提示…进度跟踪…查询反馈结果」
    if _page_has(page, "请根据短信提示") and (
        _page_has(page, "进度跟踪") or _page_has(page, "查询反馈结果")
    ):
        return True
    if _page_has(page, "查询反馈结果") and _page_has(page, "进度跟踪"):
        return True
    return False


def _wait_submit_success_page(
    page: Page, case_id: str, *, timeout_sec: float = 60
) -> tuple[Page, bool]:
    """告知确认后等到成功页；可能新开标签，需扫 context.pages。"""
    import time

    end = time.time() + timeout_sec
    while time.time() < end:
        if case_id and store.is_cancelled(case_id):
            return page, False
        candidates: list[Page] = [page]
        try:
            for p in page.context.pages:
                if p not in candidates and not p.is_closed():
                    candidates.append(p)
        except Exception:
            pass
        for p in reversed(candidates):
            try:
                if _on_submit_success_page(p):
                    return p, True
            except Exception:
                continue
        # 任意 scope 命中成功文案也算
        if _on_submit_success_page(page):
            return page, True
        page.wait_for_timeout(500)
    return page, _on_submit_success_page(page)


def _submit_material_and_ack(page: Page, case_id: str) -> dict[str, Any]:
    """材料预览点提交 → 告知弹窗 → 等到成功页（进度跟踪）。"""
    out: dict[str, Any] = {"ok": False}
    try:
        # 上传后页脚「提交」可能短暂禁用，先等到可点
        for _ in range(12):
            if not _on_material_preview_page(page):
                break
            ready = False
            for scope in _all_scopes(page):
                try:
                    btn = scope.locator(
                        "button, .el-button, [role='button']"
                    ).filter(has_text=re.compile(r"^\s*提交\s*$")).last
                    if not _visible(btn, timeout=300):
                        continue
                    try:
                        if btn.is_disabled():
                            continue
                    except Exception:
                        pass
                    cls = btn.get_attribute("class") or ""
                    if "is-disabled" in cls:
                        continue
                    ready = True
                    break
                except Exception:
                    continue
            if ready:
                break
            page.wait_for_timeout(500)

        submitted = False
        for attempt in range(3):
            if _click_footer_submit(page):
                submitted = True
            else:
                # 勿用 get_by_text('提交')：提示条里有「点击提交按钮」
                for scope in _all_scopes(page):
                    try:
                        btn = scope.get_by_role(
                            "button", name=re.compile(r"^\s*提交\s*$")
                        ).last
                        if _visible(btn, timeout=500):
                            try:
                                btn.scroll_into_view_if_needed(timeout=1500)
                            except Exception:
                                pass
                            btn.click(timeout=4000)
                            submitted = True
                            break
                    except Exception:
                        continue
            page.wait_for_timeout(1000)
            if _wait_any(
                page,
                ["我已阅读上述告知", "环境可持续", "Environmental Sustainability", "告知"],
                timeout_sec=8,
            ) or _ack_dialog_visible(page):
                break
            # 仍停在材料预览 → 点击未生效，重试
            if _on_material_preview_page(page) and attempt < 2:
                out[f"submitRetry{attempt}"] = "still-on-preview"
                page.wait_for_timeout(800)
                continue
            break

        out["submitted"] = submitted
        _shot(page, case_id, "opening-submit-notice")

        # 弹窗内滚到底并点「我已阅读上述告知」
        ack = False
        for scope in _all_scopes(page):
            try:
                dlg = scope.locator(
                    ".el-dialog:visible, .el-message-box:visible, .el-overlay-dialog:visible, "
                    "[role='dialog']:visible, .layui-layer:visible"
                ).last
                if not _visible(dlg, timeout=500):
                    continue
                try:
                    dlg.evaluate(
                        """(el) => {
                          const box = el.querySelector('.el-dialog__body, .el-message-box__content, .layui-layer-content') || el;
                          box.scrollTop = box.scrollHeight;
                        }"""
                    )
                except Exception:
                    pass
                page.wait_for_timeout(400)
                btn = dlg.locator("button, a, span, .el-button").filter(
                    has_text=re.compile(r"我已阅读上述告知")
                ).first
                if _visible(btn, timeout=800):
                    btn.click(timeout=3000, force=True)
                    ack = True
                    break
            except Exception:
                continue
        if not ack:
            ack = _click_text_any(page, ["我已阅读上述告知"], exact=False)
        out["acked"] = ack
        # 点击后既无告知弹窗、又仍停在材料预览 → 提交未生效
        if (
            submitted
            and not ack
            and _on_material_preview_page(page)
            and not _ack_dialog_visible(page)
        ):
            out["submitted"] = False
            out["error"] = out.get("error") or "submit-click-no-effect"
        page.wait_for_timeout(1000)
        _shot(page, case_id, "opening-after-ack")

        # 告知确认后必须等到成功页（进度跟踪），才算提交完成
        reached = False
        if submitted and ack:
            store.set_status(
                case_id,
                statuses.CHOOSING_PROCESS,
                customer_message="材料已提交，等待进入进度跟踪页…",
            )
            page, reached = _wait_submit_success_page(page, case_id, timeout_sec=75)
            out["page"] = page  # 供调用方切换到新标签
        out["reachedSuccess"] = reached
        out["ok"] = bool(submitted and ack and reached)
        if submitted and ack and not reached:
            out["error"] = out.get("error") or "ack-ok-but-no-success-page"
        _shot(page, case_id, "opening-submit-success")
    except Exception as e:
        out["error"] = str(e)
    return out


def _complete_material_preview(
    case_id: str,
    page: Page,
    result: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    """材料预览：上传住所文件 → 提交 → 告知 → 等到进度跟踪成功页。"""
    store.set_status(
        case_id,
        statuses.CHOOSING_PROCESS,
        customer_message="材料预览：上传住所文件并提交…",
    )
    pdf = _resolve_address_proof_pdf(case_id, payload)
    result["addressProofPath"] = str(pdf) if pdf else None
    result["addressUpload"] = _upload_address_use_file(page, case_id, pdf)
    submit_out = _submit_material_and_ack(page, case_id)
    # 成功页可能在新标签：切过去供截图 / 后续签名定位
    new_page = submit_out.get("page")
    if isinstance(new_page, Page):
        page = new_page
        try:
            page.bring_to_front()
        except Exception:
            pass
    result["materialSubmit"] = {
        k: v for k, v in submit_out.items() if k != "page"
    }
    if result.get("addressUpload", {}).get("ok") and result.get("materialSubmit", {}).get(
        "ok"
    ):
        result["saved"] = True
        result["savedVia"] = "material-submit-success-page"
        return _finish_opening_done(
            case_id, page, result, reason=reason or "submit-success-page"
        )
    # 未到成功页：不开办完成，记下缺口
    result["saved"] = False
    result["ok"] = False
    result["materialPreviewGaps"] = {
        "upload": not bool(result.get("addressUpload", {}).get("ok")),
        "submitAck": not bool(result.get("materialSubmit", {}).get("acked")),
        "successPage": not bool(result.get("materialSubmit", {}).get("reachedSuccess")),
    }
    result["error"] = (
        result.get("materialSubmit", {}).get("error")
        or "材料已操作但未进入进度跟踪成功页"
    )
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

    payload = payload if isinstance(payload, dict) else {}
    store.set_status(
        case_id,
        statuses.CHOOSING_PROCESS,
        customer_message="正在填写企业开办 / 后续办理方式…",
    )
    if dismiss_system_busy(page):
        page.wait_for_timeout(1200)
    result: dict[str, Any] = {"ok": False}

    # 已到材料预览：上传住所文件 → 提交 → 告知
    if _on_material_preview_page(page):
        return _complete_material_preview(
            case_id, page, result, payload, reason="already-on-material-preview"
        )

    page = _enter_opening(page, case_id, result)
    if _on_material_preview_page(page):
        return _complete_material_preview(
            case_id, page, result, payload, reason="enter-landed-material-preview"
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
        page.wait_for_timeout(900)
        guide_needles = ["不需要材料辅导", "无需登记机关对登记材料进行辅导"]
        if _option_present(page, "不需要材料辅导") or _option_present(
            page, "无需登记机关对登记材料进行辅导"
        ):
            # 子选项可能在点「全程网办」后延迟渲染
            page.wait_for_timeout(800)
            result["noMaterialGuide"] = _ensure_radio(page, guide_needles, retries=10)
            if not result["noMaterialGuide"]:
                # 再点一次全程网办展开子项后重试
                _ensure_radio(page, ["全程网办"])
                page.wait_for_timeout(900)
                result["noMaterialGuideRetry"] = _ensure_radio(
                    page, guide_needles, retries=8
                )
                result["noMaterialGuide"] = bool(
                    result["noMaterialGuide"] or result["noMaterialGuideRetry"]
                )
        else:
            result["noMaterialGuide"] = True
            result["noMaterialGuideSkipped"] = "option-absent"
        page.wait_for_timeout(400)
        result["smartDocEnabled"] = _wait_radio_enabled(page, "智慧文书", timeout_sec=10)
        if not result["smartDocEnabled"] and result["fullOnline"]:
            _ensure_radio(page, ["全程网办"])
            page.wait_for_timeout(500)
            if not result.get("noMaterialGuideSkipped"):
                result["noMaterialGuide"] = bool(
                    result.get("noMaterialGuide")
                    or _ensure_radio(page, guide_needles, retries=4)
                )
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
            if not result.get("noMaterialGuideSkipped"):
                result["noMaterialGuide"] = bool(
                    result.get("noMaterialGuide")
                    or _ensure_radio(page, guide_needles, retries=4)
                )
            result["smartDocRetry"] = _ensure_radio(page, ["智慧文书"])
            result["fullOnline"] = bool(result["fullOnline"] or result["fullOnlineRetry"])
            result["smartDoc"] = bool(result["smartDoc"] or result["smartDocRetry"])
        # 点下一步前再确认辅导子项（父级误判曾导致此处跳过）
        if not result.get("noMaterialGuideSkipped") and not _radio_checked(
            page, "不需要材料辅导"
        ):
            result["noMaterialGuideFinal"] = _ensure_radio(page, guide_needles, retries=6)
            result["noMaterialGuide"] = bool(
                result.get("noMaterialGuide") or result["noMaterialGuideFinal"]
            )
        result["radioProbeAfter"] = _probe_radios(page)
        _shot(page, case_id, "opening-process-selected")

        if not result["fullOnline"] or not result["smartDoc"] or (
            not result.get("noMaterialGuide") and not result.get("noMaterialGuideSkipped")
        ):
            result["error"] = "办理方式单选未生效"
            result["missing"] = [
                k
                for k, ok in (
                    ("全程网办", result["fullOnline"]),
                    ("不需要材料辅导", result.get("noMaterialGuide") or result.get("noMaterialGuideSkipped")),
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
            page.wait_for_timeout(500)
            if not result.get("noMaterialGuideSkipped"):
                _ensure_radio(page, guide_needles, retries=6)
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
            return _complete_material_preview(
                case_id, page, result, payload, reason="after-process-to-material-preview"
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
        return _complete_material_preview(
            case_id, page, result, payload, reason="before-seal-material-preview"
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
        return _complete_material_preview(
            case_id, page, result, payload, reason="after-social-to-material-preview"
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
            return _complete_material_preview(
                case_id, page, result, payload, reason="after-seal-to-material-preview"
            )
        result["next4"] = _click_footer_next(page) or _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(1500)
        _wait_any(
            page,
            [
                "本次同步办理预约银行开户",
                "开户预约",
                "下一步",
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
        return _complete_material_preview(
            case_id, page, result, payload, reason="landed-material-preview"
        )

    # 开户预约页：点「下一步」进入材料预览（不再点暂存）
    result["nextBank"] = _click_footer_next(page) or _click_text_any(
        page, ["下一步"], exact=True
    )
    page.wait_for_timeout(1500)
    _wait_any(
        page,
        ["材料预览", "系统已自动为您生成", "住所使用相关文件", "重新生成", "提交"],
        timeout_sec=20,
    )
    if not _on_material_preview_page(page):
        page.wait_for_timeout(1000)
        result["nextBankRetry"] = _click_footer_next(page) or _click_text_any(
            page, ["下一步"], exact=True
        )
        page.wait_for_timeout(1500)
        _wait_any(
            page,
            ["材料预览", "系统已自动为您生成", "住所使用相关文件", "重新生成", "提交"],
            timeout_sec=15,
        )
    _shot(page, case_id, "opening-after-bank-next")

    if _on_material_preview_page(page):
        return _complete_material_preview(
            case_id, page, result, payload, reason="after-bank-next-material-preview"
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
    if not result.get("nextBank") and not result.get("nextBankRetry"):
        missing.append("开户预约下一步")
    if not _on_material_preview_page(page):
        missing.append("材料预览")
    result["missing"] = missing
    result["ok"] = not missing
    result["currentUrl"] = getattr(page, "url", "") or ""
    _dump(case_id, "fill-opening-result.json", result)
    return result
