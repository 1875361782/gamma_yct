"""登录态监听：检测政务网是否已扫码登录成功。

一网通办登录后通常会：
  - 写入 web_session / JSESSIONID / SSO_TOKEN 等 cookie
  - URL 从 loginCountry 跳转到个人中心/首页
  - 出现用户姓名、退出登录、我的主页等 UI 元素
  - 原二维码消失
"""
from __future__ import annotations
from typing import Optional
from playwright.async_api import Page, BrowserContext


# 登录后出现的关键选择器
LOGGED_IN_SELECTORS: list[str] = [
    ".user-info",
    ".user-info-name",
    ".user-name",
    ".header-user",
    "#user-name",
    ".logout",
    "a[href*='logout']",
    "a[href*='exit']",
    "[data-logged-in='1']",
    ".login-success",
    ".nav-user",
    ".user-bar",
    ".user-center",
    ".personal-center",
    ".my-account",
    ".my-page",
]

# URL 跳转到这些关键词视为已登录
LOGGED_IN_URL_KEYWORDS: list[str] = [
    "home.do",
    "main.do",
    "user/profile",
    "userCenter",
    "personal",
    "uc/country/index",
    "wsbs",
    "onlinehall",
]

# 政务网登录前的 URL 关键词（避免误判）
LOGIN_PAGE_URL_KEYWORDS: list[str] = [
    "loginCountry",
    "login",
    "uc/country/login",
    "portal_yct",
    "shanghai.gov.cn",
]

# 登录后页面标题常见关键词（注意门户页标题也会包含「企业登记/一网通办」，不要误填）
LOGGED_IN_TITLE_KEYWORDS: list[str] = [
    "个人中心",
    "我的",
    "用户中心",
    "用户首页",
    "主页",
    "首页",
]


async def is_logged_in(page: Page, context: Optional[BrowserContext] = None) -> bool:
    """综合判定：URL 跳转 / UI 元素 / Cookie / 页面标题。"""
    url = (page.url or "").lower()

    # 0. 如果还在登录页，未登录
    if any(k in url for k in LOGIN_PAGE_URL_KEYWORDS):
        return False

    # 1. URL 跳转到登录后页面
    if any(k in url for k in LOGGED_IN_URL_KEYWORDS):
        return True

    # 2. UI 元素
    for selector in LOGGED_IN_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el is not None:
                return True
        except Exception:
            continue

    # 3. 页面标题
    try:
        title = (await page.title() or "").lower()
        if any(k in title for k in LOGGED_IN_TITLE_KEYWORDS):
            # 排除登录页标题误报
            if "登录" not in title:
                return True
    except Exception:
        pass

    # 4. Cookie 探测
    if context is not None:
        try:
            cookies = await context.cookies()
            cookie_names = {c.get("name", "").lower() for c in cookies}
            for k in ("web_session", "jsessionid", "sso_token", "session", "yct_session", "token"):
                if k in cookie_names:
                    return True
        except Exception:
            pass

    # 5. JS 检测：页面里是否有全局登录标识
    try:
        has_flag = await page.evaluate("""() => {
            return !!(window.__login || window.isLogin || window.userInfo || window.LOGIN);
        }""")
        if has_flag:
            return True
    except Exception:
        pass

    return False


async def is_still_on_login_page(page: Page) -> bool:
    """简单判定是否仍在登录页。"""
    url = (page.url or "").lower()
    return any(k in url for k in LOGIN_PAGE_URL_KEYWORDS)
