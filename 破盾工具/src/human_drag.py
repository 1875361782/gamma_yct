"""拟人化拖拽轨迹。"""
from __future__ import annotations

import random
import time
from typing import Iterable

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def build_tracks(distance: float, steps: int | None = None) -> list[tuple[float, float]]:
    """生成相对位移序列 (dx, dy)，总和约等于 distance。"""
    if distance <= 0:
        return []

    steps = steps or max(25, min(45, int(abs(distance) / 3)))
    points: list[float] = []
    for i in range(1, steps + 1):
        t = i / steps
        points.append(_ease_out_cubic(t) * distance)

    tracks: list[tuple[float, float]] = []
    prev = 0.0
    for p in points:
        dx = p - prev
        prev = p
        dy = random.uniform(-1.2, 1.2)
        tracks.append((dx, dy))

    overshoot = random.uniform(2.0, 5.0)
    tracks.append((overshoot, random.uniform(-1.0, 1.0)))
    tracks.append((-overshoot + random.uniform(-1.0, 1.0), random.uniform(-1.0, 1.0)))
    return tracks


def human_drag(driver: WebDriver, handle: WebElement, distance: float) -> None:
    """
    拟人拖拽。一网通办 imgVer 监听的是 window.onmousemove/onmouseup，
    使用整段 ActionChains 保持按住状态更稳。
    """
    tracks = build_tracks(distance)
    actions = ActionChains(driver)
    actions.click_and_hold(handle)
    actions.pause(random.uniform(0.05, 0.15))
    for dx, dy in tracks:
        actions.move_by_offset(int(round(dx)), int(round(dy)))
        actions.pause(random.uniform(0.008, 0.022))
    actions.pause(random.uniform(0.08, 0.18))
    actions.release()
    actions.perform()


def human_drag_pointer(driver: WebDriver, handle: WebElement, distance: float) -> None:
    """备用：基于 PointerInput 的拖拽（部分环境下比 ActionChains 更贴近真实鼠标）。"""
    tracks = build_tracks(distance)
    rect = handle.rect
    start_x = rect["x"] + rect["width"] / 2
    start_y = rect["y"] + rect["height"] / 2

    pointer = PointerInput("mouse", "mouse")
    actions = ActionBuilder(driver, mouse=pointer)
    actions.pointer_action.move_to_location(int(start_x), int(start_y))
    actions.pointer_action.pointer_down()
    actions.pointer_action.pause(random.uniform(0.05, 0.15))
    cur_x, cur_y = start_x, start_y
    for dx, dy in tracks:
        cur_x += dx
        cur_y += dy
        actions.pointer_action.move_to_location(int(round(cur_x)), int(round(cur_y)))
        actions.pointer_action.pause(random.uniform(0.008, 0.022))
    actions.pointer_action.pause(random.uniform(0.08, 0.18))
    actions.pointer_action.pointer_up()
    actions.perform()


def replay_offsets(driver: WebDriver, handle: WebElement, offsets: Iterable[tuple[int, int]]) -> None:
    """调试用：按给定偏移重放。"""
    actions = ActionChains(driver)
    actions.click_and_hold(handle)
    for dx, dy in offsets:
        actions.move_by_offset(dx, dy)
        actions.pause(0.01)
    actions.release()
    actions.perform()
