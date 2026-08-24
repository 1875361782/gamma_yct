"""File-based case storage under data/cases/{caseId}/."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import statuses
from .payload import CasePayload

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "cases"

_lock = threading.Lock()
_worker_busy = False
_worker_case_id: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _case_dir(case_id: str) -> Path:
    return DATA_DIR / case_id


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def new_case_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    short = uuid.uuid4().hex[:6].upper()
    return f"GM{stamp}{short}"


def create_case(flow: str = "full_registration") -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    case_id = new_case_id()
    d = _case_dir(case_id)
    (d / "uploads").mkdir(parents=True)
    (d / "screenshots").mkdir(parents=True)
    payload = CasePayload().model_dump()
    if flow == "name_declaration_v1":
        payload["enterpriseType"] = "limited_company"
        payload["preApproval"] = False
        payload["name"].update(
            {
                "adminDivision": "上海市",
                "tradeName": "盛表玛",
                "industry": "科技",
                "orgForm": "有限公司",
            }
        )
    meta = {
        "caseId": case_id,
        "flow": flow,
        "status": statuses.DRAFT,
        "customerMessage": statuses.CUSTOMER_MESSAGES[statuses.DRAFT],
        "currentStep": 1,
        "result": {},
        "artifacts": {"qrImageUrl": None, "screenshots": []},
        "reason": None,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    _write_json(d / "meta.json", meta)
    _write_json(d / "payload.json", payload)
    return get_case(case_id)


def get_case(case_id: str) -> dict[str, Any]:
    d = _case_dir(case_id)
    if not (d / "meta.json").exists():
        raise FileNotFoundError(case_id)
    meta = _read_json(d / "meta.json")
    payload = _read_json(d / "payload.json")
    qr_path = d / "qr.png"
    if qr_path.exists():
        meta.setdefault("artifacts", {})
        meta["artifacts"]["qrImageUrl"] = f"/api/cases/{case_id}/qr.png"
    shots = sorted((d / "screenshots").glob("*.png"))
    meta.setdefault("artifacts", {})
    meta["artifacts"]["screenshots"] = [
        f"/api/cases/{case_id}/screenshots/{p.name}" for p in shots
    ]
    return {**meta, "payload": payload}


def update_payload(case_id: str, patch: dict[str, Any], wizard_step: Optional[int] = None) -> dict[str, Any]:
    from .payload import deep_merge

    d = _case_dir(case_id)
    meta = _read_json(d / "meta.json")
    if meta["status"] not in (statuses.DRAFT, statuses.READY_TO_SUBMIT, statuses.NAME_FAILED):
        raise ValueError(f"当前状态 {meta['status']} 不允许修改 payload")
    payload = _read_json(d / "payload.json")
    merged = deep_merge(payload, patch)
    # re-validate structure
    CasePayload.model_validate(merged)
    _write_json(d / "payload.json", merged)
    if wizard_step is not None:
        meta["currentStep"] = wizard_step
    if meta["status"] == statuses.READY_TO_SUBMIT:
        meta["status"] = statuses.DRAFT
        meta["customerMessage"] = statuses.CUSTOMER_MESSAGES[statuses.DRAFT]
    meta["updatedAt"] = _now()
    _write_json(d / "meta.json", meta)
    return get_case(case_id)


def set_status(
    case_id: str,
    status: str,
    *,
    customer_message: Optional[str] = None,
    result: Optional[dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    d = _case_dir(case_id)
    meta = _read_json(d / "meta.json")
    meta["status"] = status
    meta["customerMessage"] = customer_message or statuses.CUSTOMER_MESSAGES.get(
        status, status
    )
    if reason is not None:
        meta["reason"] = reason
        if status == statuses.FAILED and reason:
            meta["customerMessage"] = f"办理失败：{reason}"
    if result is not None:
        meta["result"] = {**meta.get("result", {}), **result}
    meta["updatedAt"] = _now()
    _write_json(d / "meta.json", meta)
    return get_case(case_id)


def save_upload(case_id: str, filename: str, content: bytes) -> str:
    d = _case_dir(case_id) / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]", "_", filename)
    file_id = f"{uuid.uuid4().hex[:10]}_{safe}"
    (d / file_id).write_bytes(content)
    return file_id


def upload_path(case_id: str, file_id: str) -> Path:
    path = _case_dir(case_id) / "uploads" / file_id
    if not path.exists():
        raise FileNotFoundError(file_id)
    return path


def resolve_id_card_images(
    case_id: str,
    payload: dict | None = None,
    *,
    person_key: str | None = None,
    person_name: str | None = None,
    fallback_front: Path | None = None,
    fallback_back: Path | None = None,
) -> tuple[Path | None, Path | None]:
    """按 personKey / 姓名匹配 attachments.idCards；缺省回退首张或桌面文件。"""
    front = back = None
    cards = ((payload or {}).get("attachments") or {}).get("idCards") or []
    card: dict | None = None
    if cards:
        if person_key:
            for c in cards:
                if isinstance(c, dict) and str(c.get("personKey") or "") == person_key:
                    card = c
                    break
        if card is None and person_name:
            want = str(person_name).strip()
            for c in cards:
                if isinstance(c, dict) and str(c.get("personName") or "").strip() == want:
                    card = c
                    break
        if card is None and isinstance(cards[0], dict):
            card = cards[0]
    if card:
        try:
            fid = str(card.get("frontFileId") or "").strip()
            if fid:
                front = upload_path(case_id, fid)
        except FileNotFoundError:
            pass
        try:
            bid = str(card.get("backFileId") or "").strip()
            if bid:
                back = upload_path(case_id, bid)
        except FileNotFoundError:
            pass
    if front is None and fallback_front is not None and fallback_front.exists():
        front = fallback_front
    if back is None and fallback_back is not None and fallback_back.exists():
        back = fallback_back
    return front, back


def case_dir(case_id: str) -> Path:
    return _case_dir(case_id)


def try_acquire_worker(case_id: str) -> bool:
    global _worker_busy, _worker_case_id
    with _lock:
        if _worker_busy:
            return False
        _worker_busy = True
        _worker_case_id = case_id
        return True


def release_worker(case_id: Optional[str] = None) -> None:
    global _worker_busy, _worker_case_id
    with _lock:
        if case_id is None or _worker_case_id == case_id:
            _worker_busy = False
            _worker_case_id = None


def worker_busy() -> bool:
    with _lock:
        return _worker_busy


def cancel_case(case_id: str) -> dict[str, Any]:
    meta = _read_json(_case_dir(case_id) / "meta.json")
    if meta["status"] in statuses.TERMINAL and meta["status"] != statuses.NEED_HUMAN:
        if meta["status"] == statuses.CANCELLED:
            return get_case(case_id)
    set_status(case_id, statuses.CANCELLED)
    release_worker(case_id)
    return get_case(case_id)
