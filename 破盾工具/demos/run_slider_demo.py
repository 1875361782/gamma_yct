"""
演示：打开一网通办登录页，触发滑块后自动识别拖拽。

用法:
  python demos/run_slider_demo.py
  python demos/run_slider_demo.py --username 手机号 --password 密码
  python demos/run_slider_demo.py --wait-only   # 不自动填表，手动操作到弹出滑块后自动解
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from browser import create_driver  # noqa: E402
from captcha_capture import find_first  # noqa: E402
from verify_flow import load_config, solve_slider  # noqa: E402


def _click_first(driver, selectors: list[str]) -> bool:
    el = find_first(driver, selectors, visible_only=True)
    if el is None:
        return False
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            return False


def _fill_first(driver, selectors: list[str], text: str) -> bool:
    el = find_first(driver, selectors, visible_only=True)
    if el is None:
        return False
    el.clear()
    el.send_keys(text)
    return True


def trigger_captcha(driver, cfg: dict, username: str, password: str) -> None:
    page = cfg.get("login_page", {})
    time.sleep(1.5)
    _click_first(driver, page.get("account_tab_xpaths", []))
    time.sleep(0.8)
    if username:
        _fill_first(driver, page.get("username_selectors", []), username)
    if password:
        _fill_first(driver, page.get("password_selectors", []), password)
    time.sleep(0.5)
    # 一网通办：#login-btn -> showSlidingValidation() -> imgVer 渲染 #scream/#puzzleShadow
    if not _click_first(driver, page.get("submit_selectors", [])):
        try:
            driver.execute_script(
                "if (typeof showSlidingValidation==='function') showSlidingValidation();"
            )
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="一网通办滑块验证演示")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "selectors.yaml"),
        help="配置文件路径",
    )
    parser.add_argument("--username", default="", help="手机号/身份证（用于触发验证码）")
    parser.add_argument("--password", default="", help="登录密码（用于触发验证码）")
    parser.add_argument(
        "--wait-only",
        action="store_true",
        help="不自动填表，等待你手动弹出滑块后自动求解",
    )
    parser.add_argument("--timeout", type=float, default=90, help="等待/求解总超时秒数")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    cfg = load_config(args.config)
    driver = create_driver(cfg.get("browser", {}))
    log_dir = ROOT / "logs"

    try:
        url = cfg["target_url"]
        logging.info("打开页面: %s", url)
        driver.get(url)

        if args.wait_only:
            logging.info("请在浏览器中手动操作，直到滑块验证弹出……")
        else:
            if not args.username or not args.password:
                logging.warning(
                    "未提供账号密码，将尝试仅点击登录；若未弹出滑块请改用 --wait-only 或传入账号"
                )
            trigger_captcha(driver, cfg, args.username, args.password)

        ok = solve_slider(driver, cfg, log_dir=log_dir, timeout=args.timeout)
        if ok:
            logging.info("滑块验证通过")
            return 0
        logging.error("滑块验证失败，请查看 logs/ 下截图与样本图，并微调 config 中 bias/选择器")
        return 1
    finally:
        # 保留窗口几秒便于观察
        time.sleep(3)
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
