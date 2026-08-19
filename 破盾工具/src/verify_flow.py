"""滑块验证编排：截图 → 识别 → 拖拽 → 判定 → 重试。"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.remote.webdriver import WebDriver

from browser import ensure_dir
from captcha_capture import (
    capture_captcha_images,
    find_first,
    switch_to_captcha_frame,
)
from captcha_solver import SliderSolver
from human_drag import human_drag, human_drag_pointer

logger = logging.getLogger(__name__)


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class SliderVerifyFlow:
    def __init__(self, driver: WebDriver, config: dict[str, Any], log_dir: str | Path = "logs"):
        self.driver = driver
        self.config = config
        self.captcha_cfg = config["captcha"]
        self.slider_cfg = config.get("slider", {})
        self.log_dir = ensure_dir(Path(log_dir))
        self.solver = SliderSolver()

    def _enter_context(self) -> None:
        switch_to_captcha_frame(self.driver, self.captcha_cfg.get("iframe_selectors", []))

    def _leave_context(self) -> None:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass

    def wait_for_panel(self, timeout: float = 20) -> bool:
        """等待验证弹层出现。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._enter_context()
            panel = find_first(
                self.driver,
                self.captcha_cfg.get("panel_selectors", []),
                visible_only=True,
            )
            handle = find_first(
                self.driver,
                self.captcha_cfg.get("handle_selectors", []),
                visible_only=True,
            )
            if panel or handle:
                return True
            self._leave_context()
            time.sleep(0.3)
        return False

    def _is_success(self) -> bool:
        # 一网通办成功后会写入 #tokenId
        try:
            token = self.driver.execute_script(
                "var el=document.getElementById('tokenId'); return el ? el.value : '';"
            )
            if token:
                return True
        except Exception:
            pass

        if find_first(self.driver, self.captcha_cfg.get("success_selectors", []), visible_only=True):
            return True

        # 成功提示：.ver-tips.slider-tips 内绿色文案
        tip_ok = self.driver.execute_script(
            """
            const tip = document.querySelector('.ver-tips.slider-tips');
            if (!tip) return false;
            const t = tip.innerText || '';
            return /成功|通过/.test(t) || (tip.querySelector('span[style*=\"42ca6b\"]') != null);
            """
        )
        if tip_ok:
            return True

        handle = find_first(
            self.driver, self.captcha_cfg.get("handle_selectors", []), visible_only=True
        )
        scream = find_first(self.driver, ["#scream"], visible_only=True)
        sliding = find_first(self.driver, ["#Sliding"], visible_only=True)
        # 滑块容器被隐藏且把手消失
        if handle is None and scream is None and (sliding is None or not sliding.is_displayed()):
            return True
        return False

    def _is_fail(self) -> bool:
        return (
            find_first(self.driver, self.captcha_cfg.get("fail_selectors", []), visible_only=True)
            is not None
        )

    def _save_debug(self, prefix: str, bg: bytes, piece: bytes) -> None:
        ts = int(time.time() * 1000)
        (self.log_dir / f"{prefix}_{ts}_bg.png").write_bytes(bg)
        (self.log_dir / f"{prefix}_{ts}_piece.png").write_bytes(piece)
        try:
            self.driver.save_screenshot(str(self.log_dir / f"{prefix}_{ts}_page.png"))
        except Exception:
            pass

    def _click_refresh(self, refresh_el) -> None:
        if refresh_el is None:
            return
        try:
            refresh_el.click()
            time.sleep(random.uniform(0.8, 1.4))
        except Exception as exc:
            logger.warning("点击刷新失败: %s", exc)

    def solve_once(self) -> bool:
        self._enter_context()
        images, handle, refresh = capture_captcha_images(self.driver, self.captcha_cfg)
        result = self.solver.match_offset(
            images,
            simple_target=bool(self.slider_cfg.get("simple_target", True)),
            bias=float(self.slider_cfg.get("bias", 0)),
        )
        logger.info(
            "识别 target_x=%.1f scale=%.4f drag=%.1f raw=%s",
            result.target_x_img,
            result.scale,
            result.drag_distance,
            result.raw,
        )
        self._save_debug("attempt", images.background_png, images.piece_png)

        # imgVer 依赖真实鼠标坐标；优先 Pointer 拖拽
        try:
            human_drag_pointer(self.driver, handle, result.drag_distance)
        except Exception:
            human_drag(self.driver, handle, result.drag_distance)
        time.sleep(float(self.slider_cfg.get("result_wait", 2.5)))

        if self._is_success():
            logger.info("滑块验证成功")
            return True

        if self._is_fail():
            logger.info("滑块验证失败（显式失败标记）")
            self._click_refresh(refresh)
            return False

        # 无明确成功/失败时：若把手仍在且几乎回到原点，判失败
        handle_now = find_first(
            self.driver, self.captcha_cfg.get("handle_selectors", []), visible_only=True
        )
        if handle_now is None:
            logger.info("滑块把手消失，按成功处理")
            return True

        logger.info("未检测到成功，准备重试")
        self._click_refresh(refresh)
        return False

    def solve(self, timeout: float = 60) -> bool:
        """带重试的完整求解。成功返回 True。"""
        max_retries = int(self.slider_cfg.get("max_retries", 5))
        if not self.wait_for_panel(timeout=timeout):
            raise TimeoutException("等待滑块验证弹层超时")

        for i in range(1, max_retries + 1):
            logger.info("第 %s/%s 次尝试", i, max_retries)
            try:
                if self.solve_once():
                    self._leave_context()
                    return True
            except Exception as exc:
                logger.exception("第 %s 次尝试异常: %s", i, exc)
                try:
                    self.driver.save_screenshot(
                        str(self.log_dir / f"error_{int(time.time())}.png")
                    )
                except Exception:
                    pass

            wait_s = random.uniform(
                float(self.slider_cfg.get("retry_wait_min", 0.5)),
                float(self.slider_cfg.get("retry_wait_max", 1.5)),
            )
            time.sleep(wait_s)
            self._enter_context()

        self._leave_context()
        return False


def solve_slider(
    driver: WebDriver,
    config: dict[str, Any] | str | Path,
    *,
    log_dir: str | Path = "logs",
    timeout: float = 60,
) -> bool:
    """对外 API：传入已打开到验证步骤的 driver。"""
    if not isinstance(config, dict):
        config = load_config(config)
    flow = SliderVerifyFlow(driver, config, log_dir=log_dir)
    return flow.solve(timeout=timeout)
