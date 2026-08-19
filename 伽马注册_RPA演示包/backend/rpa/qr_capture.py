"""二维码捕获：抓政务网登录页 QR 元素 → PNG base64。

借鉴 MediaCrawler media_platform/xhs/login.py 中 utils.find_login_qrcode() 的做法：
  - 用 selector 定位 QR 元素
  - element.screenshot() → PNG bytes
  - base64.b64encode() → data URL

每个目标政务网的 QR selector 不同，所以采用"按优先级尝试"策略。

注意：上海一网通办 / 政务网默认登录页是「账号密码」表单，
必须先点「扫码登录」tab 才会出现 QR。我们的策略：
  1. 第一帧抓到 QR：直接返回
  2. 第一帧没抓到：尝试点「扫码登录」/「扫码」/「电子证照」类 tab
  3. 再次抓 QR
"""
from __future__ import annotations
import base64
import re
from typing import Optional
from playwright.async_api import Page, ElementHandle


# 上海一网通办 / 通用政务网常见 QR 选择器（按命中率降序）
QR_SELECTORS: list[str] = [
    "img.qrcode-img",
    ".qrcode img",
    "img[src*='qrcode']",
    "img[src*='qr-code']",
    "img[src^='data:image/png']",
    "img[src^='data:image/jpeg']",
    "img[src*='base64']",
    ".login-qrcode img",
    ".qrcode-box img",
    "canvas.qrcode",
    "#qrcode",
    "#qrcodeImg",
    ".qr-img img",
    # 一网通办实测常见
    "img[alt*='扫码']",
    "img[alt*='二维码']",
    ".tab-content img[src*='data:image']",
    "[class*='qrCode'] img",
    "[class*='qr-code'] img",
]


# 「扫码登录」tab/button 选择器（点击后才出 QR）
QR_TAB_SELECTORS: list[str] = [
    "text=扫码登录",
    "text=扫码",
    "text=电子证照登录",
    "text=电子证照",
    "text=QR 登录",
    "text=二维码登录",
    "text=随申办登录",
    "text=扫码/电子证照",
    "button:has-text('扫码')",
    "a:has-text('扫码')",
    ".tab:has-text('扫码')",
    "[role='tab']:has-text('扫码')",
    ".login-tab:has-text('扫码')",
    ".login-way:has-text('扫码')",
    ".ant-tabs-tab:has-text('扫码')",
    ".el-tabs__item:has-text('扫码')",
]

# 排除规则：包含这些字的元素不要点（如"扫码下载 APP"）
QR_TAB_EXCLUDE = re.compile(r"下载|说明|教程|帮助|app|APP|什么是|如何|注册|忘记|找回|登录失败")


async def _click_qr_tab(page: Page) -> bool:
    """尝试点「扫码登录」tab，点中返回 True。"""
    # 1. Playwright text selector
    for sel in QR_TAB_SELECTORS:
        try:
            if sel.startswith("text="):
                # 用 inner_text 匹配
                txt = sel[5:]
                el = await page.query_selector(f"text=\"{txt}\"")
                if not el:
                    el = await page.query_selector(f"text={txt}")
                if not el:
                    continue
            else:
                # CSS 选择器，需要 playwright 不支持 :has-text 走 page.evaluate
                if ":has-text" in sel:
                    # 退化到 JS
                    el = await page.evaluate_handle(
                        """(sub) => {
                            const all = document.querySelectorAll('button, a, [role="tab"], .tab, .login-tab, .login-way, .ant-tabs-tab, .el-tabs__item');
                            for (const e of all) {
                                if (e.textContent && e.textContent.includes(sub)) return e;
                            }
                            return null;
                        }""",
                        sel.split("'")[1] if "'" in sel else sel.split("：")[-1].strip("'"),
                    )
                    el = el.as_element() if el else None
                else:
                    el = await page.query_selector(sel)
            if el is None:
                continue
            # 排除误点
            try:
                txt = (await el.text_content() or "").strip()
            except Exception:
                txt = ""
            if QR_TAB_EXCLUDE.search(txt):
                continue
            if not txt or len(txt) > 20:
                continue
            await el.click(timeout=2000)
            await page.wait_for_timeout(800)  # 等 QR 渲染
            return True
        except Exception:
            continue
    return False


async def _try_capture(page: Page) -> Optional[str]:
    """按优先级尝试多个 QR 选择器，返回 data:image/png;base64,... 形式。"""
    for selector in QR_SELECTORS:
        try:
            el: Optional[ElementHandle] = await page.query_selector(selector)
            if el is None:
                continue
            # 如果是 img 且 src 本身已是 data URL，直接复用避免重新截图失真
            tag = await el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "img":
                src = await el.get_attribute("src")
                if src and src.startswith("data:image"):
                    # 统一转成 png base64（政务网通常已是 png）
                    return src
            png_bytes = await el.screenshot(timeout=2000)
            if png_bytes and len(png_bytes) > 200:
                return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        except Exception:
            continue

    # 兜底 1：canvas 渲染的二维码（部分政务网用 canvas 画 QR）
    try:
        canvas = await page.query_selector("canvas")
        if canvas:
            png_bytes = await canvas.screenshot(timeout=2000)
            if png_bytes and len(png_bytes) > 200:
                return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    except Exception:
        pass

    # 兜底 2：扫描页面所有 img，找近似正方形且 src 是 base64 的图片（上海一网通办实测）
    try:
        src = await page.evaluate(
            """() => {
                const imgs = Array.from(document.querySelectorAll('img'));
                const candidates = imgs.filter(i => {
                    const w = i.naturalWidth, h = i.naturalHeight, src = i.getAttribute('src') || '';
                    const square = w > 0 && h > 0 && Math.abs(w - h) / Math.max(w, h) < 0.3;
                    return w >= 110 && w <= 420 && square && (src.startsWith('data:image') || src.includes('base64'));
                });
                candidates.sort((a,b) => (b.naturalWidth*b.naturalHeight) - (a.naturalWidth*a.naturalHeight));
                return candidates.length ? candidates[0].getAttribute('src') : null;
            }"""
        )
        if src and isinstance(src, str) and src.startswith("data:image"):
            return src
    except Exception:
        pass
    return None


async def capture_qr_base64(page: Page) -> Optional[str]:
    """抓 QR 元素 → base64 PNG。失败时尝试点「扫码登录」tab 后再抓一次。

    返回 None 表示未找到（页面还在加载 / 非 QR 阶段）。
    """
    # 1. 直接抓
    qr = await _try_capture(page)
    if qr:
        return qr
    # 2. 没抓到 → 尝试点「扫码登录」tab
    clicked = await _click_qr_tab(page)
    if clicked:
        await page.wait_for_timeout(1200)  # 给 QR 渲染时间
        qr = await _try_capture(page)
        if qr:
            return qr
    # 3. 留个排查信号：返回 None
    return None


async def find_qr_selector(page: Page) -> Optional[str]:
    """返回第一个命中的选择器（用于调试 / 日志）。"""
    for selector in QR_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el is not None:
                return selector
        except Exception:
            continue
    return None