"""一次性探测登录页 HTML 中的验证码相关线索。"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser import create_driver
from verify_flow import load_config


def main() -> int:
    cfg = load_config(ROOT / "config" / "selectors.yaml")
    browser_cfg = dict(cfg.get("browser", {}))
    browser_cfg["headless"] = True
    driver = create_driver(browser_cfg)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    try:
        driver.get(cfg["target_url"])
        time.sleep(5)
        html = driver.page_source
        (logs / "login_page.html").write_text(html, encoding="utf-8")
        keys = sorted(
            set(
                re.findall(
                    r"(geetest|gt_|yidun|aliyun|captcha|slider|shumei|tencent|tcaptcha)[\w./-]*",
                    html,
                    re.I,
                )
            )
        )
        print("KEYS:", keys[:100])
        for s in re.findall(r'src=["\']([^"\']+)["\']', html):
            if re.search(r"captcha|geetest|gt|verify|slide|yidun", s, re.I):
                print("SCRIPT:", s)
        print("title:", driver.title)
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
