"""打开登录页，打印页面中与验证码相关的 script/class 线索。"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser import create_driver
from verify_flow import load_config


def main() -> None:
    cfg = load_config(ROOT / "config" / "selectors.yaml")
    driver = create_driver({**cfg.get("browser", {}), "headless": True})
    try:
        driver.get(cfg["target_url"])
        time.sleep(4)
        html = driver.page_source
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs" / "login_page.html").write_text(html, encoding="utf-8")
        scripts = re.findall(r'src=["\']([^"\']+)["\']', html, flags=re.I)
        interesting = [
            s
            for s in scripts
            if any(k in s.lower() for k in ("gee", "captcha", "verify", "slide", "yidun", "aliyun", "tianai"))
        ]
        classes = sorted(set(re.findall(r'class=["\']([^"\']+)["\']', html)))
        captcha_classes = [
            c
            for c in classes
            if any(k in c.lower() for k in ("gt_", "gee", "captcha", "slider", "verify", "nc-"))
        ]
        print("URL:", driver.current_url)
        print("TITLE:", driver.title)
        print("INTERESTING_SCRIPTS:")
        for s in interesting[:40]:
            print(" ", s)
        print("CAPTCHA_LIKE_CLASSES:")
        for c in captcha_classes[:60]:
            print(" ", c)
        # 页面文案
        for kw in ("滑块", "账号密码", "语音模式", "geetest", "验证"):
            print(f"HAS[{kw}]:", kw in html)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
