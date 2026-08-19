"""FastAPI entry: uvicorn apps.api.main:app --reload --app-dir ."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ensure project root on path when launched as module
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api import statuses, store
from apps.api.payload import validate_name_declaration, validate_payload
from apps.worker.runner import run_case

app = FastAPI(title="gamma-yct API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PayloadPatch(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    wizardStep: Optional[int] = None


class CreateCaseRequest(BaseModel):
    flow: str = "full_registration"


def _validate_case(case: dict[str, Any]) -> list[str]:
    if case.get("flow") == "name_declaration_v1":
        return validate_name_declaration(case["payload"])
    return validate_payload(case["payload"])


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "workerBusy": store.worker_busy(),
        "automationMode": __import__("os").environ.get("AUTOMATION_MODE", "dry_run"),
    }


@app.post("/api/cases")
def create_case(body: CreateCaseRequest | None = None) -> dict[str, Any]:
    flow = (body.flow if body else "full_registration").strip()
    if flow not in {"full_registration", "name_declaration_v1"}:
        raise HTTPException(400, "unsupported flow")
    return store.create_case(flow=flow)


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    try:
        return store.get_case(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "case not found")


@app.patch("/api/cases/{case_id}/payload")
def patch_payload(case_id: str, body: PayloadPatch) -> dict[str, Any]:
    try:
        return store.update_payload(case_id, body.payload, body.wizardStep)
    except FileNotFoundError:
        raise HTTPException(404, "case not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"invalid payload: {e}")


@app.post("/api/cases/{case_id}/validate")
def validate_case(case_id: str) -> dict[str, Any]:
    try:
        case = store.get_case(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "case not found")
    errors = _validate_case(case)
    if errors:
        return {"ok": False, "errors": errors, "case": case}
    store.set_status(case_id, statuses.READY_TO_SUBMIT)
    return {"ok": True, "errors": [], "case": store.get_case(case_id)}


def _start_worker(case_id: str) -> None:
    run_case(case_id)


@app.post("/api/cases/{case_id}/start")
def start_case(case_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    try:
        case = store.get_case(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "case not found")
    if case["status"] not in (
        statuses.READY_TO_SUBMIT,
        statuses.NAME_FAILED,
        statuses.NEED_HUMAN,
        statuses.HUMAN_CONFIRMATION_REQUIRED,
        statuses.CANCELLED,
    ):
        raise HTTPException(400, f"状态 {case['status']} 不可 start，请先 validate")
    # re-validate
    errors = _validate_case(case)
    if errors:
        raise HTTPException(400, {"message": "校验未通过", "errors": errors})
    if not store.try_acquire_worker(case_id):
        raise HTTPException(409, "worker 忙碌，请稍后重试")
    from apps.worker import config as worker_config

    mode = (worker_config.AUTOMATION_MODE or "").strip().lower()
    if mode == "desktop_rpa":
        store.set_status(
            case_id,
            statuses.LOGGED_IN,
            customer_message="已跳过扫码，将在当前桌面已登录窗口模拟操作填报",
        )
    elif mode in ("playwright", "auto") and worker_config.SKIP_QR_LOGIN:
        store.set_status(
            case_id,
            statuses.LOGGED_IN,
            customer_message="将打开门户确认已登录会话，再进入名称申报填报（不弹码）",
        )
    elif mode in ("playwright", "auto"):
        store.set_status(
            case_id,
            statuses.AWAITING_QR_LOGIN,
            customer_message="请先在门户扫码登录，登录成功后再自动填报",
        )
    elif worker_config.SKIP_QR_LOGIN:
        store.set_status(
            case_id,
            statuses.LOGGED_IN,
            customer_message="跳过扫码，将接入本机已登录浏览器填报",
        )
    else:
        store.set_status(case_id, statuses.AWAITING_QR_LOGIN)
    background_tasks.add_task(_start_worker, case_id)
    return store.get_case(case_id)


@app.get("/api/cases/{case_id}/qr.png")
def get_qr(case_id: str) -> FileResponse:
    path = store.case_dir(case_id) / "qr.png"
    if not path.exists():
        raise HTTPException(404, "qr not ready")
    return FileResponse(path, media_type="image/png")


@app.get("/api/cases/{case_id}/screenshots/{name}")
def get_screenshot(case_id: str, name: str) -> FileResponse:
    path = store.case_dir(case_id) / "screenshots" / name
    if not path.exists():
        raise HTTPException(404, "screenshot not found")
    return FileResponse(path, media_type="image/png")


@app.post("/api/cases/{case_id}/files")
async def upload_file(case_id: str, file: UploadFile = File(...)) -> dict[str, str]:
    try:
        store.get_case(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "case not found")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 5MB")
    file_id = store.save_upload(case_id, file.filename or "upload.bin", content)
    return {"fileId": file_id}


@app.post("/api/cases/{case_id}/cancel")
def cancel_case(case_id: str) -> dict[str, Any]:
    try:
        return store.cancel_case(case_id)
    except FileNotFoundError:
        raise HTTPException(404, "case not found")
