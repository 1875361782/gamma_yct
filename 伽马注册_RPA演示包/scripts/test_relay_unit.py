"""干跑：中继会话 + 假填报线程推送 QR/登录（不访问政务网）。"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    from backend.relay import RelayHub
    from backend.protocol import msg_qr_update, msg_login_success

    hub = RelayHub()
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)

    received: list[dict] = []

    async def send(text: str):
        received.append(json.loads(text))

    sid = "sid-test-relay"
    await hub.ensure_job(sid, "GM202608062231EF")
    await hub.add_client(sid, "lid-1", send, {"qr", "login", "step", "error"})

    # 模拟无头线程推送
    hub.push_qr(sid, "data:image/png;base64,AAAA")
    await asyncio.sleep(0.2)
    hub.push_login(sid)
    await asyncio.sleep(0.2)

    types = [m.get("type") for m in received]
    assert "qr_update" in types, types
    assert "login_success" in types, types
    job = await hub.get(sid)
    assert job and job.qr_b64 and job.logged_in
    print("relay_unit_ok", types)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
