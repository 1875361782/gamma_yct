"""真实环境：门户强制扫码 + DOM 名称申报到「下一步」。

流程：
  打开 portal_yct → 关弹窗 → 开办 → 企业 → 扫码登录
  → 我已知晓上述内容 → 企业设立登记（不涉及前置审批事项）
  → 字号盛表玛 / 行业科技(点第一条) / 组织形式有限公司 → 下一步

用法（在 gamma-yct 根目录）:
  .\\.venv\\Scripts\\python.exe scripts\\run_name_flow.py --case GM202608062231EF
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 强制：Playwright DOM、扫码、无 CDP、无桌面 RPA
os.environ["AUTOMATION_MODE"] = "playwright"
os.environ["SKIP_QR_LOGIN"] = "false"
os.environ["PLAYWRIGHT_USE_CDP"] = "false"
os.environ["DESKTOP_RPA_FALLBACK"] = "false"
os.environ["ALLOW_FINAL_SUBMIT"] = "false"
os.environ["PLAYWRIGHT_HEADLESS"] = "false"
os.environ.setdefault("YCT_CAPTURE_QR", "false")
os.environ.setdefault("YCT_LOGIN_TIMEOUT_SEC", "300")
os.environ.setdefault("YCT_PORTAL_URL", "https://yct.sh.gov.cn/portal_yct/")
os.environ.setdefault("PLAYWRIGHT_CHANNEL", "chrome")


def main() -> int:
    parser = argparse.ArgumentParser(description="YCT 门户扫码 + 名称申报 DOM 流程")
    parser.add_argument("--case", default="GM202608062231EF", help="案件 ID")
    parser.add_argument("--no-wait", action="store_true", help="结束后不等待回车即退出（会关浏览器）")
    args = parser.parse_args()
    case_id = args.case.strip()

    from apps.api import statuses, store
    from apps.worker import config
    from apps.worker.playbooks.fill_pipeline import run_fill_pipeline
    from apps.worker.playbooks.portal import open_name_declaration_from_portal
    from apps.worker.playbooks.qr_login import run_qr_login
    from playwright.sync_api import sync_playwright

    case = store.get_case(case_id)
    # 重置为可跑
    store.set_status(
        case_id,
        statuses.READY_TO_SUBMIT,
        customer_message="准备启动门户扫码流程",
        reason="",
    )
    # 确保走名称申报 v1（到下一步为止）
    meta_path = store.case_dir(case_id) / "meta.json"
    import json

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["flow"] = "name_declaration_v1"
    meta["workerLocked"] = True
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    name = (case.get("payload") or {}).get("name") or {}
    print("=" * 60)
    print(f"case={case_id}")
    print(
        "流程: 门户扫码 → 核名({trade}/{ind}/有限责任公司) → 下一步\n"
        "      → 比对/告知承诺弹窗 → 基本信息(身份证/机关/住所/暂存/经营范围) → 下一步\n"
        "      → 出资情况(注册资本/股东/认缴) → 下一步\n"
        "      → 人员信息(董事/高管/联络员) → 受益所有人 → 下一步\n"
        "      → 企业开办(全程网办/智慧文书/暂不刻章) → 暂存".format(
            trade=name.get("tradeName") or "盛表玛",
            ind=name.get("industry") or "科技",
        )
    )
    print(f"扫码等待上限: {config.LOGIN_TIMEOUT_SEC}s（请用随申办扫浏览器二维码）")
    print("中断后自动修复并重试，直到完成或达到轮次上限。")
    print("=" * 60)

    MAX_ROUNDS = 4
    playwright = None
    browser = None
    exit_code = 1
    try:
        for round_i in range(1, MAX_ROUNDS + 1):
            print(f"----- 第 {round_i}/{MAX_ROUNDS} 轮 -----")
            if playwright is None:
                playwright = sync_playwright().start()
            launch_kwargs: dict = {"headless": False}
            if config.PLAYWRIGHT_CHANNEL:
                launch_kwargs["channel"] = config.PLAYWRIGHT_CHANNEL
            if browser is None:
                browser = playwright.chromium.launch(**launch_kwargs)
            try:
                page = run_qr_login(case_id, browser)
                page = open_name_declaration_from_portal(page, case_id)
                run_fill_pipeline(case_id, page)
            except Exception as exc:
                store.set_status(case_id, statuses.NEED_HUMAN, reason=str(exc))
                print(f"异常: {exc}")
                print(f"截图目录: {store.case_dir(case_id) / 'screenshots'}")
                try:
                    browser.close()
                except Exception:
                    pass
                browser = None
                if round_i < MAX_ROUNDS:
                    print("浏览器将重启并自动继续…")
                    time.sleep(2)
                    continue
                break

            fresh = store.get_case(case_id)
            print(f"最终状态: {fresh.get('status')}  {fresh.get('reason') or ''}")
            print(f"结果: {fresh.get('result') or {}}")
            print(f"截图目录: {store.case_dir(case_id) / 'screenshots'}")
            if fresh.get("status") in (statuses.READY_FOR_USER_SUBMIT, statuses.NAME_NEXT_REACHED):
                print("已完成到企业开办「暂存」（不代最终提交/签名）。")
                exit_code = 0
                break
            print("本轮未完成，请查看截图与 fill-*-result.json。")
            # 人员信息失败时不要整轮重扫码；浏览器仍在则只再跑人员（pipeline 内已重试 3 次）
            if round_i >= MAX_ROUNDS:
                break
            print("仅在浏览器被关闭时才重启整轮。本轮浏览器仍在，停止以免重复扫码。")
            break
    finally:
        store.release_worker(case_id)
        if not args.no_wait:
            try:
                input("浏览器保持打开，按回车关闭…")
            except EOFError:
                time.sleep(30)
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
