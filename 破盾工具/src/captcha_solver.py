"""ddddocr 缺口识别与拖动距离换算。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ddddocr

from captcha_capture import CaptchaImages


@dataclass
class SolveResult:
    target_x_img: float
    drag_distance: float
    scale: float
    raw: dict[str, Any]


class SliderSolver:
    def __init__(self) -> None:
        # 仅滑块匹配，关闭 OCR/检测以加快加载
        self._ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

    def match_offset(
        self,
        images: CaptchaImages,
        *,
        simple_target: bool = True,
        bias: float = 0.0,
        slider_init_x: float = 0.0,
    ) -> SolveResult:
        raw = self._ocr.slide_match(
            images.piece_png,
            images.background_png,
            simple_target=simple_target,
        )
        # raw['target'] 一般为 [x1, y1, x2, y2] 或 (x, y)
        target = raw.get("target")
        if isinstance(target, (list, tuple)) and len(target) >= 1:
            target_x = float(target[0])
        else:
            raise RuntimeError(f"ddddocr 返回异常: {raw}")

        if images.bg_natural_width <= 0:
            raise RuntimeError("背景图自然宽度无效")

        scale = images.bg_display_width / images.bg_natural_width
        distance = (target_x - slider_init_x) * scale + bias

        # 限制在轨道可拖范围内
        max_drag = max(0.0, images.bg_display_width - images.piece_display_width)
        if max_drag > 0:
            distance = max(0.0, min(distance, max_drag))

        return SolveResult(
            target_x_img=target_x,
            drag_distance=distance,
            scale=scale,
            raw=raw,
        )
