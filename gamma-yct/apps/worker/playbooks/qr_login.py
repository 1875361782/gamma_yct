"""门户登录：以 portal_yct 为入口，扫码登录后回到门户。"""

from __future__ import annotations

from playwright.sync_api import Browser, Frame, Page

from apps.worker import config
from apps.worker.playbooks.portal import ensure_portal_logged_in


def _score_qr_candidate(box: dict, *, page_width: float = 1360.0, hint: str = "") -> float:
    """给候选截图节点打分：真正二维码通常更大、近正方形、偏左侧；应用图标应被压低。"""
    w = float(box.get("width") or 0)
    h = float(box.get("height") or 0)
    x = float(box.get("x") or 0)
    if w < 100 or h < 100:
        return -1.0
    ratio = w / max(h, 1.0)
    if ratio < 0.72 or ratio > 1.38:
        return -1.0
    side = min(w, h)
    # 应用 logo 常约 64–110；支付宝登录码通常 ≥140
    if side < 120:
        return -1.0
    score = side
    if 140 <= side <= 360:
        score += 80
    if abs(ratio - 1.0) < 0.12:
        score += 40
    # 支付宝快捷登录：码在左侧黑框，一网通办图标在右侧
    if page_width > 0 and x + w / 2 < page_width * 0.55:
        score += 60
    else:
        score -= 40
    hint_l = (hint or "").lower()
    if any(k in hint_l for k in ("qrcode", "qr-code", "login-qr", "auth-qr")):
        score += 50
    if any(k in hint for k in ("扫码", "支付宝")):
        score += 30
    if any(k in hint for k in ("一网通办", "授权后", "详情", "应用将获得")):
        score -= 200
    return score


def capture_qr_data_uri(
    page: Page,
    case_id: str | None = None,
    *,
    allow_fullpage: bool = True,
    prefer_left: bool = True,
) -> str:
    """截取登录二维码，返回 data:image/png;base64,...；失败返回空串。

    allow_fullpage=False 时不回退整页截图（避免把非二维码页面推给演示端）。
    对多候选按面积/位置打分，避免误截支付宝页右侧「一网通办」图标。
    """
    import base64

    from apps.api import store
    from apps.worker import config

    def _try_capture(p: Page) -> bytes | None:
        selectors = [s.strip() for s in config.QR_SELECTOR.split(",") if s.strip()]
        # 勿把裸 "img" 放太前：支付宝授权页右侧应用图标也会命中
        preferred = [
            "img.qrcode-img",
            ".qrcode img",
            "img[src*='qrcode']",
            "img[src*='qr-code']",
            ".login-qrcode img",
            "#qrcode img",
            "#qrcode",
            "canvas.qrcode",
            "canvas",
            "img[src^='data:image']",
        ]
        # 过滤过于宽泛的配置选择器；仍保留但靠后、靠打分淘汰 logo
        broad = [s for s in selectors if s not in ("img", "canvas")]
        broad_last = [s for s in selectors if s in ("img", "canvas")]
        seen: set[int] = set()
        best_png: bytes | None = None
        best_score = -1.0
        page_width = 1360.0
        try:
            vs = getattr(p, "viewport_size", None)
            if vs and vs.get("width"):
                page_width = float(vs["width"])
            else:
                page_width = float(
                    p.evaluate("() => window.innerWidth || document.documentElement.clientWidth || 1360")
                    or 1360
                )
        except Exception:
            page_width = 1360.0

        for sel in preferred + broad + broad_last:
            try:
                locs = p.locator(sel)
                n = min(locs.count(), 16)
            except Exception:
                continue
            for i in range(n):
                loc = locs.nth(i)
                try:
                    if not loc.is_visible(timeout=400):
                        continue
                    box = loc.bounding_box()
                    if not box:
                        continue
                    # 去重同一节点
                    key = (
                        int(box.get("x", 0)),
                        int(box.get("y", 0)),
                        int(box.get("width", 0)),
                        int(box.get("height", 0)),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    hint = sel
                    try:
                        # 父级文案：左侧「扫码登录」加分，右侧「一网通办」扣分
                        hint += " " + (
                            loc.evaluate(
                                """(el) => {
                                  let n = el, t = '';
                                  for (let i = 0; i < 4 && n; i++) {
                                    t += ' ' + (n.innerText || n.alt || '');
                                    n = n.parentElement;
                                  }
                                  return t.slice(0, 200);
                                }"""
                            )
                            or ""
                        )
                    except Exception:
                        pass
                    score = _score_qr_candidate(
                        box, page_width=page_width if prefer_left else 0.0, hint=hint
                    )
                    if score < 0:
                        continue
                    png = loc.screenshot(type="png")
                    if png and score > best_score:
                        best_score = score
                        best_png = png
                except Exception:
                    continue
        return best_png

    png: bytes | None = None
    # 当前页 + 同上下文其它页（登录常新开标签）
    pages = [page]
    try:
        for p in page.context.pages:
            if p not in pages:
                pages.append(p)
    except Exception:
        pass
    for p in pages:
        try:
            png = _try_capture(p)
        except Exception:
            png = None
        if png:
            break
        # iframe 内二维码
        try:
            for fr in p.frames:
                png = _try_capture(fr)  # type: ignore[arg-type]
                if png:
                    break
        except Exception:
            pass
        if png:
            break

    if png is None and allow_fullpage:
        try:
            png = page.screenshot(type="png", full_page=False)
        except Exception:
            png = None
    if not png:
        return ""
    if case_id:
        qr_path = store.case_dir(case_id) / "qr.png"
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        qr_path.write_bytes(png)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def wait_capture_qr_data_uri(
    page: Page,
    case_id: str | None = None,
    *,
    timeout_sec: float = 20,
) -> str:
    """等待二维码节点渲染完成再截取；首轮失败会重试，避免第一次推空。"""
    import time

    end = time.time() + timeout_sec
    last = ""
    while time.time() < end:
        uri = capture_qr_data_uri(page, case_id=case_id, allow_fullpage=False)
        if uri:
            return uri
        last = uri
        time.sleep(0.7)
    # 超时仍无独立二维码节点时，不再用整页冒充（演示页会无法扫）
    return last or capture_qr_data_uri(page, case_id=case_id, allow_fullpage=False)


def _qr_scopes(page: Page) -> list[Page | Frame]:
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


def capture_alipay_panel_data_uri(page: Page, case_id: str | None = None) -> str:
    """支付宝「快捷登录」页：只截左侧「扫码登录」黑框内的码，勿截右侧一网通办图标。"""
    import base64

    from apps.api import store

    def _visible(loc, timeout: int = 300) -> bool:
        try:
            return bool(loc.count() and loc.is_visible(timeout=timeout))
        except Exception:
            return False

    png: bytes | None = None
    for scope in _qr_scopes(page):
        try:
            panel = None
            for marker in ("请使用手机支付宝扫码", "扫码登录", "请使用手机支付宝"):
                try:
                    node = scope.get_by_text(marker, exact=False).first
                    if not _visible(node, timeout=300):
                        continue
                    panel = node.locator(
                        "xpath=ancestor::*[self::div or self::section or self::td][1]"
                    )
                    for _ in range(4):
                        try:
                            parent = panel.locator("xpath=..")
                            box = parent.bounding_box()
                            if box and box["width"] >= 200 and box["height"] >= 200:
                                panel = parent
                            else:
                                break
                        except Exception:
                            break
                    break
                except Exception:
                    continue
            if panel is None:
                continue
            best = None
            best_side = 0.0
            for sel in ("canvas", "img", "canvas, img"):
                try:
                    locs = panel.locator(sel)
                    n = min(locs.count(), 10)
                except Exception:
                    continue
                for i in range(n):
                    loc = locs.nth(i)
                    if not _visible(loc, timeout=200):
                        continue
                    try:
                        box = loc.bounding_box()
                    except Exception:
                        continue
                    if not box:
                        continue
                    w, h = float(box["width"]), float(box["height"])
                    side = min(w, h)
                    if side < 120:
                        continue
                    if abs(w - h) / max(w, h) > 0.25:
                        continue
                    if side > best_side:
                        best_side = side
                        best = loc
            if best is not None:
                try:
                    png = best.screenshot(type="png")
                except Exception:
                    png = None
            if png is None:
                try:
                    box = panel.bounding_box()
                    if box and box["width"] >= 180 and box["height"] >= 180:
                        png = panel.screenshot(type="png")
                except Exception:
                    pass
            if png:
                break
        except Exception:
            continue

    if png is None:
        for scope in _qr_scopes(page):
            try:
                handle = scope.evaluate_handle(
                    """() => {
                      const has = (el, s) => (el.innerText || '').includes(s);
                      const roots = Array.from(document.querySelectorAll('div, section, td'));
                      let panel = roots.find(el => has(el, '扫码登录') && has(el, '支付宝') && !has(el, '授权后'));
                      if (!panel) panel = roots.find(el => has(el, '扫码登录'));
                      if (!panel) return null;
                      const nodes = Array.from(panel.querySelectorAll('canvas, img'));
                      let best = null, bestSide = 0;
                      for (const el of nodes) {
                        const r = el.getBoundingClientRect();
                        const side = Math.min(r.width, r.height);
                        if (side < 120) continue;
                        if (Math.abs(r.width - r.height) / Math.max(r.width, r.height) > 0.25) continue;
                        if (side > bestSide) { bestSide = side; best = el; }
                      }
                      return best;
                    }"""
                )
                el = handle.as_element() if handle else None
                if el is not None:
                    png = el.screenshot(type="png")
                    if png:
                        break
            except Exception:
                continue

    if not png:
        return ""
    if case_id:
        try:
            qr_path = store.case_dir(case_id) / "qr.png"
            qr_path.parent.mkdir(parents=True, exist_ok=True)
            qr_path.write_bytes(png)
        except Exception:
            pass
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def run_qr_login(
    case_id: str,
    browser: Browser,
    *,
    on_qr=None,
    on_login=None,
) -> Page:
    """门户 → 强制扫码登录 → 返回已登录页。

    ``browser`` 可为 Browser，或 launch_persistent_context 返回的 BrowserContext。
    """
    # persistent context 已有页面/上下文，勿再 new_context
    if hasattr(browser, "new_context"):
        context = browser.new_context(
            locale="zh-CN",
            viewport={"width": 1360, "height": 900},
            accept_downloads=False,
        )
        page = context.new_page()
    else:
        context = browser
        page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
    return ensure_portal_logged_in(
        case_id, page, allow_qr=True, on_qr=on_qr, on_login=on_login
    )
