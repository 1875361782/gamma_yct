"""QR / 登录中继：无头 gamma-yct 填报 → WebSocket 推到演示页。

与 RPASession 分离：relay 模式不启演示包门户浏览器。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]


@dataclass
class RelayClient:
    lid: str
    send: SendFn
    subscribe: set


@dataclass
class RelayJob:
    sid: str
    case_id: str
    qr_b64: Optional[str] = None
    logged_in: bool = False
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    ok: Optional[bool] = None
    error: Optional[str] = None
    clients: List[RelayClient] = field(default_factory=list)
    thread: Optional[threading.Thread] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class RelayHub:
    def __init__(self) -> None:
        self._jobs: Dict[str, RelayJob] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def get(self, sid: str) -> Optional[RelayJob]:
        async with self._lock:
            return self._jobs.get(sid)

    async def ensure_job(self, sid: str, case_id: str) -> RelayJob:
        async with self._lock:
            job = self._jobs.get(sid)
            if job is None:
                job = RelayJob(sid=sid, case_id=case_id)
                self._jobs[sid] = job
            else:
                job.case_id = case_id or job.case_id
            return job

    async def add_client(
        self, sid: str, lid: str, send: SendFn, subscribe: set
    ) -> Optional[RelayJob]:
        async with self._lock:
            job = self._jobs.get(sid)
            if job is None:
                # 允许先连 WS 再 start-automation：先占位，避免断开
                job = RelayJob(sid=sid, case_id="")
                self._jobs[sid] = job
            job.clients.append(RelayClient(lid=lid, send=send, subscribe=subscribe))
        # 回放
        if job.qr_b64 and "qr" in subscribe:
            from .protocol import msg_qr_update

            await send(msg_qr_update(job.qr_b64))
        if job.logged_in and "login" in subscribe:
            from .protocol import msg_login_success

            await send(msg_login_success(sid))
        return job

    async def remove_client(self, sid: str, lid: str) -> None:
        async with self._lock:
            job = self._jobs.get(sid)
            if not job:
                return
            job.clients = [c for c in job.clients if c.lid != lid]

    async def _broadcast(self, sid: str, event: str, text: str) -> None:
        async with self._lock:
            job = self._jobs.get(sid)
            clients = list(job.clients) if job else []
        for c in clients:
            if event not in c.subscribe:
                continue
            try:
                await c.send(text)
            except Exception:
                pass

    def _schedule(self, coro) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def push_qr(self, sid: str, qr_b64: str) -> None:
        # 同步写入，避免事件循环调度失败时轮询一直拿不到二维码
        job = self._jobs.get(sid)
        if job and qr_b64:
            job.qr_b64 = qr_b64

        async def _do():
            async with self._lock:
                j = self._jobs.get(sid)
                if j and qr_b64:
                    j.qr_b64 = qr_b64
            from .protocol import msg_qr_update, msg_step

            await self._broadcast(sid, "qr", msg_qr_update(qr_b64))
            await self._broadcast(
                sid, "step", msg_step("qr_captured", "二维码已同步到演示页")
            )

        self._schedule(_do())

    def push_login(self, sid: str, user: str = "法人") -> None:
        # 同步置位：演示页轮询依赖 logged_in，不能只靠异步广播
        job = self._jobs.get(sid)
        if job:
            job.logged_in = True

        async def _do():
            async with self._lock:
                j = self._jobs.get(sid)
                if j:
                    j.logged_in = True
            from .protocol import msg_login_success, msg_step

            await self._broadcast(sid, "step", msg_step("login_success", "扫码成功"))
            await self._broadcast(sid, "login", msg_login_success(sid, user))

        self._schedule(_do())

    def push_error(self, sid: str, message: str) -> None:
        async def _do():
            from .protocol import msg_error

            await self._broadcast(sid, "error", msg_error(message))

        self._schedule(_do())

    def mark_finished(self, sid: str, ok: bool, error: Optional[str] = None) -> None:
        async def _do():
            async with self._lock:
                job = self._jobs.get(sid)
                if job:
                    job.finished = True
                    job.ok = ok
                    job.error = error

        self._schedule(_do())

    def start_fill_thread(
        self, sid: str, case_id: str, gamma_root: str, *, headless: bool = False
    ) -> bool:
        """启动填报线程（默认可见 Chrome 窗口）；若同 sid 已在跑则返回 False。"""
        job = self._jobs.get(sid)
        if job is None:
            job = RelayJob(sid=sid, case_id=case_id)
            self._jobs[sid] = job
        if job.thread and job.thread.is_alive():
            return False

        def _worker():
            import sys

            if gamma_root not in sys.path:
                sys.path.insert(0, gamma_root)
            try:
                from apps.worker.run_fill_session import run_fill_session

                def on_qr(uri: str):
                    if uri:
                        self.push_qr(sid, uri)

                def on_login():
                    self.push_login(sid)

                result = run_fill_session(
                    case_id,
                    headless=headless,
                    on_qr=on_qr,
                    on_login=on_login,
                    max_rounds=2,
                )
                self.mark_finished(sid, bool(result.get("ok")), result.get("reason"))
                if not result.get("ok") and result.get("reason"):
                    self.push_error(sid, str(result.get("reason")))
            except Exception as e:
                logger.exception("fill session failed sid=%s", sid)
                self.mark_finished(sid, False, str(e))
                self.push_error(sid, str(e))

        t = threading.Thread(target=_worker, name=f"fill-{sid}", daemon=True)
        job.thread = t
        job.case_id = case_id
        job.finished = False
        job.ok = None
        job.error = None
        job.logged_in = False
        t.start()
        return True

    def cancel_job(self, sid: str) -> dict:
        """取消填报：置案件 CANCELLED、异步关闭浏览器、标记 job 结束。"""
        job = self._jobs.get(sid)
        if job is None:
            return {"ok": False, "error": "unknown sid"}
        case_id = (job.case_id or "").strip()
        cancel_err = None
        if case_id:
            try:
                import sys
                from pathlib import Path

                # relay.py → backend → 演示包 → 仓库根
                gamma_root = str(Path(__file__).resolve().parents[2] / "gamma-yct")
                if gamma_root not in sys.path:
                    sys.path.insert(0, gamma_root)
                from apps.api import store as gamma_store
                from apps.worker.run_fill_session import abort_fill_browser

                gamma_store.request_cancel(case_id)

                def _close_browser() -> None:
                    try:
                        abort_fill_browser(case_id)
                    except Exception:
                        logger.exception("abort browser failed case=%s", case_id)

                # 不阻塞 HTTP：关窗放后台线程，接口侧还有二次兜底
                threading.Thread(
                    target=_close_browser, name=f"abort-{sid}", daemon=True
                ).start()
                try:
                    abort_fill_browser(case_id)
                except Exception:
                    pass
            except Exception as e:
                logger.exception("cancel fill failed sid=%s", sid)
                cancel_err = str(e)

        self.mark_finished(sid, False, "用户取消")
        try:
            self.push_error(sid, "用户取消填报")
        except Exception:
            pass
        out = {
            "ok": True,
            "sid": sid,
            "caseId": case_id,
            "browserClosed": "async",
            "message": "已取消填报",
        }
        if cancel_err:
            out["warn"] = cancel_err
        return out


relay_hub = RelayHub()
