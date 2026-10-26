"""门户标准流程（强制扫码，纯 DOM）：

1. 打开 https://yct.sh.gov.cn/portal_yct/
2. 关闭弹窗
3. 点击「开办」
4. 点击「企业」（layui iframe）
5. 点击「扫码登录」，等待扫码跳转
6. 下拉点击「我已知晓上述内容」
7. 点击「企业设立登记（不涉及前置审批事项）」
"""

from __future__ import annotations

import time
from typing import Optional

from playwright.sync_api import Frame, Page, TimeoutError as PlaywrightTimeoutError

from apps.api import statuses, store
from apps.worker import config


def _shot(page: Page, case_id: str, name: str) -> None:
    path = store.case_dir(case_id) / "screenshots" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        pass


def _visible(loc, timeout: int = 800) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible(timeout=timeout)
    except Exception:
        return False


def _click_locator(loc, timeout: int = 5000) -> bool:
    try:
        if loc.count() == 0:
            return False
        target = loc.first
        if not target.is_visible(timeout=1000):
            return False
        target.scroll_into_view_if_needed(timeout=2000)
        target.click(timeout=timeout)
        time.sleep(0.5)
        return True
    except Exception:
        try:
            loc.first.click(timeout=timeout, force=True)
            time.sleep(0.5)
            return True
        except Exception:
            return False


def _click_text_in(scope: Page | Frame, texts: list[str], *, exact: bool = False, timeout: int = 4000) -> bool:
    for text in texts:
        strategies = [
            scope.get_by_role("button", name=text),
            scope.get_by_role("link", name=text),
            scope.get_by_text(text, exact=exact),
            scope.locator(f"text={text}"),
        ]
        for loc in strategies:
            if _click_locator(loc, timeout=timeout):
                return True
    return False


def _is_business_choice_layer(page: Page, close_btn) -> bool:
    """勿关闭「请选择业务办理」层（开办后的企业入口）。"""
    try:
        layer = close_btn.locator(
            "xpath=ancestor::*[contains(@class,'layui-layer')][1]"
        )
        if layer.count() == 0:
            return False
        text = layer.first.inner_text(timeout=800) or ""
        return "请选择业务办理" in text or ("企业" in text and "个体工商户" in text)
    except Exception:
        return False


def _page_text_snip(page: Page, limit: int = 800) -> str:
    try:
        return (page.locator("body").inner_text(timeout=1200) or "")[:limit]
    except Exception:
        return ""


def is_gov_jump_error_page(page: Page) -> bool:
    """政务网 jumpEtregNew / SSO 跳转失败页（站点自身 500，非本系统）。"""
    url = (page.url or "").lower()
    body = _page_text_snip(page)
    if "500服务器内部错误" in body or "服务器内部错误" in body:
        return True
    if "jumpetregnew" in url and (
        "500" in body or "错误" in body or "信息提示" in body or len(body.strip()) < 40
    ):
        # jump 页几乎空白或仅错误标题
        if "扫码" not in body and "随申办" not in body and "退出" not in body:
            return True
    return False


def recover_from_gov_jump_error(page: Page, case_id: str, *, reason: str = "") -> Page:
    """从 jumpEtregNew 500 回收：关掉坏页，回到门户首页。"""
    _shot(page, case_id, "gov-jump-500")
    try:
        store.set_status(
            case_id,
            statuses.LOGGING_IN,
            customer_message="政务网跳转页异常（站点 500），正在自动重试…",
            reason=reason or "jumpEtregNew 500",
        )
    except Exception:
        pass

    # 关掉明显错误标签，保留至少一个上下文页
    try:
        ctx = page.context
        bad = []
        for p in list(ctx.pages):
            try:
                if is_gov_jump_error_page(p):
                    bad.append(p)
            except Exception:
                continue
        keep = None
        for p in list(ctx.pages):
            if p not in bad:
                keep = p
                break
        for p in bad:
            if keep is None or p == keep:
                continue
            try:
                p.close()
            except Exception:
                pass
        if keep is not None:
            page = keep
            try:
                page.bring_to_front()
            except Exception:
                pass
    except Exception:
        pass

    try:
        page.goto(config.YCT_PORTAL_URL, wait_until="domcontentloaded", timeout=90000)
    except Exception:
        pass
    time.sleep(1.5)
    dismiss_portal_overlays(page)
    _shot(page, case_id, "gov-jump-recovered-portal")
    return page


def dismiss_portal_overlays(page: Page) -> None:
    """关闭门户政策解读等弹层；保留「请选择业务办理」。"""
    for _ in range(5):
        closed = False
        for sel in (
            ".layui-layer-setwin .layui-layer-close",
            ".layui-layer-ico.layui-layer-close",
            ".layui-layer-close1",
            ".layui-layer-close",
            ".el-dialog__headerbtn",
            ".el-message-box__headerbtn",
            "[aria-label='Close']",
            "button:has-text('关闭')",
            "text=暂不需要",
        ):
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 4)):
                    btn = loc.nth(i)
                    if not btn.is_visible(timeout=300):
                        continue
                    if _is_business_choice_layer(page, btn):
                        continue
                    btn.click(timeout=2000)
                    closed = True
                    time.sleep(0.35)
            except Exception:
                continue
        if not closed:
            break


def looks_logged_in(page: Page) -> bool:
    """是否已登录门户。

    注意：登录前「告知书」也可能含「我已知晓」文案，不能单凭该句判定已登录，
    否则会跳过扫码、演示页一直等不到二维码。
    门户顶栏常有隐藏的「退出」副本，`.first` 可能不可见，需遍历可见节点。
    """
    url = (page.url or "").lower()
    if any(k in url for k in ("login.jsp", "/uc/login", "zwdtuser", "ssoLogin", "login?")):
        return False
    try:
        loc = page.get_by_text("退出", exact=False)
        n = min(int(loc.count() or 0), 8)
        for i in range(n):
            try:
                if loc.nth(i).is_visible(timeout=400):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        body = page.locator("body").inner_text(timeout=1000) or ""
    except Exception:
        body = ""
    # 门户首页已登录：顶栏「**x | 退出」+ 开办入口（扫码页不会同时出现）
    if "退出" in body and ("开办" in body or "企业登记在线" in body or "上海企业登记" in body):
        return True
    # 登录后须知/设立入口：须同时具备设立相关文案，避免把告知书当成已登录
    if "不涉及前置审批" in body and (
        "企业设立登记" in body or "我已知晓上述内容" in body
    ):
        return True
    if "企业设立登记" in body and "不涉及前置审批" in body:
        return True
    return False


def _handle_blocking_notice(page: Page) -> bool:
    """关闭挡住扫码入口的告知书/须知（点已知晓/同意等）。"""
    labels = [
        "我已知晓上述内容",
        "我已知晓",
        "同意并继续",
        "已阅读并同意",
        "同意",
    ]
    clicked = False
    for p in _active_pages(page):
        try:
            p.bring_to_front()
        except Exception:
            pass
        for label in labels:
            if _click_text_in(p, [label], exact=False, timeout=1200):
                clicked = True
                time.sleep(0.5)
                break
        if clicked:
            break
        for fr in p.frames:
            for label in labels:
                if _click_text_in(fr, [label], exact=False, timeout=800):
                    clicked = True
                    time.sleep(0.5)
                    break
            if clicked:
                break
        if clicked:
            break
    # 弹层关闭钮（勿关业务选择层）
    if not clicked:
        try:
            dismiss_portal_overlays(page)
        except Exception:
            pass
    return clicked


def goto_portal(page: Page, case_id: Optional[str] = None) -> None:
    page.goto(config.YCT_PORTAL_URL, wait_until="domcontentloaded", timeout=90000)
    time.sleep(2.0)
    dismiss_portal_overlays(page)
    time.sleep(0.4)
    dismiss_portal_overlays(page)
    if case_id:
        _shot(page, case_id, "portal-home")


def _click_kaiban(page: Page) -> bool:
    # 精确点「开办」卡片，避免点中整块父容器
    candidates = [
        page.locator(".navCons.kbBg").first,
        page.locator(".chiBox.route4").first,
        page.locator(".navCld.child-div").filter(has_text="开办").first,
        page.get_by_text("开办", exact=True),
        page.locator("strong").filter(has_text="开办"),
    ]
    for loc in candidates:
        if _click_locator(loc, timeout=5000):
            return True
    return _click_text_in(page, ["开办"], exact=True)


def _click_enterprise(page: Page) -> bool:
    """开办后的「企业」在同域 layui iframe（kbLst4）内；可能新开登录页。"""
    try:
        page.wait_for_selector("iframe[src*='kbLst4'], iframe[id*='layui-layer-iframe']", timeout=8000)
    except Exception:
        pass

    def _try_click(target) -> bool:
        try:
            if target.count() == 0 or not target.first.is_visible(timeout=1500):
                return False
            tgt = target.first
            tgt.scroll_into_view_if_needed(timeout=2000)
            try:
                with page.context.expect_page(timeout=6000) as page_info:
                    tgt.click(timeout=5000)
                new_page = page_info.value
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=20000)
                    new_page.bring_to_front()
                except Exception:
                    pass
            except PlaywrightTimeoutError:
                # 已点击，同页跳转、无新标签
                pass
            time.sleep(1.2)
            return True
        except Exception:
            return False

    frame_selectors = (
        "iframe[src*='kbLst4']",
        "iframe[id*='layui-layer-iframe']",
        "iframe.layui-layer-iframe",
    )
    for fsel in frame_selectors:
        fl = page.frame_locator(fsel)
        for loc in (
            fl.locator("a.progress-item").filter(has_text="企业"),
            fl.get_by_text("企业", exact=True),
            fl.locator(".progress-box").filter(has_text="企业"),
            fl.locator("a").filter(has_text="Enterprise"),
        ):
            if _try_click(loc):
                return True

    for fr in page.frames:
        if "kbLst4" not in (fr.url or "") and "layui-layer" not in (fr.name or ""):
            continue
        loc = fr.locator("a.progress-item").filter(has_text="企业")
        if _try_click(loc):
            return True
        loc2 = fr.get_by_text("企业", exact=True)
        if _try_click(loc2):
            return True

    return _try_click(page.get_by_text("企业", exact=True))


def click_kaiban_then_enterprise(page: Page, case_id: str) -> None:
    last_err = ""
    for attempt in range(1, 4):
        dismiss_portal_overlays(page)
        page.wait_for_timeout(600)
        if not _click_kaiban(page):
            _shot(page, case_id, "kaiban-missing")
            raise RuntimeError("未找到门户「开办」入口")
        time.sleep(1.2)
        _shot(page, case_id, "portal-after-kaiban")
        # 此处不要 dismiss：业务选择层正是下一步要点的「企业」

        if not _click_enterprise(page):
            _shot(page, case_id, "enterprise-missing")
            raise RuntimeError("未找到「开办」后的「企业」入口（含 kbLst4 iframe）")
        time.sleep(1.8)
        _shot(page, case_id, "portal-after-enterprise")

        # 点「企业」后常落到 jumpEtregNew；政务网偶发 500，自动回门户重试
        hit_err = False
        for p in _active_pages(page):
            if is_gov_jump_error_page(p):
                hit_err = True
                last_err = p.url or "jumpEtregNew"
                page = recover_from_gov_jump_error(
                    p, case_id, reason=f"attempt={attempt} url={last_err}"
                )
                break
        if hit_err:
            time.sleep(1.2 + attempt * 0.8)
            continue

        # 首次进入常弹出告知书，挡住扫码；点掉后再等登录页
        for _ in range(4):
            if looks_logged_in(page):
                break
            if not _handle_blocking_notice(page):
                break
            time.sleep(0.6)
        _shot(page, case_id, "portal-after-enterprise-notice")
        return

    raise RuntimeError(
        "政务网跳转页连续失败（jumpEtregNew 500）。请稍后重试扫码办理；"
        f"末次地址：{last_err or 'unknown'}"
    )


def _active_pages(page: Page) -> list[Page]:
    pages = list(page.context.pages)

    def _rank(p: Page) -> tuple[int, int]:
        url = (p.url or "").lower()
        if "login" in url or "zwdtuser" in url:
            return (0, 0)
        if p == page:
            return (1, 0)
        if "portal_yct" in url:
            return (2, 0)
        return (3, 0)

    pages.sort(key=_rank)
    return pages


def _click_scan_login(page: Page) -> tuple[bool, Page]:
    """在当前页 / 新开页 / 各 frame 内点击扫码登录。返回 (是否点到, 当前应观察的 page)。"""
    labels = ["扫码登录", "二维码登录"]

    # 企业点击后可能同页跳转或新开登录页
    time.sleep(1.2)
    deadline = time.time() + 8
    while time.time() < deadline and len(page.context.pages) < 2:
        # 给新标签一点时间
        if any("login" in ((p.url or "").lower()) for p in page.context.pages):
            break
        time.sleep(0.3)

    for p in _active_pages(page):
        try:
            p.bring_to_front()
        except Exception:
            pass
        for label in labels:
            if _click_text_in(p, [label], exact=False, timeout=3000):
                return True, p
        for fr in p.frames:
            if _click_text_in(fr, labels, exact=False, timeout=2000):
                return True, p
        # 常见登录页先点「登录」再切扫码
        if _click_text_in(p, ["登录"], exact=True, timeout=2000):
            time.sleep(0.6)
            for label in labels:
                if _click_text_in(p, [label], exact=False, timeout=3000):
                    return True, p
            for fr in p.frames:
                if _click_text_in(fr, labels, exact=False, timeout=2000):
                    return True, p
    return False, page


def wait_qr_login(
    case_id: str,
    page: Page,
    *,
    on_qr=None,
    on_login=None,
) -> Page:
    """强制扫码：必须点到扫码入口并等待登录成功（不跳过）。"""
    from apps.worker.playbooks.qr_login import capture_qr_data_uri, wait_capture_qr_data_uri

    store.set_status(
        case_id,
        statuses.AWAITING_QR_LOGIN,
        customer_message="请使用随申办扫描演示页中的二维码登录一网通办",
    )

    def _pick_logged_in_page(seed: Page) -> Optional[Page]:
        for p in _active_pages(seed):
            if looks_logged_in(p):
                return p
        return None

    # 已真实登录（有退出/设立入口）才跳过扫码
    logged = _pick_logged_in_page(page)
    if logged is not None:
        page = logged
        try:
            page.bring_to_front()
        except Exception:
            pass
        _shot(page, case_id, "already-logged-in-before-qr")
        store.set_status(
            case_id,
            statuses.LOGGED_IN,
            reason="企业入口后已是登录态，未再出现扫码页",
        )
        if on_login:
            try:
                on_login()
            except Exception:
                pass
        return page

    # 告知书挡在扫码前：先处理再点扫码
    for _ in range(3):
        _handle_blocking_notice(page)
        time.sleep(0.4)

    clicked, page = _click_scan_login(page)
    if not clicked:
        for _ in range(3):
            _handle_blocking_notice(page)
            time.sleep(0.5)
            clicked, page = _click_scan_login(page)
            if clicked:
                break
    if not clicked:
        logged = _pick_logged_in_page(page)
        if logged is not None:
            page = logged
            try:
                page.bring_to_front()
            except Exception:
                pass
            _shot(page, case_id, "already-logged-in-after-enterprise")
            store.set_status(
                case_id,
                statuses.LOGGED_IN,
                reason="企业入口后已是登录态，未再出现扫码页",
            )
            if on_login:
                try:
                    on_login()
                except Exception:
                    pass
            return page
        _shot(page, case_id, "qr-login-missing")
        raise RuntimeError("扫码登录: 未命中可点击目标，已中止后续步骤")

    time.sleep(1.0)
    _shot(page, case_id, "qr-login-shown")
    # 首次截取常早于二维码渲染：等待重试后再推送（勿用整页截图冒充）
    if config.CAPTURE_QR or on_qr:
        uri = wait_capture_qr_data_uri(page, case_id=case_id, timeout_sec=20)
        if on_qr and uri:
            try:
                on_qr(uri)
            except Exception:
                pass
        elif config.CAPTURE_QR and uri:
            capture_qr_data_uri(page, case_id=case_id)

    deadline = time.time() + config.LOGIN_TIMEOUT_SEC
    last_qr_push = 0.0
    while time.time() < deadline:
        if store.is_cancelled(case_id) or store.consume_cancel(case_id):
            raise RuntimeError("cancelled")
        try:
            if str(store.get_case(case_id).get("status") or "") == statuses.CANCELLED:
                raise RuntimeError("cancelled")
        except RuntimeError:
            raise
        except Exception:
            pass

        # 新标签可能在扫码后成为主页面
        for p in _active_pages(page):
            if store.is_cancelled(case_id):
                raise RuntimeError("cancelled")
            if is_gov_jump_error_page(p):
                page = recover_from_gov_jump_error(p, case_id, reason="qr-wait-jump-500")
                try:
                    click_kaiban_then_enterprise(page, case_id)
                    clicked2, page = _click_scan_login(page)
                    if clicked2:
                        _shot(page, case_id, "qr-login-shown-after-jump-retry")
                except Exception:
                    pass
                continue
            if looks_logged_in(p):
                try:
                    p.bring_to_front()
                except Exception:
                    pass
                store.set_status(case_id, statuses.LOGGED_IN)
                _shot(p, case_id, "logged-in")
                if on_login:
                    try:
                        on_login()
                    except Exception:
                        pass
                return p

        # 二维码会刷新：周期性推送到演示页（已取消则不再拉取）
        now = time.time()
        if on_qr and now - last_qr_push >= 3.0:
            last_qr_push = now
            if store.is_cancelled(case_id):
                raise RuntimeError("cancelled")
            try:
                uri = capture_qr_data_uri(
                    page, case_id=case_id, allow_fullpage=False
                )
                if uri:
                    on_qr(uri)
            except Exception:
                pass
        time.sleep(0.5)

    _shot(page, case_id, "login-timeout")
    raise TimeoutError(f"扫码登录超时（{config.LOGIN_TIMEOUT_SEC}s）")


def _js_click_ack_known() -> str:
    """精确点「我已知晓上述内容」按钮（须知页底部）。"""
    return """() => {
      const needles = ['我已知晓上述内容', '我已知晓'];
      const nodes = Array.from(document.querySelectorAll(
        'button, a, .el-button, [role="button"], span, div, li, p'
      ));
      for (const want of needles) {
        const hits = nodes.filter(el => {
          const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
          if (t !== want && !(want.length >= 4 && t === want)) return false;
          const r = el.getBoundingClientRect();
          return r.width > 40 && r.height > 14 && r.bottom > 0 && r.top < (window.innerHeight + 200);
        });
        // 优先短节点（真按钮），避免点到整页容器
        hits.sort((a, b) => {
          const ta = (a.innerText || '').replace(/\\s+/g, '').length;
          const tb = (b.innerText || '').replace(/\\s+/g, '').length;
          return ta - tb;
        });
        const btn = hits.find(el => {
          const t = (el.innerText || '').replace(/\\s+/g, '');
          return t === '我已知晓上述内容' || t === '我已知晓';
        });
        if (btn) {
          try { btn.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e) {}
          btn.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
          if (typeof btn.click === 'function') btn.click();
          return {ok:true, text:(btn.innerText || '').trim().slice(0, 20)};
        }
      }
      return {ok:false};
    }"""


def _ack_button_present(page: Page) -> bool:
    """页面上是否仍有可点的「我已知晓」——有则绝不能跳过。"""
    for p in _active_pages(page):
        try:
            loc = p.get_by_text("我已知晓上述内容", exact=False)
            if loc.count() > 0 and loc.first.is_visible(timeout=600):
                return True
        except Exception:
            pass
        try:
            for fr in p.frames:
                loc = fr.get_by_text("我已知晓上述内容", exact=False)
                if loc.count() > 0 and loc.first.is_visible(timeout=400):
                    return True
        except Exception:
            pass
        try:
            hit = p.evaluate(
                """() => {
                  const nodes = Array.from(document.querySelectorAll('button,a,.el-button,[role="button"],span,div'));
                  return nodes.some(el => {
                    const t = (el.innerText || '').replace(/\\s+/g, '');
                    if (t !== '我已知晓上述内容' && t !== '我已知晓') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 40 && r.height > 14;
                  });
                }"""
            )
            if hit:
                return True
        except Exception:
            pass
    return False


def _click_ack_known(page: Page) -> bool:
    """滚动并点击「我已知晓上述内容」。"""
    labels = ["我已知晓上述内容", "我已知晓"]
    for p in _active_pages(page):
        try:
            p.bring_to_front()
        except Exception:
            pass
        # 须知很长，按钮在底部：多滚几屏
        for _ in range(10):
            try:
                p.mouse.wheel(0, 900)
            except Exception:
                try:
                    p.evaluate("window.scrollBy(0, 800)")
                except Exception:
                    pass
            time.sleep(0.2)
            if _click_text_in(p, labels, exact=False, timeout=1200):
                return True
            for fr in p.frames:
                if _click_text_in(fr, labels, exact=False, timeout=800):
                    return True
            try:
                res = p.evaluate(_js_click_ack_known())
                if isinstance(res, dict) and res.get("ok"):
                    time.sleep(0.5)
                    return True
            except Exception:
                pass
            for fr in p.frames:
                try:
                    res = fr.evaluate(_js_click_ack_known())
                    if isinstance(res, dict) and res.get("ok"):
                        time.sleep(0.5)
                        return True
                except Exception:
                    pass
    return False


def _click_establish_no_preapproval(page: Page) -> bool:
    """点击「企业设立登记（不涉及前置审批事项）」，多种策略 + 重试。"""
    labels = [
        "企业设立登记（不涉及前置审批事项）",
        "企业设立登记(不涉及前置审批事项)",
        "不涉及前置审批事项",
    ]
    # 先常规文本点击
    for p in _active_pages(page):
        if _click_text_in(p, labels, exact=False, timeout=2500):
            return True
        for fr in p.frames:
            if _click_text_in(fr, labels, exact=False, timeout=1500):
                return True
    # 卡片常是 div/可点区域：用 JS 按全文匹配点击
    for p in _active_pages(page):
        try:
            ok = p.evaluate(
                """() => {
                  const needles = [
                    '企业设立登记（不涉及前置审批事项）',
                    '企业设立登记(不涉及前置审批事项)',
                    '不涉及前置审批事项',
                  ];
                  const nodes = Array.from(document.querySelectorAll('a,button,div,li,span,p'));
                  for (const n of needles) {
                    const hit = nodes.find(el => {
                      const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
                      if (!t.includes(n.replace(/\\s+/g, ''))) return false;
                      // 避免点到整页容器：优先短文案节点
                      return t.length < 80;
                    });
                    if (hit) { hit.click(); return true; }
                  }
                  return false;
                }"""
            )
            if ok:
                time.sleep(0.8)
                return True
        except Exception:
            pass
    return False


def _on_establish_choice_page(page: Page) -> bool:
    """是否已在「三项设立入口」选择页。

    注意：登录后「须知」正文也会出现「不涉及/涉及前置审批」字样，
    不能单凭文案判断；若仍有「我已知晓」按钮则一定还在须知页。
    """
    if _ack_button_present(page):
        return False
    url = (page.url or "").lower()
    if "establish-firm" in url:
        return True
    try:
        body = page.locator("body").inner_text(timeout=1200) or ""
    except Exception:
        body = ""
    # 三选一页特征：返回首页/进度查询 + 两条设立登记入口，且无我已知晓按钮
    has_two = "不涉及前置审批" in body and "涉及前置审批" in body
    has_nav = ("返回首页" in body and "进度查询" in body) or "已办理企业名称预保留" in body
    return has_two and has_nav and ("我已知晓上述内容" not in body)


def acknowledge_and_enter_establishment(page: Page, case_id: str) -> Page:
    time.sleep(1.0)
    dismiss_portal_overlays(page)
    _shot(page, case_id, "before-acknowledge")

    # 只要还能看到「我已知晓」，就必须点，绝不能因文案误判跳过
    already_choice = _on_establish_choice_page(page) and not _ack_button_present(page)
    if already_choice:
        for p in _active_pages(page):
            if _on_establish_choice_page(p):
                page = p
                try:
                    p.bring_to_front()
                except Exception:
                    pass
                break

    if not already_choice:
        acknowledged = False
        for attempt in range(15):
            if store.is_cancelled(case_id):
                raise RuntimeError("cancelled")
            if _click_ack_known(page):
                acknowledged = True
                break
            # 点完或路由中：已进入三选一则视为成功
            if _on_establish_choice_page(page):
                acknowledged = True
                break
            if attempt in (4, 9):
                dismiss_portal_overlays(page)
                _shot(page, case_id, f"ack-retry-{attempt}")
            time.sleep(0.45)

        if not acknowledged:
            # 最后再确认一次：若按钮仍在则失败；若已到三选一则继续
            if _on_establish_choice_page(page):
                acknowledged = True
            else:
                _shot(page, case_id, "ack-missing")
                raise RuntimeError("未找到或未能点击「我已知晓上述内容」")

        time.sleep(1.2)
        _shot(page, case_id, "after-acknowledge")
        # 等 SPA 路由到 establish-firm（须知点过后才出现三选一）
        deadline = time.time() + 15
        while time.time() < deadline:
            if _ack_button_present(page):
                # 偶发点了没生效：再点一次
                _click_ack_known(page)
                time.sleep(0.8)
            for p in _active_pages(page):
                if _on_establish_choice_page(p):
                    page = p
                    try:
                        p.bring_to_front()
                    except Exception:
                        pass
                    break
            else:
                time.sleep(0.4)
                continue
            break

    dismiss_portal_overlays(page)
    time.sleep(0.4)

    entered = False
    for attempt in range(8):
        dismiss_portal_overlays(page)
        # Chrome「要恢复页面吗」提示条偶发挡点击：先点一下空白再试
        if attempt > 0:
            try:
                page.mouse.click(40, 120)
            except Exception:
                pass
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(0.35)
        if _click_establish_no_preapproval(page):
            entered = True
            break
        time.sleep(0.6 + attempt * 0.2)

    if not entered:
        _shot(page, case_id, "establish-entry-missing")
        raise RuntimeError("未找到「企业设立登记（不涉及前置审批事项）」")

    time.sleep(2.5)
    dismiss_portal_overlays(page)
    _shot(page, case_id, "name-entry-via-establish")
    return page


def ensure_portal_logged_in(
    case_id: str,
    page: Page,
    *,
    allow_qr: bool = True,
    on_qr=None,
    on_login=None,
) -> Page:
    """强制走门户 → 开办 → 企业 → 扫码。allow_qr=False 仅用于显式调试，默认禁止。"""
    store.set_status(case_id, statuses.LOGGING_IN, customer_message="正在打开企业登记门户…")
    goto_portal(page, case_id)
    click_kaiban_then_enterprise(page, case_id)

    if not allow_qr:
        raise RuntimeError("当前流程禁止跳过扫码登录（SKIP_QR_LOGIN/allow_qr=false）")

    return wait_qr_login(case_id, page, on_qr=on_qr, on_login=on_login)


def open_name_declaration_from_portal(page: Page, case_id: str) -> Page:
    url = page.url or ""
    if any(k in url for k in ("name-declaration", "web-name-apply")):
        _shot(page, case_id, "already-on-name")
        return page

    body = ""
    try:
        body = page.locator("body").inner_text(timeout=1500)
    except Exception:
        pass

    if is_gov_jump_error_page(page):
        page = recover_from_gov_jump_error(page, case_id, reason="open-name-jump-500")
        url = page.url or ""
        body = _page_text_snip(page)

    if "我已知晓上述内容" not in body and "不涉及前置审批" not in body:
        # 登录后回到门户时，再点一次开办→企业
        if "portal_yct" in url or "开办" in body:
            dismiss_portal_overlays(page)
            try:
                click_kaiban_then_enterprise(page, case_id)
            except Exception:
                pass
            time.sleep(1.0)

    page = acknowledge_and_enter_establishment(page, case_id)
    url2 = page.url or ""
    # 也可能在新标签打开 online.yct
    for p in _active_pages(page):
        u = p.url or ""
        if any(k in u for k in ("name-declaration", "web-name-apply", "web-yct-master", "web-reg-apply")):
            try:
                p.bring_to_front()
            except Exception:
                pass
            return p
    if any(k in url2 for k in ("name-declaration", "web-name-apply", "web-yct-master", "web-reg-apply")):
        return page
    # 再等一下路由
    time.sleep(2.0)
    for p in _active_pages(page):
        u = p.url or ""
        if any(k in u for k in ("name-declaration", "web-name-apply", "web-yct-master")):
            return p
    _shot(page, case_id, "name-entry-transition-missing")
    raise RuntimeError("企业设立入口点击后未进入名称申报页面")
