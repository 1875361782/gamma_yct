"""会话路由：sid ↔ RPASession，支持多 WebSocket 客户端同时监听。

每个 sid 复用同一个 RPASession；A 页（二维码获取页）与 B 页（扫码登录页）
可绑定到同一会话，服务端把 QR / 登录成功 / 截图 / 步骤 广播给所有在线客户端。
"""
from __future__ import annotations
import asyncio
import time
import uuid
from typing import Dict, Optional, Callable, Awaitable
from .rpa.browser import RPASession, Listener


class SessionManager:
    def __init__(self, ttl_seconds: int = 600):
        self._sessions: Dict[str, RPASession] = {}
        self._lock = asyncio.Lock()
        self.ttl_seconds = ttl_seconds

    async def get_or_create(
        self,
        sid: str,
        url: str,
        initial_listener: Optional[Listener] = None,
        headless: bool = True,
    ) -> RPASession:
        """获取或创建会话。如会话已存在，直接加入监听器；不存在则启动 RPA。"""
        async with self._lock:
            existing = self._sessions.get(sid)
            if existing is not None and not existing._stopped:
                existing.last_active = time.time()
                if initial_listener:
                    existing.add_listener(initial_listener)
                    await existing.replay_state_to(initial_listener)
                return existing
            session = RPASession(sid=sid, headless=headless)
            self._sessions[sid] = session
        # 启动在锁外，避免阻塞
        if initial_listener:
            session.add_listener(initial_listener)
        await session.start(url)
        return session

    async def add_listener(self, sid: str, listener: Listener) -> bool:
        """向已存在会话追加监听器。返回是否成功。"""
        async with self._lock:
            session = self._sessions.get(sid)
        if session is None or session._stopped:
            return False
        session.add_listener(listener)
        session.last_active = time.time()
        return True

    async def remove_listener(self, sid: str, lid: str) -> None:
        """移除指定监听器；如会话无监听器且运行超 TTL 则清理。"""
        async with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            return
        session.remove_listener(lid)
        # 无监听器时直接清理
        if not session._listeners:
            await self.remove(sid)

    async def remove(self, sid: str) -> None:
        async with self._lock:
            session = self._sessions.pop(sid, None)
        if session:
            await session.stop()

    async def get(self, sid: str) -> Optional[RPASession]:
        return self._sessions.get(sid)

    async def list_active(self) -> list[str]:
        return [sid for sid, s in self._sessions.items() if not s._stopped]

    async def cleanup_idle(self) -> int:
        """清理空闲 TTL 会话；返回清理数量。"""
        now = time.time()
        expired: list[str] = []
        async with self._lock:
            for sid, s in self._sessions.items():
                if s._stopped:
                    expired.append(sid)
                    continue
                # 无监听器 或 超过 TTL 都清理
                if not s._listeners or (now - s.last_active) > self.ttl_seconds:
                    expired.append(sid)
        for sid in expired:
            await self.remove(sid)
        return len(expired)
