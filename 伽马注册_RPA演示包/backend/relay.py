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


QUEUE_WAIT_MSG = "当前办理人数较多或服务器繁忙，请稍候"
QUEUE_READY_MSG = "可以开始办理了，请点击开始办理"


@dataclass
class RelayJob:
    sid: str
    case_id: str
    qr_b64: Optional[str] = None
    qr_kind: Optional[str] = None  # login | alipay | face
    sign_phase: Optional[str] = None  # alipay_qr | alipay_scanned | face_qr | done
    logged_in: bool = False
    started_at: float = field(default_factory=time.time)
    finished: bool = False
    ok: Optional[bool] = None
    error: Optional[str] = None
    clients: List[RelayClient] = field(default_factory=list)
    thread: Optional[threading.Thread] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class WaitEntry:
    sid: str
    case_id: str
    enqueued_at: float = field(default_factory=time.time)
    ready: bool = False


class RelayHub:
    def __init__(self) -> None:
        self._jobs: Dict[str, RelayJob] = {}
        self._wait_queue: List[WaitEntry] = []
        self._wait_lock = threading.Lock()
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def get(self, sid: str) -> Optional[RelayJob]:
        async with self._lock:
            return self._jobs.get(sid)

    async def list_jobs(self) -> List[RelayJob]:
        async with self._lock:
            return list(self._jobs.values())

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

    def push_qr(self, sid: str, qr_b64: str, kind: Optional[str] = None) -> None:
        # 同步写入，避免事件循环调度失败时轮询一直拿不到二维码
        job = self._jobs.get(sid)
        if job and qr_b64:
            job.qr_b64 = qr_b64
            if kind:
                job.qr_kind = kind

        async def _do():
            async with self._lock:
                j = self._jobs.get(sid)
                if j and qr_b64:
                    j.qr_b64 = qr_b64
                    if kind:
                        j.qr_kind = kind
            from .protocol import msg_qr_update, msg_step

            await self._broadcast(sid, "qr", msg_qr_update(qr_b64))
            await self._broadcast(
                sid, "step", msg_step("qr_captured", "二维码已同步到演示页")
            )

        self._schedule(_do())

    def clear_qr(self, sid: str) -> None:
        """进入加载态时清掉旧二维码，避免前端继续展示上一张码。"""
        job = self._jobs.get(sid)
        if job:
            job.qr_b64 = None
            job.qr_kind = None

        async def _do():
            async with self._lock:
                j = self._jobs.get(sid)
                if j:
                    j.qr_b64 = None
                    j.qr_kind = None

        self._schedule(_do())

    def push_sign_phase(self, sid: str, phase: str) -> None:
        job = self._jobs.get(sid)
        if job:
            job.sign_phase = phase

        async def _do():
            async with self._lock:
                j = self._jobs.get(sid)
                if j:
                    j.sign_phase = phase
            from .protocol import msg_step

            await self._broadcast(
                sid, "step", msg_step("sign_phase", f"签名阶段：{phase}")
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
        # 同步置 finished，便于立刻释放并发计数
        job = self._jobs.get(sid)
        if job:
            job.finished = True
            job.ok = ok
            job.error = error

        async def _do():
            async with self._lock:
                j = self._jobs.get(sid)
                if j:
                    j.finished = True
                    j.ok = ok
                    j.error = error

        self._schedule(_do())
        # 槽位释放后唤醒等待队列队首
        try:
            self.mark_wait_ready_when_slot_free()
        except Exception:
            logger.exception("mark_wait_ready failed after finish sid=%s", sid)

    def count_live_fill_jobs(self) -> int:
        """存活填报/签名线程数（不含仅排队未启动的 sid）。"""
        n = 0
        for job in list(self._jobs.values()):
            if job.thread and job.thread.is_alive() and not job.finished:
                n += 1
        return n

    def enqueue_wait(self, sid: str, case_id: str) -> dict[str, Any]:
        """满载入队；同 sid 重复入队则更新 caseId 并返回当前位置。"""
        with self._wait_lock:
            for i, ent in enumerate(self._wait_queue):
                if ent.sid == sid:
                    ent.case_id = case_id or ent.case_id
                    ent.ready = False
                    ent.enqueued_at = time.time()
                    return {
                        "position": i + 1,
                        "ready": False,
                        "message": QUEUE_WAIT_MSG,
                    }
            self._wait_queue.append(WaitEntry(sid=sid, case_id=case_id))
            return {
                "position": len(self._wait_queue),
                "ready": False,
                "message": QUEUE_WAIT_MSG,
            }

    def dequeue_wait(self, sid: str) -> None:
        with self._wait_lock:
            self._wait_queue = [e for e in self._wait_queue if e.sid != sid]

    def get_wait_state(self, sid: str) -> Optional[dict[str, Any]]:
        with self._wait_lock:
            for i, ent in enumerate(self._wait_queue):
                if ent.sid == sid:
                    return {
                        "queued": True,
                        "queuePosition": i + 1,
                        "queueReady": bool(ent.ready),
                        "caseId": ent.case_id,
                        "message": QUEUE_READY_MSG if ent.ready else QUEUE_WAIT_MSG,
                    }
        return None

    def consume_wait_ready(self, sid: str) -> Optional[WaitEntry]:
        """开始办理时取出已就绪条目（出队）。"""
        with self._wait_lock:
            for i, ent in enumerate(self._wait_queue):
                if ent.sid == sid:
                    if not ent.ready:
                        return None
                    self._wait_queue.pop(i)
                    return ent
        return None

    def mark_wait_ready_when_slot_free(self) -> None:
        """有空位时仅唤醒队首（FIFO），避免一哄而上再次打满。"""
        with self._wait_lock:
            if not self._wait_queue:
                return
            # 若队首已 ready，保持；否则标 ready
            head = self._wait_queue[0]
            if not head.ready:
                head.ready = True

    def start_fill_thread(
        self, sid: str, case_id: str, gamma_root: str, *, headless: bool = True
    ) -> bool:
        """启动填报线程（默认无头）；若同 sid 已在跑则返回 False。"""
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

                from apps.worker.run_fill_session import park_await_sign

                result = run_fill_session(
                    case_id,
                    headless=headless,
                    on_qr=on_qr,
                    on_login=on_login,
                    max_rounds=2,
                )
                # 先通知前端填报结束，再挂起同线程等待「前往签名」
                self.mark_finished(sid, bool(result.get("ok")), result.get("reason"))
                if not result.get("ok") and result.get("reason"):
                    self.push_error(sid, str(result.get("reason")))

                if result.get("ok") and result.get("keepBrowserOpen"):
                    sign_result = park_await_sign(case_id, timeout_sec=7200)
                    if sign_result is not None:
                        self.mark_finished(
                            sid,
                            bool(sign_result.get("ok")),
                            sign_result.get("reason") or sign_result.get("error"),
                        )
                        if sign_result.get("ok"):
                            self.push_sign_phase(sid, "done")
                        elif sign_result.get("reason") or sign_result.get("error"):
                            self.push_error(
                                sid,
                                str(
                                    sign_result.get("reason")
                                    or sign_result.get("error")
                                ),
                            )
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
        job.qr_kind = None
        job.sign_phase = None
        t.start()
        return True

    def start_sign_thread(
        self, sid: str, case_id: str, gamma_root: str
    ) -> bool:
        """优先唤醒挂起的填报线程跑签名；若无挂起则用同一 profile 重绑启动。"""
        import sys

        if gamma_root not in sys.path:
            sys.path.insert(0, gamma_root)
        from apps.worker.run_fill_session import (
            has_parked_sign_session,
            request_parked_sign,
            run_sign_session_rebind,
            wait_until_sign_park_ready,
        )

        job = self._jobs.get(sid)
        if job is None:
            job = RelayJob(sid=sid, case_id=case_id)
            self._jobs[sid] = job

        def on_qr(uri: str, kind: str = "alipay"):
            if uri:
                self.push_qr(sid, uri, kind=kind)

        def on_phase(phase: str):
            self.push_sign_phase(sid, phase)

        # 已在签名中（用户重复点继续签名）：直接复用，勿重启打断衔接
        phase_now = str(job.sign_phase or "")
        if (
            job.thread
            and job.thread.is_alive()
            and phase_now
            and phase_now not in ("", "done")
            and not job.finished
        ):
            self.clear_qr(sid)
            return True

        job.case_id = case_id
        job.finished = False
        job.ok = None
        job.error = None
        self.clear_qr(sid)
        job.sign_phase = "start"

        # 1) 同线程挂起唤醒（最佳）；填报刚结束可能有短空窗，先等就绪
        if job.thread and job.thread.is_alive() and not has_parked_sign_session(case_id):
            wait_until_sign_park_ready(case_id, timeout_sec=15.0)
        if has_parked_sign_session(case_id):
            if request_parked_sign(case_id, on_qr=on_qr, on_phase=on_phase):
                return True

        # 2) 回退：新线程 + 同一 Chrome profile 重开（挂起丢失时仍可签名）
        if job.thread and job.thread.is_alive():
            # 再给挂起一点时间（用户稍晚点「继续签名」仍应接上）
            if wait_until_sign_park_ready(case_id, timeout_sec=8.0):
                if request_parked_sign(case_id, on_qr=on_qr, on_phase=on_phase):
                    return True
            logger.warning(
                "sign fallback skipped: fill thread still alive without park sid=%s",
                sid,
            )
            return False

        def _worker():
            try:
                result = run_sign_session_rebind(
                    case_id,
                    on_qr=on_qr,
                    on_phase=on_phase,
                    headless=False,
                    close_browser=True,
                )
                self.mark_finished(
                    sid,
                    bool(result.get("ok")),
                    result.get("reason") or result.get("error"),
                )
                if result.get("ok"):
                    self.push_sign_phase(sid, "done")
                elif result.get("reason") or result.get("error"):
                    self.push_error(
                        sid, str(result.get("reason") or result.get("error"))
                    )
            except Exception as e:
                logger.exception("sign rebind failed sid=%s", sid)
                self.mark_finished(sid, False, str(e))
                self.push_error(sid, str(e))

        t = threading.Thread(target=_worker, name=f"sign-rebind-{sid}", daemon=True)
        job.thread = t
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
                        abort_fill_browser(case_id, kill_all=False)
                    except Exception:
                        logger.exception("abort browser failed case=%s", case_id)

                # 不阻塞 HTTP：关窗放后台线程，接口侧还有二次兜底
                threading.Thread(
                    target=_close_browser, name=f"abort-{sid}", daemon=True
                ).start()
                try:
                    abort_fill_browser(case_id, kill_all=False)
                except Exception:
                    pass
            except Exception as e:
                logger.exception("cancel fill failed sid=%s", sid)
                cancel_err = str(e)

        self.dequeue_wait(sid)
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
