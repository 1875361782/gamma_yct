import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

AUTOMATION_MODE = os.environ.get("AUTOMATION_MODE", "dry_run")  # dry_run | playwright | auto | desktop_rpa
ALLOW_FINAL_SUBMIT = os.environ.get("ALLOW_FINAL_SUBMIT", "false").lower() == "true"
# desktop_rpa：动作间隔（秒），交给 pyautogui.PAUSE
DESKTOP_RPA_PAUSE = float(os.environ.get("DESKTOP_RPA_PAUSE", "0.15"))
# Playwright CDP 失败时是否回退桌面键鼠
DESKTOP_RPA_FALLBACK = os.environ.get("DESKTOP_RPA_FALLBACK", "false").lower() == "true"
YCT_PORTAL_URL = os.environ.get(
    "YCT_PORTAL_URL", "https://yct.sh.gov.cn/portal_yct/"
)
# 一网通办登录页；可按实际跳转覆盖
LOGIN_URL = os.environ.get(
    "YCT_LOGIN_URL",
    "https://zwdtuser.sh.gov.cn/uc/login/login.jsp?kind=0&self=self"
    "&redirect_uri=https%3A%2F%2Fyct.sh.gov.cn%2Fportal_yct%2F",
)
# 名称申报入口（仅登录成功后使用；勿未登录深链）
NAME_APPLY_URL = os.environ.get(
    "YCT_NAME_APPLY_URL",
    "https://online.yct.sh.gov.cn/web-yct-master/"
    "?web-name-apply=%2Fweb-name-apply%2F%23%2Fname-declaration#/wujieViews/A1",
)
QR_SELECTOR = os.environ.get("YCT_QR_SELECTOR", "img, canvas, .qrcode, #qrcode")
# 本机可见浏览器模式下二维码直接展示给用户；默认不把二维码截图上传到案件目录。
CAPTURE_QR = os.environ.get("YCT_CAPTURE_QR", "false").lower() == "true"
LOGIN_TIMEOUT_SEC = int(os.environ.get("YCT_LOGIN_TIMEOUT_SEC", "300"))
HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
PLAYWRIGHT_CHANNEL = os.environ.get("PLAYWRIGHT_CHANNEL", "").strip() or None
# 标准名称申报流程禁止 CDP；仅调试时可显式打开（runner 会拒绝）
PLAYWRIGHT_USE_CDP = os.environ.get("PLAYWRIGHT_USE_CDP", "false").lower() == "true"
# 标准流程必须扫码；true 仅作遗留开关，portal.ensure_portal_logged_in 会拒绝
SKIP_QR_LOGIN = os.environ.get("SKIP_QR_LOGIN", "false").lower() == "true"
PLAYWRIGHT_CDP_URL = os.environ.get("PLAYWRIGHT_CDP_URL", "http://127.0.0.1:9222").strip()
