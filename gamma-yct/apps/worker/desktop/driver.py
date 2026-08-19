"""Desktop RPA: focus already-logged-in browser window and drive mouse/keyboard."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from PIL import ImageGrab

from apps.worker import config

try:
    import pyautogui
    import pyperclip
except ImportError as e:  # pragma: no cover
    pyautogui = None  # type: ignore
    pyperclip = None  # type: ignore
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None

user32 = ctypes.windll.user32


@dataclass
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int
    title: str
    hwnd: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


def require_deps() -> None:
    if _IMPORT_ERROR is not None:
        raise RuntimeError(
            "desktop_rpa 需要依赖 pyautogui、pyperclip。"
            "请执行: pip install pyautogui pyperclip opencv-python-headless"
        ) from _IMPORT_ERROR
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = float(getattr(config, "DESKTOP_RPA_PAUSE", 0.15))


def _enum_windows() -> list[WindowRect]:
    results: list[WindowRect] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < 200 or rect.bottom - rect.top < 200:
            return True
        results.append(
            WindowRect(rect.left, rect.top, rect.right, rect.bottom, title, int(hwnd))
        )
        return True

    user32.EnumWindows(_cb, 0)
    return results


def find_browser_window(titles: Sequence[str]) -> WindowRect:
    windows = _enum_windows()
    scored: list[tuple[int, WindowRect]] = []
    for win in windows:
        t = win.title.lower()
        score = 0
        for i, key in enumerate(titles):
            if not key:
                continue
            if key.lower() in t or key in win.title:
                score += 100 - i
        if "chrome" in t or "edge" in t or "企业登记" in win.title or "一网通办" in win.title:
            score += 5
        if score:
            scored.append((score, win))
    if not scored:
        preview = ", ".join(w.title[:40] for w in windows[:8]) or "(无可见窗口)"
        raise RuntimeError(f"未找到匹配浏览器窗口（titles={list(titles)}）。当前可见: {preview}")
    scored.sort(key=lambda x: (-x[0], -x[1].width * x[1].height))
    return scored[0][1]


def focus_window(win: WindowRect) -> None:
    hwnd = win.hwnd
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.35)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    win.left, win.top, win.right, win.bottom = rect.left, rect.top, rect.right, rect.bottom


class DesktopDriver:
    """Mouse/keyboard driver scoped to a browser window."""

    def __init__(self, win: WindowRect, layout: dict[str, Any]):
        require_deps()
        self.win = win
        self.layout = layout or {}
        inset = self.layout.get("content_inset") or {}
        self.inset_left = float(inset.get("left", 0.0))
        self.inset_top = float(inset.get("top", 0.10))
        self.inset_right = float(inset.get("right", 0.0))
        self.inset_bottom = float(inset.get("bottom", 0.0))
        self.template_dir = Path(__file__).resolve().parent / "templates"
        conf = float(self.layout.get("image_confidence", 0.82))
        self.image_confidence = conf

    def content_box(self) -> tuple[int, int, int, int]:
        w, h = self.win.width, self.win.height
        left = int(self.win.left + w * self.inset_left)
        top = int(self.win.top + h * self.inset_top)
        right = int(self.win.right - w * self.inset_right)
        bottom = int(self.win.bottom - h * self.inset_bottom)
        return left, top, right, bottom

    def abs_xy(self, x_ratio: float, y_ratio: float) -> tuple[int, int]:
        left, top, right, bottom = self.content_box()
        x = int(left + (right - left) * float(x_ratio))
        y = int(top + (bottom - top) * float(y_ratio))
        return x, y

    def click_rel(self, x_ratio: float, y_ratio: float, clicks: int = 1) -> None:
        x, y = self.abs_xy(x_ratio, y_ratio)
        pyautogui.click(x, y, clicks=clicks)
        time.sleep(0.2)

    def click_point(self, name: str, section: str) -> bool:
        sec = (self.layout.get(section) or {}) if section else self.layout
        pt = sec.get(name) if isinstance(sec, dict) else None
        if not isinstance(pt, dict):
            return False
        self.click_rel(float(pt["x"]), float(pt["y"]))
        return True

    def hotkey(self, *keys: str) -> None:
        pyautogui.hotkey(*keys)
        time.sleep(0.15)

    def press(self, key: str, presses: int = 1) -> None:
        pyautogui.press(key, presses=presses)
        time.sleep(0.12)

    def paste(self, text: str, clear_first: bool = True) -> None:
        text = "" if text is None else str(text)
        if clear_first:
            self.hotkey("ctrl", "a")
            time.sleep(0.05)
        pyperclip.copy(text)
        time.sleep(0.05)
        self.hotkey("ctrl", "v")
        time.sleep(0.15)

    def type_field(self, section: str, point_name: str, text: str, tabs_after: int = 0) -> bool:
        if not self.click_point(point_name, section):
            return False
        time.sleep(0.15)
        self.paste(text)
        for _ in range(tabs_after):
            self.press("tab")
        return True

    def screenshot(self, case_dir: Path, name: str) -> Path:
        shot_dir = case_dir / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        path = shot_dir / f"{name}.png"
        left, top, right, bottom = self.content_box()
        img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        img.save(path)
        return path

    def locate_template(self, template_name: str) -> Optional[tuple[int, int]]:
        path = self.template_dir / template_name
        if not path.exists():
            return None
        left, top, right, bottom = self.content_box()
        region = (left, top, right - left, bottom - top)
        try:
            box = pyautogui.locateOnScreen(
                str(path),
                region=region,
                confidence=self.image_confidence,
            )
        except Exception:
            # opencv missing or not found
            try:
                box = pyautogui.locateOnScreen(str(path), region=region)
            except Exception:
                return None
        if not box:
            return None
        return pyautogui.center(box)

    def click_template(self, template_name: str) -> bool:
        center = self.locate_template(template_name)
        if not center:
            return False
        pyautogui.click(center)
        time.sleep(0.25)
        return True

    def click_text_button(self, templates: Sequence[str], fallback_point: Optional[tuple[str, str]] = None) -> bool:
        for name in templates:
            if self.click_template(name):
                return True
        if fallback_point:
            return self.click_point(fallback_point[1], fallback_point[0])
        return False

    def dismiss_dialogs(self) -> None:
        # Escape closes many Element UI dialogs; then try confirm templates
        self.press("esc")
        time.sleep(0.2)
        for name in ("btn_confirm.png", "dialog_zone.png"):
            if self.click_template(name):
                time.sleep(0.3)
        # common relative center confirm
        confirm = (self.layout.get("name_page") or {}).get("dialog_confirm")
        if isinstance(confirm, dict):
            # only click if we recently saw a dialog — keep cheap: one center click attempt is risky
            pass
