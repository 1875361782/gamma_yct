"""浏览器启动与通用等待。优先 Chrome，本机无 Chrome 时回退 Edge。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait


def _apply_stealth(driver: WebDriver) -> None:
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            },
        )
    except Exception:
        pass


def _chrome_options(browser_cfg: dict[str, Any]) -> ChromeOptions:
    options = ChromeOptions()
    if browser_cfg.get("headless"):
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=zh-CN")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    binary = browser_cfg.get("binary")
    if binary:
        options.binary_location = binary
    return options


def _edge_options(browser_cfg: dict[str, Any]) -> EdgeOptions:
    options = EdgeOptions()
    if browser_cfg.get("headless"):
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=zh-CN")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    binary = browser_cfg.get("binary") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if Path(binary).exists():
        options.binary_location = binary
    return options


def create_driver(browser_cfg: dict[str, Any]) -> WebDriver:
    width = int(browser_cfg.get("window_width", 1280))
    height = int(browser_cfg.get("window_height", 900))
    preferred = (browser_cfg.get("browser") or "auto").lower()

    errors: list[str] = []
    driver: WebDriver | None = None

    def try_chrome() -> WebDriver:
        return webdriver.Chrome(service=ChromeService(), options=_chrome_options(browser_cfg))

    def try_edge() -> WebDriver:
        return webdriver.Edge(service=EdgeService(), options=_edge_options(browser_cfg))

    attempts: list[tuple[str, Any]] = []
    if preferred == "chrome":
        attempts = [("chrome", try_chrome)]
    elif preferred == "edge":
        attempts = [("edge", try_edge)]
    else:
        attempts = [("chrome", try_chrome), ("edge", try_edge)]

    for name, fn in attempts:
        try:
            driver = fn()
            break
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if driver is None:
        raise RuntimeError(
            "无法启动浏览器。请安装 Chrome 或 Edge，并确保网络可下载对应 driver。\n"
            + "\n".join(errors)
        )

    driver.set_window_size(width, height)
    driver.set_page_load_timeout(int(browser_cfg.get("page_load_timeout", 30)))
    driver.implicitly_wait(float(browser_cfg.get("implicit_wait", 0)))
    _apply_stealth(driver)
    return driver


def wait(driver: WebDriver, timeout: float = 15) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
