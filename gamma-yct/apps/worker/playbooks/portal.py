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
    url = (page.url or "").lower()
    if "login.jsp" in url or "/uc/login" in url:
        return False
    try:
        if page.get_by_text("退出", exact=False).first.is_visible(timeout=800):
            return True
    except Exception:
        pass
    # 登录后常见须知页
    try:
        body = page.locator("body").inner_text(timeout=1000)
    except Exception:
        body = ""
    return "我已知晓上述内容" in body or "不涉及前置审批" in body


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
    time.sleep(1.5)
    _shot(page, case_id, "portal-after-enterprise")


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


def wait_qr_login(case_id: str, page: Page) -> Page:
    """强制扫码：必须点到扫码入口并等待登录成功（不跳过）。"""
    from apps.worker.playbooks.qr_login import _capture_qr

    store.set_status(
        case_id,
        statuses.AWAITING_QR_LOGIN,
        customer_message="请使用随申办扫描浏览器中的二维码登录一网通办",
    )

    clicked, page = _click_scan_login(page)
    if not clicked:
        # 若已在须知页，说明上一跳已带登录态——按「不跳过登录」仍视为异常，要求显式扫码页
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=1000)
        except Exception:
            pass
        if "我已知晓上述内容" in body or looks_logged_in(page):
            # 已登录：仍截图，允许继续（用户已完成历史扫码）；但记录 reason
            _shot(page, case_id, "already-logged-in-after-enterprise")
            store.set_status(
                case_id,
                statuses.LOGGED_IN,
                reason="企业入口后已是登录态，未再出现扫码页",
            )
            return page
        _shot(page, case_id, "qr-login-missing")
        raise RuntimeError("扫码登录: 未命中可点击目标，已中止后续步骤")

    time.sleep(1.0)
    _shot(page, case_id, "qr-login-shown")
    if config.CAPTURE_QR:
        _capture_qr(page, case_id)

    deadline = time.time() + config.LOGIN_TIMEOUT_SEC
    while time.time() < deadline:
        if store.get_case(case_id)["status"] == statuses.CANCELLED:
            raise RuntimeError("cancelled")

        # 新标签可能在扫码后成为主页面
        for p in _active_pages(page):
            if looks_logged_in(p):
                try:
                    p.bring_to_front()
                except Exception:
                    pass
                store.set_status(case_id, statuses.LOGGED_IN)
                _shot(p, case_id, "logged-in")
                return p
            try:
                body = p.locator("body").inner_text(timeout=800)
            except Exception:
                body = ""
            if "我已知晓上述内容" in body or "不涉及前置审批" in body:
                store.set_status(case_id, statuses.LOGGED_IN)
                _shot(p, case_id, "logged-in-post-redirect")
                return p
        time.sleep(2)

    _shot(page, case_id, "login-timeout")
    raise TimeoutError(f"扫码登录超时（{config.LOGIN_TIMEOUT_SEC}s）")


def acknowledge_and_enter_establishment(page: Page, case_id: str) -> Page:
    time.sleep(1.0)
    dismiss_portal_overlays(page)

    acknowledged = False
    for _ in range(12):
        try:
            page.mouse.wheel(0, 800)
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, 700)")
            except Exception:
                pass
        time.sleep(0.25)
        if _click_text_in(page, ["我已知晓上述内容"], exact=False):
            acknowledged = True
            break
        for fr in page.frames:
            if _click_text_in(fr, ["我已知晓上述内容"], exact=False):
                acknowledged = True
                break
        if acknowledged:
            break

    if not acknowledged:
        _shot(page, case_id, "ack-missing")
        raise RuntimeError("未找到「我已知晓上述内容」")

    time.sleep(0.8)
    _shot(page, case_id, "after-acknowledge")

    labels = [
        "企业设立登记（不涉及前置审批事项）",
        "企业设立登记(不涉及前置审批事项)",
        "不涉及前置审批事项",
    ]
    entered = False
    for p in _active_pages(page):
        if _click_text_in(p, labels, exact=False):
            entered = True
            page = p
            break
        for fr in p.frames:
            if _click_text_in(fr, labels, exact=False):
                entered = True
                page = p
                break
        if entered:
            break
    if not entered:
        _shot(page, case_id, "establish-entry-missing")
        raise RuntimeError("未找到「企业设立登记（不涉及前置审批事项）」")

    time.sleep(2.5)
    dismiss_portal_overlays(page)
    _shot(page, case_id, "name-entry-via-establish")
    return page


def ensure_portal_logged_in(case_id: str, page: Page, *, allow_qr: bool = True) -> Page:
    """强制走门户 → 开办 → 企业 → 扫码。allow_qr=False 仅用于显式调试，默认禁止。"""
    store.set_status(case_id, statuses.LOGGING_IN, customer_message="正在打开企业登记门户…")
    goto_portal(page, case_id)
    click_kaiban_then_enterprise(page, case_id)

    if not allow_qr:
        raise RuntimeError("当前流程禁止跳过扫码登录（SKIP_QR_LOGIN/allow_qr=false）")

    return wait_qr_login(case_id, page)


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
