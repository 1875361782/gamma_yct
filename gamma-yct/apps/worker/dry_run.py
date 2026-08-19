"""Simulate automation timeline for frontend integration."""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from apps.api import statuses, store

STEPS = [
    (statuses.AWAITING_QR_LOGIN, 1.0),
    (statuses.LOGGED_IN, 1.0),
    (statuses.NAME_CHECKING, 1.2),
    (statuses.FILLING_ESTABLISHMENT, 1.0),
    (statuses.CHOOSING_PROCESS, 0.8),
    (statuses.FILLING_ADDONS, 0.8),
    (statuses.READY_FOR_USER_SUBMIT, 0.5),
]


def _write_fake_qr(case_id: str) -> None:
    path = store.case_dir(case_id) / "qr.png"
    img = Image.new("RGB", (280, 280), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 260, 260], outline=(0, 0, 0), width=4)
    # simple pattern blocks
    for i in range(8):
        for j in range(8):
            if (i + j) % 2 == 0:
                draw.rectangle(
                    [40 + i * 25, 40 + j * 25, 60 + i * 25, 60 + j * 25],
                    fill=(0, 0, 0),
                )
    try:
        font = ImageFont.load_default()
        draw.text((70, 120), "DRY-RUN QR", fill=(80, 80, 80), font=font)
    except Exception:
        pass
    img.save(path)


def run_dry_run(case_id: str) -> None:
    try:
        payload = store.get_case(case_id)["payload"]
        approved = (
            payload.get("name", {}).get("fullNamePreview")
            or f"上海{payload.get('name', {}).get('tradeName', '')}"
            f"{payload.get('name', {}).get('industry', '')}"
            f"{payload.get('name', {}).get('orgForm', '有限公司')}"
        )
        for status, delay in STEPS:
            case = store.get_case(case_id)
            if case["status"] == statuses.CANCELLED:
                return
            if status == statuses.AWAITING_QR_LOGIN:
                _write_fake_qr(case_id)
            store.set_status(
                case_id,
                status,
                result={"approvedName": approved} if status == statuses.READY_FOR_USER_SUBMIT else None,
            )
            if status == statuses.READY_FOR_USER_SUBMIT:
                shot = store.case_dir(case_id) / "screenshots" / "dry-run-done.png"
                Image.new("RGB", (640, 360), color=(240, 248, 240)).save(shot)
            time.sleep(delay)
    except Exception as e:
        store.set_status(case_id, statuses.FAILED, reason=str(e))
    finally:
        store.release_worker(case_id)
