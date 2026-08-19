"""通过已打开的浏览器拉取关键 JS 并打印 imgVer 调用参数。"""
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
    driver = create_driver(cfg.get("browser", {}))
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    try:
        driver.get(cfg["target_url"])
        time.sleep(3)
        js_text = driver.execute_async_script(
            """
            const done = arguments[arguments.length - 1];
            fetch('/uc/login/js/login_new.js?v=20251224.001')
              .then(r => r.text())
              .then(t => done(t))
              .catch(e => done('ERR:' + e));
            """
        )
        (logs / "login_new.js").write_text(js_text or "", encoding="utf-8")
        for m in re.finditer(r"imgVer\s*\(\s*\{[\s\S]{0,400}?\}", js_text or ""):
            print("CALL:", m.group(0)[:500])
        for m in re.finditer(r"showSlidingValidation[\s\S]{0,800}?\}", js_text or ""):
            print("SHOW:", m.group(0)[:800])
            break
        print("len", len(js_text or ""))
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
