"""电子签名流程（材料提交完成之后）。

图1 进度跟踪 → 图2 查看签名情况 → 图3 我要电子签名 → 图4 我是自然人
→ 图5 下一步(证件类型) → 图6 认证方式二支付宝 → 图7 支付宝扫码(推前端第9页)
→ 图8 同意协议+登录并授权 → 图9 知道了 → 图11 确认 → 图12 勾选+批量签署
→ 图13 人脸扫码(推前端第10页) → 图14 签名完成 → 关窗。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from playwright.sync_api import Frame, Page

from apps.api import statuses, store

OnQr = Callable[[str, str], None]  # (data_uri, kind) kind=alipay|face
OnPhase = Callable[[str], None]


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
        return bool(loc.count() and loc.is_visible(timeout=timeout))
    except Exception:
        return False


def _all_scopes(page: Page) -> list[Page | Frame]:
    scopes: list[Page | Frame] = [page]
    try:
        for fr in page.frames:
            if fr not in scopes:
                scopes.append(fr)
    except Exception:
        pass
    try:
        for p in page.context.pages:
            if p not in scopes:
                scopes.append(p)
                for fr in p.frames:
                    if fr not in scopes:
                        scopes.append(fr)
    except Exception:
        pass
    return scopes


def _page_has(page: Page, marker: str) -> bool:
    for scope in _all_scopes(page):
        try:
            if scope.get_by_text(marker, exact=False).count() > 0:
                loc = scope.get_by_text(marker, exact=False).first
                if _visible(loc, timeout=200):
                    return True
        except Exception:
            continue
    return False


def _click_text(page: Page, texts: list[str], *, exact: bool = False) -> bool:
    for scope in _all_scopes(page):
        for t in texts:
            # 优先点 button / link，避免点到正文里同名文字（如成功页说明中的「进度跟踪」）
            if _click_action_control(scope, t):
                return True
            try:
                loc = scope.get_by_text(t, exact=exact).first
                if not _visible(loc, timeout=400):
                    continue
                try:
                    loc.click(timeout=3000, force=True)
                except Exception:
                    loc.click(timeout=3000)
                return True
            except Exception:
                continue
    return False


def _click_action_control(scope: Page | Frame, label: str) -> bool:
    """点击可交互控件上的文案（button/a/role），避开段落内同名文字。"""
    pat = re.compile(rf"^\s*{re.escape(label)}\s*$")
    candidates = []
    try:
        candidates.append(scope.get_by_role("button", name=pat).first)
    except Exception:
        pass
    try:
        candidates.append(scope.get_by_role("link", name=pat).first)
    except Exception:
        pass
    for sel in (
        "button",
        "a",
        "[role='button']",
        ".el-button",
        ".ant-btn",
        ".btn",
        "div.el-button",
        "span.el-button",
    ):
        try:
            candidates.append(scope.locator(sel).filter(has_text=pat).first)
        except Exception:
            continue
    for loc in candidates:
        try:
            if not _visible(loc, timeout=500):
                continue
            # 排除过小的横幅文字链（成功页主按钮通常较宽）
            try:
                box = loc.bounding_box()
                if box and box.get("width", 0) < 40:
                    continue
            except Exception:
                pass
            try:
                loc.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                loc.click(timeout=3000, force=True)
            except Exception:
                loc.click(timeout=3000)
            return True
        except Exception:
            continue
    return False


def _click_progress_tracking(page: Page, browser) -> tuple[bool, Page]:
    """专门点成功页中央「进度跟踪」主按钮，并确认是否进入申请列表。"""
    page = _active_page(browser) or page
    # 成功页正文含「进度跟踪」四字，必须优先点 button，不能 get_by_text.first
    for scope in _all_scopes(page):
        if _click_action_control(scope, "进度跟踪"):
            page.wait_for_timeout(1200)
            page = _active_page(browser) or page
            if _page_has(page, "我的申请") or _page_has(page, "查看签名情况") or _page_has(
                page, "待电子签名"
            ):
                return True, page
            # 点击可能开了新页
            page.wait_for_timeout(1500)
            page = _active_page(browser) or page
            if _page_has(page, "我的申请") or _page_has(page, "查看签名情况"):
                return True, page
            # 点到了但尚未加载完，仍算推进
            return True, page
    # 横幅「进度查询」作为次选
    for scope in _all_scopes(page):
        if _click_action_control(scope, "进度查询"):
            page.wait_for_timeout(1500)
            page = _active_page(browser) or page
            return True, page
    return False, page


def _on_natural_person_select_page(page: Page) -> bool:
    """电子签名身份选择页：我是自然人 / 我是企业。"""
    return _page_has(page, "我是自然人") and _page_has(page, "我是企业")


def _on_id_type_page(page: Page) -> bool:
    return (
        _page_has(page, "居民身份证")
        or _page_has(page, "所持证件")
        or (_page_has(page, "下一步") and _page_has(page, "证件"))
    )


def _click_natural_person_card(scope: Page | Frame) -> bool:
    """点身份选择大卡片「我是自然人」，避开说明文字里的同名词。"""
    # 卡片文案含英文副标题，不能用 ^我是自然人$ 精确匹配 button name
    selectors = (
        "a",
        "button",
        "[role='button']",
        "[role='radio']",
        "label",
        "li",
        "div",
    )
    for sel in selectors:
        try:
            cards = scope.locator(sel).filter(
                has_text=re.compile(r"我是自然人")
            ).filter(has_text=re.compile(r"As a natural person|自然人"))
            n = min(cards.count(), 12)
            best = None
            best_area = 0.0
            for i in range(n):
                loc = cards.nth(i)
                if not _visible(loc, timeout=300):
                    continue
                try:
                    txt = " ".join((loc.inner_text(timeout=400) or "").split())
                except Exception:
                    txt = ""
                # 排除提示框整段（很长、含「点击下方」等）
                if "点击下方" in txt or "随申办" in txt or "电脑端" in txt:
                    continue
                if "我是自然人" not in txt:
                    continue
                # 只要自然人卡片，不要整页容器
                if "我是企业" in txt and "As an enterprise" in txt:
                    continue
                try:
                    box = loc.bounding_box()
                except Exception:
                    box = None
                if not box:
                    continue
                w, h = float(box.get("width") or 0), float(box.get("height") or 0)
                # 身份卡片通常较宽较高；过滤小文字节点
                if w < 120 or h < 36:
                    continue
                area = w * h
                # 过大容器（整页）跳过
                if w > 900 and h > 400:
                    continue
                if area > best_area:
                    best_area = area
                    best = loc
            if best is None:
                continue
            try:
                best.scroll_into_view_if_needed(timeout=1500)
            except Exception:
                pass
            try:
                best.click(timeout=4000)
            except Exception:
                best.click(timeout=4000, force=True)
            return True
        except Exception:
            continue
    # JS 兜底：找含「我是自然人」且面积适中的可点节点
    try:
        return bool(
            scope.evaluate(
                """() => {
                  const bad = (t) => t.includes('点击下方') || t.includes('随申办') || t.includes('电脑端');
                  const nodes = Array.from(document.querySelectorAll(
                    'a, button, label, li, div, [role=button], [role=radio]'
                  ));
                  const cands = [];
                  for (const el of nodes) {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!t.includes('我是自然人')) continue;
                    if (bad(t)) continue;
                    if (t.includes('我是企业') && t.includes('As an enterprise')) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 120 || r.height < 36) continue;
                    if (r.width > 900 && r.height > 400) continue;
                    const st = window.getComputedStyle(el);
                    if (st.display === 'none' || st.visibility === 'hidden') continue;
                    cands.push({ el, area: r.width * r.height, top: r.top });
                  }
                  if (!cands.length) return false;
                  cands.sort((a, b) => a.area - b.area); // 取最小合适卡片，避免点到外层
                  const hit = cands[0].el;
                  hit.scrollIntoView({ block: 'center', inline: 'nearest' });
                  const radio = hit.querySelector('input[type=radio]');
                  if (radio) { radio.click(); radio.checked = true; }
                  hit.click();
                  return true;
                }"""
            )
        )
    except Exception:
        return False


def _click_natural_person(page: Page, browser) -> tuple[bool, Page]:
    """点「我是自然人」并确认进入证件类型页。"""
    page = _active_page(browser) or page
    for attempt in range(3):
        clicked = False
        for scope in _all_scopes(page):
            if _click_natural_person_card(scope):
                clicked = True
                break
        if not clicked:
            # 次选：role=radio / get_by_text，但仍避开提示条
            for scope in _all_scopes(page):
                try:
                    loc = scope.get_by_role(
                        "radio", name=re.compile(r"我是自然人")
                    ).first
                    if _visible(loc, timeout=400):
                        loc.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    pass
        page.wait_for_timeout(1200)
        page = _active_page(browser) or page
        if _on_id_type_page(page) or not _on_natural_person_select_page(page):
            return True, page
        if attempt < 2:
            page.wait_for_timeout(800)
    return (not _on_natural_person_select_page(page)) or _on_id_type_page(page), page


def _on_batch_sign_page(page: Page) -> bool:
    """待签材料列表页（含批量签署按钮）。"""
    return _page_has(page, "批量签署") or (
        _page_has(page, "查看") and _page_has(page, "企业名称")
    )


def _on_face_qr_page(page: Page) -> bool:
    return (
        _page_has(page, "扫码人脸认证")
        or _page_has(page, "人脸认证")
        or _page_has(page, "请使用支付宝/微信扫码")
        or _page_has(page, "请使用支付宝")
    )


def _click_batch_sign_button(scope: Page | Frame) -> bool:
    """点蓝钮「批量签署 / Batch Signature」（文案含英文，不能精确匹配）。"""
    pat = re.compile(r"批量签署")
    selectors = (
        "button.el-button--primary",
        ".el-button--primary",
        "button",
        "a",
        "[role='button']",
        ".el-button",
        "div.el-button",
    )
    for sel in selectors:
        try:
            btns = scope.locator(sel).filter(has_text=pat)
            n = min(btns.count(), 10)
            # 自下而上：页底主按钮通常靠后
            for i in range(n - 1, -1, -1):
                btn = btns.nth(i)
                if not _visible(btn, timeout=400):
                    continue
                try:
                    txt = " ".join((btn.inner_text(timeout=400) or "").split())
                except Exception:
                    txt = ""
                if "批量签署" not in txt:
                    continue
                # 排除过长正文容器
                if len(txt) > 40:
                    continue
                try:
                    box = btn.bounding_box()
                    if box and float(box.get("width") or 0) < 80:
                        continue
                except Exception:
                    pass
                try:
                    if btn.is_disabled():
                        continue
                except Exception:
                    pass
                try:
                    cls = btn.get_attribute("class") or ""
                    if "is-disabled" in cls:
                        continue
                except Exception:
                    pass
                try:
                    btn.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    btn.click(timeout=4000)
                except Exception:
                    try:
                        btn.click(timeout=4000, force=True)
                    except Exception:
                        continue
                return True
        except Exception:
            continue
    # JS 兜底
    try:
        return bool(
            scope.evaluate(
                """() => {
                  const nodes = Array.from(document.querySelectorAll(
                    'button, a, [role=button], .el-button'
                  ));
                  const cands = nodes.filter((el) => {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (!t.includes('批量签署')) return false;
                    if (t.length > 40) return false;
                    if (el.disabled || (el.className || '').includes('is-disabled')) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 80 || r.height < 28) return false;
                    const st = window.getComputedStyle(el);
                    return st.display !== 'none' && st.visibility !== 'hidden';
                  });
                  if (!cands.length) return false;
                  cands.sort((a, b) => {
                    const ap = (a.className || '').includes('primary') ? 1 : 0;
                    const bp = (b.className || '').includes('primary') ? 1 : 0;
                    if (ap !== bp) return bp - ap;
                    return b.getBoundingClientRect().top - a.getBoundingClientRect().top;
                  });
                  const hit = cands[0];
                  hit.scrollIntoView({ block: 'center', inline: 'nearest' });
                  hit.click();
                  return true;
                }"""
            )
        )
    except Exception:
        return False


def _click_batch_sign(page: Page, browser) -> tuple[bool, Page]:
    """勾选材料后点「批量签署」，并确认进入人脸认证。"""
    page = _active_page(browser) or page
    # 确保列表项已勾选
    try:
        for scope in _all_scopes(page):
            boxes = scope.locator("input[type=checkbox]")
            n = min(boxes.count(), 30)
            for i in range(n):
                try:
                    b = boxes.nth(i)
                    if _visible(b, timeout=200) and not b.is_checked():
                        b.check(force=True)
                except Exception:
                    continue
            if n:
                break
    except Exception:
        pass
    page.wait_for_timeout(400)

    for attempt in range(3):
        clicked = False
        for scope in _all_scopes(page):
            if _click_batch_sign_button(scope):
                clicked = True
                break
        page.wait_for_timeout(1500)
        page = _active_page(browser) or page
        if _on_face_qr_page(page) or not _on_batch_sign_page(page):
            return True, page
        if _wait_any(
            page,
            ["扫码人脸认证", "人脸认证", "请使用支付宝/微信扫码", "签名完成"],
            case_id="",
            timeout_sec=6,
        ):
            page = _active_page(browser) or page
            return True, page
        if clicked and attempt < 2:
            page.wait_for_timeout(800)
            continue
        if not clicked and attempt < 2:
            page.wait_for_timeout(600)
            continue
        break
    page = _active_page(browser) or page
    return _on_face_qr_page(page) or not _on_batch_sign_page(page), page


def _wait_any(
    page: Page, markers: list[str], *, case_id: str = "", timeout_sec: float = 20
) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if case_id and store.is_cancelled(case_id):
            return False
        if any(_page_has(page, m) for m in markers):
            return True
        page.wait_for_timeout(400)
    return any(_page_has(page, m) for m in markers)


def _capture_qr_uri(page: Page, case_id: str, tag: str) -> str:
    from apps.worker.playbooks.qr_login import (
        capture_alipay_panel_data_uri,
        capture_qr_data_uri,
    )

    # 支付宝登录码：优先截「扫码登录」面板，避免右侧一网通办图标
    if tag == "alipay":
        uri = capture_alipay_panel_data_uri(page, case_id=case_id)
        if uri:
            _shot(page, case_id, f"sign-qr-{tag}")
            return uri

    uri = capture_qr_data_uri(page, case_id=case_id, allow_fullpage=False)
    if uri:
        _shot(page, case_id, f"sign-qr-{tag}")
    return uri


def _await_user_scan(
    *,
    case_id: str,
    browser,
    page: Page,
    kind: str,
    push_qr,
    cancelled,
    success_check,
    timeout_error: str,
    timeout_sec: float = 600,
) -> tuple[bool, Page, str | None]:
    """等用户扫码：共用 refresh / cancel / 超时；success_check(browser, page)->bool。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if cancelled():
            return False, page, "用户取消"
        try:
            from apps.worker.run_fill_session import consume_sign_qr_refresh

            if consume_sign_qr_refresh(case_id, kind):
                push_qr(kind)
        except Exception:
            pass
        try:
            page = _active_page(browser) or page
            if success_check(browser, page):
                return True, page, None
            _safe_wait(page, 1000)
        except Exception as e:
            if _is_target_closed(e):
                if success_check(browser, page):
                    return True, page, None
                time.sleep(1.0)
                continue
            raise
    page = _active_page(browser) or page
    if success_check(browser, page):
        return True, page, None
    return False, page, timeout_error


def _active_page(browser) -> Page | None:
    try:
        pages = list(browser.pages)
        for p in reversed(pages):
            try:
                if not p.is_closed():
                    return p
            except Exception:
                continue
    except Exception:
        pass
    return None


def _is_target_closed(exc: BaseException) -> bool:
    msg = str(exc or "").lower()
    return (
        "target page" in msg
        or "has been closed" in msg
        or "browser has been closed" in msg
        or "context or browser has been closed" in msg
    )


def _any_open_page_has(browser, marker: str) -> bool:
    try:
        for p in list(browser.pages):
            try:
                if p.is_closed():
                    continue
            except Exception:
                continue
            if _page_has(p, marker):
                return True
    except Exception:
        pass
    return False


def _safe_wait(page: Page | None, ms: int = 1000) -> None:
    if page is None:
        time.sleep(max(0.05, ms / 1000.0))
        return
    try:
        if page.is_closed():
            time.sleep(max(0.05, ms / 1000.0))
            return
        page.wait_for_timeout(ms)
    except Exception as e:
        if _is_target_closed(e):
            time.sleep(max(0.05, ms / 1000.0))
            return
        raise


def run_e_sign_flow(
    case_id: str,
    browser,
    *,
    on_qr: OnQr | None = None,
    on_phase: OnPhase | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False}
    page = _active_page(browser)
    if page is None:
        result["error"] = "no-browser-page"
        return result
    try:
        page._gamaz_case_id = case_id  # type: ignore[attr-defined]
    except Exception:
        pass

    def phase(name: str) -> None:
        result["phase"] = name
        if on_phase:
            try:
                on_phase(name)
            except Exception:
                pass

    def push_qr(kind: str) -> str:
        nonlocal page
        uri = ""
        for _ in range(12):
            page = _active_page(browser) or page
            uri = _capture_qr_uri(page, case_id, kind)
            if uri:
                break
            page.wait_for_timeout(800)
        if uri and on_qr:
            try:
                on_qr(uri, kind)
            except Exception:
                pass
        result[f"qr_{kind}"] = bool(uri)
        return uri

    def cancelled() -> bool:
        return store.is_cancelled(case_id)

    store.set_status(
        case_id,
        statuses.SIGNING,
        customer_message="正在进入电子签名流程…",
    )
    phase("start")
    _shot(page, case_id, "sign-start")

    # 图1：成功页中央蓝钮「进度跟踪」（勿点说明文字里的同名词）
    def _ensure_progress_entry() -> bool:
        nonlocal page
        ok, page = _click_progress_tracking(page, browser)
        if ok:
            return True
        # 已在成功页却点不到：多等一会再试（SPA 渲染）
        if _page_has(page, "请根据短信提示") or _page_has(page, "查询反馈结果"):
            page.wait_for_timeout(1500)
            ok, page = _click_progress_tracking(page, browser)
            if ok:
                return True
            # JS 兜底：点可见的 button/a，文案恰好为「进度跟踪」
            try:
                clicked = page.evaluate(
                    """() => {
                      const nodes = Array.from(document.querySelectorAll('button, a, [role=button], .el-button'));
                      const btn = nodes.find(el => (el.innerText || '').trim() === '进度跟踪');
                      if (!btn) return false;
                      btn.scrollIntoView({block:'center'});
                      btn.click();
                      return true;
                    }"""
                )
                if clicked:
                    page.wait_for_timeout(1500)
                    page = _active_page(browser) or page
                    return True
            except Exception:
                pass
        # 不在成功页时再回门户找入口（避免成功页点失败后被跳走）
        try:
            from apps.worker import config

            candidates = [
                getattr(config, "YCT_ONLINE_URL", "") or "",
                "https://online.yct.sh.gov.cn/web-yct-master/",
                getattr(config, "YCT_PORTAL_URL", "") or "",
                "https://yct.sh.gov.cn/portal_yct/",
            ]
        except Exception:
            candidates = [
                "https://online.yct.sh.gov.cn/web-yct-master/",
                "https://yct.sh.gov.cn/portal_yct/",
            ]
        for url in candidates:
            if not url:
                continue
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1200)
                page = _active_page(browser) or page
                ok, page = _click_progress_tracking(page, browser)
                if ok:
                    return True
            except Exception:
                continue
        return False

    if not _ensure_progress_entry():
        result["error"] = "未找到进度跟踪按钮"
        _shot(page, case_id, "sign-progress-miss")
        _dump(case_id, "fill-sign-result.json", result)
        return result
    page.wait_for_timeout(1500)
    page = _active_page(browser) or page
    if not _wait_any(
        page,
        ["我的申请", "查看签名情况", "待电子签名", "申请状态"],
        case_id=case_id,
        timeout_sec=25,
    ):
        result["error"] = "已点进度跟踪但未进入申请列表"
        _shot(page, case_id, "sign-after-progress-fail")
        _dump(case_id, "fill-sign-result.json", result)
        return result
    _shot(page, case_id, "sign-after-progress")
    result["progressTracking"] = True

    # 图2：查看签名情况
    if not _click_text(page, ["查看签名情况"], exact=False):
        result["error"] = "未找到查看签名情况"
        _dump(case_id, "fill-sign-result.json", result)
        return result
    page.wait_for_timeout(1500)
    page = _active_page(browser) or page
    _wait_any(
        page,
        ["我要电子签名", "未签名", "住所使用相关文件"],
        case_id=case_id,
        timeout_sec=25,
    )
    _shot(page, case_id, "sign-status-list")
    result["viewSignStatus"] = True

    # 图3：我要电子签名
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    page.wait_for_timeout(400)
    if not _click_text(page, ["我要电子签名"], exact=False):
        result["error"] = "未找到我要电子签名"
        _dump(case_id, "fill-sign-result.json", result)
        return result
    page.wait_for_timeout(1500)
    page = _active_page(browser) or page
    _wait_any(
        page, ["我是自然人", "我是企业", "电子签名"], case_id=case_id, timeout_sec=25
    )
    _shot(page, case_id, "sign-choose-entity")
    result["wantESign"] = True

    # 图4：我是自然人（大卡片；勿点提示文案里的「我是自然人」）
    ok_nat, page = _click_natural_person(page, browser)
    if not ok_nat:
        result["error"] = "未选中我是自然人"
        _shot(page, case_id, "sign-natural-person-miss")
        _dump(case_id, "fill-sign-result.json", result)
        return result
    page = _active_page(browser) or page
    if not _wait_any(
        page, ["居民身份证", "下一步", "所持证件"], case_id=case_id, timeout_sec=20
    ):
        # 仍停在身份选择页 → 点击未生效
        if _on_natural_person_select_page(page):
            result["error"] = "已点我是自然人但未进入证件类型页"
            _shot(page, case_id, "sign-natural-person-stuck")
            _dump(case_id, "fill-sign-result.json", result)
            return result
    _shot(page, case_id, "sign-natural-person")
    result["naturalPerson"] = True

    # 图5：证件类型默认居民身份证 → 下一步
    _click_text(page, ["中华人民共和国居民身份证", "居民身份证"], exact=False)
    page.wait_for_timeout(400)
    if not _click_text(page, ["下一步"], exact=True):
        _click_text(page, ["下一步"], exact=False)
    page.wait_for_timeout(1500)
    page = _active_page(browser) or page
    _wait_any(
        page, ["认证方式", "支付宝", "签名人身份"], case_id=case_id, timeout_sec=25
    )
    _shot(page, case_id, "sign-id-type-next")
    result["idTypeNext"] = True

    # 图6：认证方式二：支付宝
    if not _click_text(
        page,
        ["认证方式二：支付宝", "认证方式二", "支付宝 (ALIPAY)", "支付宝"],
        exact=False,
    ):
        result["error"] = "未找到支付宝认证方式"
        _dump(case_id, "fill-sign-result.json", result)
        return result
    page.wait_for_timeout(1500)
    page = _active_page(browser) or page
    _wait_any(
        page,
        ["扫码登录", "请使用手机支付宝", "快捷登录"],
        case_id=case_id,
        timeout_sec=30,
    )
    page = _active_page(browser) or page
    _shot(page, case_id, "sign-alipay-qr-page")
    result["alipayAuth"] = True

    # 图7：推支付宝二维码 → 等用户扫码
    phase("alipay_qr")
    store.set_status(
        case_id,
        statuses.AWAITING_ALIPAY_QR,
        customer_message="请使用支付宝扫描二维码完成签名登录",
    )
    push_qr("alipay")

    def _alipay_scanned(br, pg) -> bool:
        if pg is None:
            return False
        return any(
            _page_has(pg, m)
            for m in (
                "同意服务协议",
                "登录并授权",
                "签名人身份信息比对成功",
                "知道了",
            )
        )

    ok_scan, page, scan_err = _await_user_scan(
        case_id=case_id,
        browser=browser,
        page=page,
        kind="alipay",
        push_qr=push_qr,
        cancelled=cancelled,
        success_check=_alipay_scanned,
        timeout_error="支付宝扫码超时",
    )
    if not ok_scan:
        result["error"] = scan_err or "支付宝扫码超时"
        _dump(case_id, "fill-sign-result.json", result)
        return result
    result["alipayScanned"] = True
    phase("alipay_scanned")
    store.set_status(
        case_id,
        statuses.SIGNING,
        customer_message="支付宝已授权，正在继续电子签名…",
    )
    _shot(page, case_id, "sign-after-alipay-scan")

    # 图8：同意服务协议 + 登录并授权
    page = _active_page(browser) or page
    if _page_has(page, "同意服务协议") or _page_has(page, "登录并授权"):
        try:
            for scope in _all_scopes(page):
                cb = scope.locator("input[type=checkbox]").first
                if _visible(cb, timeout=400):
                    if not cb.is_checked():
                        cb.check(force=True)
                    break
                lab = scope.get_by_text("同意服务协议", exact=False).first
                if _visible(lab, timeout=300):
                    lab.click(force=True)
                    break
        except Exception:
            _click_text(page, ["同意服务协议"], exact=False)
        page.wait_for_timeout(400)
        if not _click_text(page, ["登录并授权"], exact=False):
            result["error"] = "未点到登录并授权"
            _dump(case_id, "fill-sign-result.json", result)
            return result
        page.wait_for_timeout(2000)
    result["alipayAuthorized"] = True
    _shot(page, case_id, "sign-after-authorize")

    # 图9：知道了
    page = _active_page(browser) or page
    _wait_any(
        page,
        ["知道了", "签名人身份信息比对成功"],
        case_id=case_id,
        timeout_sec=30,
    )
    page = _active_page(browser) or page
    if not _click_text(page, ["知道了"], exact=False):
        result["error"] = "未点到知道了"
        _dump(case_id, "fill-sign-result.json", result)
        return result
    page.wait_for_timeout(1200)
    result["ackKnown"] = True

    # 图11：确认弹窗
    page = _active_page(browser) or page
    _wait_any(
        page,
        ["请在15分钟内完成电子签名", "确认", "批量签署"],
        case_id=case_id,
        timeout_sec=20,
    )
    page = _active_page(browser) or page
    if _page_has(page, "请在15分钟内完成电子签名") or _page_has(page, "15分钟"):
        _click_text(page, ["确认"], exact=True) or _click_text(
            page, ["确认"], exact=False
        )
        page.wait_for_timeout(1200)
    result["confirm15min"] = True
    _shot(page, case_id, "sign-doc-list")

    # 图12：勾选全部 + 批量签署（文案为「批量签署 / Batch Signature」，勿精确匹配）
    page = _active_page(browser) or page
    ok_batch, page = _click_batch_sign(page, browser)
    if not ok_batch:
        result["error"] = "未点到批量签署"
        _shot(page, case_id, "sign-batch-miss")
        _dump(case_id, "fill-sign-result.json", result)
        return result
    result["batchSign"] = True
    _shot(page, case_id, "sign-after-batch")

    # 图13：人脸认证二维码
    page = _active_page(browser) or page
    if not _wait_any(
        page,
        ["扫码人脸认证", "人脸认证", "请使用支付宝/微信扫码"],
        case_id=case_id,
        timeout_sec=40,
    ):
        if _on_batch_sign_page(page):
            result["error"] = "已点批量签署但未进入人脸认证"
            _shot(page, case_id, "sign-batch-stuck")
            _dump(case_id, "fill-sign-result.json", result)
            return result
    page = _active_page(browser) or page
    phase("face_qr")
    store.set_status(
        case_id,
        statuses.AWAITING_SIGN_FACE_QR,
        customer_message="请扫码完成人脸认证以签署材料",
    )
    push_qr("face")

    def _face_done(br, pg) -> bool:
        if _any_open_page_has(br, "签名完成"):
            return True
        return bool(pg is not None and _page_has(pg, "签名完成"))

    ok_scan, page, scan_err = _await_user_scan(
        case_id=case_id,
        browser=browser,
        page=page,
        kind="face",
        push_qr=push_qr,
        cancelled=cancelled,
        success_check=_face_done,
        timeout_error="人脸扫码超时",
    )
    if not ok_scan:
        result["error"] = scan_err or "人脸扫码超时"
        _dump(case_id, "fill-sign-result.json", result)
        return result
    result["faceScanned"] = True
    phase("done")
    store.set_status(
        case_id,
        statuses.SIGN_DONE,
        customer_message="电子签名已完成，可前往一网通办查看进度",
    )
    page = _active_page(browser) or page
    try:
        _shot(page, case_id, "sign-done")
    except Exception:
        pass
    _safe_wait(page, 800)
    result["ok"] = True
    result["error"] = None
    _dump(case_id, "fill-sign-result.json", result)
    return result
