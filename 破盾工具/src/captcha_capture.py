"""元素定位与验证码图像采集。"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Optional

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement


@dataclass
class CaptchaImages:
    background_png: bytes
    piece_png: bytes
    bg_display_width: float
    bg_natural_width: float
    piece_display_width: float


def _by_for(selector: str) -> tuple[str, str]:
    if selector.startswith("//") or selector.startswith("(//"):
        return By.XPATH, selector
    return By.CSS_SELECTOR, selector


def find_first(
    root: WebDriver | WebElement,
    selectors: list[str],
    *,
    visible_only: bool = False,
) -> Optional[WebElement]:
    for sel in selectors:
        by, value = _by_for(sel)
        try:
            elements = root.find_elements(by, value)
        except Exception:
            continue
        for el in elements:
            try:
                if visible_only and not el.is_displayed():
                    continue
                return el
            except Exception:
                continue
    return None


def switch_to_captcha_frame(driver: WebDriver, iframe_selectors: list[str]) -> bool:
    """若验证码在 iframe 中则切入，成功返回 True。"""
    driver.switch_to.default_content()
    frame = find_first(driver, iframe_selectors, visible_only=True)
    if frame is None:
        return False
    driver.switch_to.frame(frame)
    return True


def _canvas_to_png(driver: WebDriver, canvas: WebElement) -> bytes:
    data_url = driver.execute_script(
        "return arguments[0].toDataURL('image/png');", canvas
    )
    if not data_url or "," not in data_url:
        raise RuntimeError("无法从 canvas 导出图片")
    return base64.b64decode(data_url.split(",", 1)[1])


def _element_png(element: WebElement) -> bytes:
    return element.screenshot_as_png


def _decode_data_url(data_url: str) -> Optional[bytes]:
    if not data_url or "base64," not in data_url:
        return None
    try:
        return base64.b64decode(data_url.split("base64,", 1)[1])
    except Exception:
        return None


def _fetch_url_bytes(driver: WebDriver, url: str) -> Optional[bytes]:
    raw = driver.execute_async_script(
        """
        const url = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch(url).then(r => r.arrayBuffer()).then(buf => {
          done(Array.from(new Uint8Array(buf)));
        }).catch(() => done(null));
        """,
        url,
    )
    return _to_bytes(raw)


def _bg_url_bytes(driver: WebDriver, element: WebElement) -> Optional[bytes]:
    """尝试从 img.src 或 CSS background-image 取图（兼容 imgVer 的 base64）。"""
    tag = (element.tag_name or "").lower()
    if tag == "img":
        src = element.get_attribute("src") or ""
        if src.startswith("data:image"):
            return _decode_data_url(src)
        if src.startswith("http") or src.startswith("/"):
            return _fetch_url_bytes(driver, src)

    # 内联 style / computed background（#puzzleShadow）
    for blob in (
        element.get_attribute("style") or "",
        driver.execute_script(
            "return window.getComputedStyle(arguments[0]).backgroundImage;", element
        )
        or "",
    ):
        if not isinstance(blob, str):
            continue
        m = re.search(r"url\([\"']?(data:image[^\"')]+)", blob)
        if m:
            data = _decode_data_url(m.group(1))
            if data:
                return data
        m2 = re.search(r"url\([\"']?(https?://[^\"')]+|/[^\"')]+)", blob)
        if m2:
            data = _fetch_url_bytes(driver, m2.group(1))
            if data:
                return data

    # 子节点 img（部分版本）
    try:
        child = element.find_element(By.CSS_SELECTOR, "img")
        return _bg_url_bytes(driver, child)
    except Exception:
        return None


def _to_bytes(raw: Any) -> Optional[bytes]:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, list):
        return bytes(raw)
    return None


def capture_captcha_images(
    driver: WebDriver,
    captcha_cfg: dict[str, Any],
) -> tuple[CaptchaImages, WebElement, Optional[WebElement]]:
    """
    采集背景图与滑块图。
    返回 (图像数据, 拖动手把元素, 刷新按钮元素或 None)
    """
    bg_el = find_first(driver, captcha_cfg["background_selectors"], visible_only=True)
    piece_el = find_first(driver, captcha_cfg["piece_selectors"], visible_only=True)
    handle = find_first(driver, captcha_cfg["handle_selectors"], visible_only=True)
    refresh = find_first(driver, captcha_cfg.get("refresh_selectors", []), visible_only=True)

    if handle is None:
        raise NoSuchElementException("未找到滑块拖动手把，请检查 config/selectors.yaml")

    if bg_el is None:
        raise NoSuchElementException("未找到验证码背景图元素")

    # 背景
    bg_bytes: Optional[bytes] = None
    if (bg_el.tag_name or "").lower() == "canvas":
        bg_bytes = _canvas_to_png(driver, bg_el)
    else:
        bg_bytes = _to_bytes(_bg_url_bytes(driver, bg_el)) or _element_png(bg_el)

    # 滑块块：没有独立节点时，用整图截图兜底会降低准确率，尽量找 piece
    if piece_el is None:
        raise NoSuchElementException("未找到滑块拼图块元素")

    if (piece_el.tag_name or "").lower() == "canvas":
        piece_bytes = _canvas_to_png(driver, piece_el)
    else:
        piece_bytes = _to_bytes(_bg_url_bytes(driver, piece_el)) or _element_png(piece_el)

    natural_w = driver.execute_script(
        """
        const el = arguments[0];
        if (el.tagName.toLowerCase() === 'canvas') return el.width;
        if (el.naturalWidth) return el.naturalWidth;
        return el.getBoundingClientRect().width;
        """,
        bg_el,
    )
    display_w = float(bg_el.size.get("width") or bg_el.rect.get("width") or 0)
    piece_w = float(piece_el.size.get("width") or piece_el.rect.get("width") or 0)

    images = CaptchaImages(
        background_png=bg_bytes,
        piece_png=piece_bytes,
        bg_display_width=display_w,
        bg_natural_width=float(natural_w or display_w),
        piece_display_width=piece_w,
    )
    return images, handle, refresh
