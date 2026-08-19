"""最短闭环桌面填报：字号区 → 下一步 → 设立关键字段 → 暂存。

前提：用户已在本机打开并登录上海企业登记页面（名称申报或可由此进入），
Chrome/Edge 窗口可见。本模块不打开登录页、不扫码、不关闭浏览器。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from apps.api import statuses, store
from apps.worker import config
from apps.worker.desktop.driver import DesktopDriver, find_browser_window, focus_window, require_deps


def _load_layout() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "layout.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _sleep_ms(ms: int) -> None:
    time.sleep(max(0, ms) / 1000.0)


def _payload_bits(payload: dict[str, Any]) -> dict[str, str]:
    name = payload.get("name") or {}
    reg = payload.get("registration") or {}
    addr = reg.get("address") or {}
    company = payload.get("company") or {}
    investors = payload.get("investors") or []
    inv0 = investors[0] if investors else {}
    lr = (payload.get("personnel") or {}).get("legalRepresentative") or {}

    district = (addr.get("district") or reg.get("district") or "").strip()
    detail = (addr.get("detail") or "").strip()
    if detail and not detail.startswith("上海"):
        detail_full = f"上海市{district}{detail}"
    else:
        detail_full = detail

    person = (lr.get("name") or inv0.get("name") or "").strip()
    mobile = (lr.get("mobile") or inv0.get("mobile") or "").strip()
    id_number = (lr.get("idNumber") or inv0.get("idNumber") or "").strip()
    capital = company.get("registeredCapital")
    capital_s = "" if capital is None else str(capital)

    return {
        "division": (name.get("adminDivision") or "上海").strip(),
        "trade_name": (name.get("tradeName") or "").strip(),
        "industry": (name.get("industry") or "").strip(),
        "org_form": (name.get("orgForm") or "有限公司").strip(),
        "preview": (
            name.get("fullNamePreview")
            or f"上海{name.get('tradeName', '')}{name.get('industry', '')}{name.get('orgForm', '有限公司')}"
        ).strip(),
        "person": person,
        "mobile": mobile,
        "id_number": id_number,
        "address": detail_full,
        "postal_code": (addr.get("postalCode") or "").strip(),
        "capital": capital_s,
    }


def _fill_name(driver: DesktopDriver, bits: dict[str, str], timings: dict[str, Any], case_id: str) -> dict[str, Any]:
    """按官网实际顺序：区划 → 字号 → 行业建议 → 组织形式弹层 → 确认 → 选名称 → 下一步。"""
    result = {
        "division": False,
        "trade_name": False,
        "industry": False,
        "org_form": False,
        "dialog_confirm": False,
        "name_picked": False,
        "next": False,
    }
    case_dir = store.case_dir(case_id)
    gap = timings.get("between_fields_ms", 500)
    drop = timings.get("after_dropdown_ms", 700)

    # 勿盲目 Esc，避免关掉已打开的下拉；仅尝试点掉残留「确定」
    driver.click_text_button(["btn_confirm.png"], fallback_point=None)
    _sleep_ms(200)
    driver.screenshot(case_dir, "desktop-name-before")

    # 1) 名称区划 = 上海
    if driver.click_point("division", "name_page"):
        _sleep_ms(drop)
        # 下拉点「上海」；若已是上海再点一次也无妨
        driver.click_point("division_option_shanghai", "name_page")
        result["division"] = True
        _sleep_ms(gap)

    # 2) 名称字号
    result["trade_name"] = driver.type_field("name_page", "trade_name", bits["trade_name"])
    _sleep_ms(timings.get("after_paste_ms", 400))
    _sleep_ms(gap)
    driver.screenshot(case_dir, "desktop-name-after-trade")

    # 3) 行业表述 + 点自动完成第一项（勿只用键盘，避免跳错控件）
    if driver.click_point("industry", "name_page"):
        _sleep_ms(200)
        driver.paste(bits["industry"])
        _sleep_ms(timings.get("after_suggest_ms", 900))
        driver.click_point("industry_suggest_first", "name_page")
        result["industry"] = True
        _sleep_ms(gap)
    driver.screenshot(case_dir, "desktop-name-after-industry")

    # 4) 组织形式：弹层内先选企业类型「有限公司」，再选组织形式「有限公司」
    if driver.click_point("org_form", "name_page"):
        _sleep_ms(timings.get("after_org_ms", 800))
        driver.click_point("org_type_limited", "name_page")
        _sleep_ms(400)
        driver.click_point("org_form_limited", "name_page")
        result["org_form"] = True
        _sleep_ms(timings.get("after_dialog_ms", 600))

    # 5) 「您选择申报的为有限公司名称…」→ 确定
    confirmed = driver.click_text_button(
        ["btn_confirm.png", "dialog_zone.png"],
        fallback_point=("name_page", "dialog_confirm"),
    )
    result["dialog_confirm"] = confirmed
    _sleep_ms(timings.get("after_dialog_ms", 600))
    driver.screenshot(case_dir, "desktop-name-after-org")

    # 6) 勾选完整企业名称（第一项，含「有限公司」）
    result["name_picked"] = driver.click_point("name_option_first", "name_page")
    _sleep_ms(gap)
    driver.screenshot(case_dir, "desktop-name-filled")

    # 7) 下一步
    result["next"] = driver.click_text_button(
        ["btn_next.png"],
        fallback_point=("name_page", "next_button"),
    )
    _sleep_ms(timings.get("after_next_ms", 2800))

    # 比对通过后的告知书/承诺书（若出现）
    for _ in range(2):
        driver.click_point("pledge_check", "name_page")
        _sleep_ms(250)
        if not driver.click_text_button(
            ["btn_next.png", "btn_confirm.png"],
            fallback_point=("name_page", "pledge_next"),
        ):
            break
        _sleep_ms(800)

    driver.screenshot(case_dir, "desktop-name-after-next")
    return result


def _fill_establishment(driver: DesktopDriver, bits: dict[str, str], timings: dict[str, Any], case_id: str) -> dict[str, Any]:
    result = {
        "agent_name": False,
        "mobile": False,
        "id_number": False,
        "address": False,
        "postal_code": False,
        "capital": False,
        "saved": False,
    }
    case_dir = store.case_dir(case_id)
    driver.dismiss_dialogs()
    # 温馨提示
    driver.click_text_button(["btn_confirm.png"], fallback_point=("name_page", "dialog_confirm"))
    _sleep_ms(500)
    driver.screenshot(case_dir, "desktop-establishment-before")

    gap = timings.get("between_fields_ms", 350)

    result["agent_name"] = driver.type_field("establishment_page", "agent_name", bits["person"])
    _sleep_ms(gap)
    result["mobile"] = driver.type_field("establishment_page", "mobile", bits["mobile"])
    _sleep_ms(gap)
    result["id_number"] = driver.type_field("establishment_page", "id_number", bits["id_number"])
    _sleep_ms(gap)

    if bits["address"]:
        result["address"] = driver.type_field("establishment_page", "address_detail", bits["address"])
        _sleep_ms(gap)
    if bits["postal_code"]:
        result["postal_code"] = driver.type_field("establishment_page", "postal_code", bits["postal_code"])
        _sleep_ms(gap)
    if bits["capital"]:
        result["capital"] = driver.type_field("establishment_page", "capital", bits["capital"])
        _sleep_ms(gap)

    # 法人姓名若与经办人不同页区域，再填一次兜底
    if bits["person"]:
        driver.type_field("establishment_page", "legal_name", bits["person"])
        _sleep_ms(gap)

    driver.screenshot(case_dir, "desktop-establishment-filled")

    # 暂存（绝不点最终提交）
    saved = driver.click_text_button(
        ["btn_save_draft.png", "btn_save_alt.png"],
        fallback_point=("establishment_page", "save_button"),
    )
    _sleep_ms(timings.get("after_save_ms", 2000))
    result["saved"] = saved
    driver.screenshot(case_dir, "desktop-after-save")
    return result


def run_desktop_rpa(case_id: str) -> None:
    """入口：跳过全部扫码/登录，直接桌面操控填报。"""
    if config.ALLOW_FINAL_SUBMIT:
        store.set_status(case_id, statuses.FAILED, reason="一期禁止 ALLOW_FINAL_SUBMIT=true")
        store.release_worker(case_id)
        return

    try:
        require_deps()
        layout = _load_layout()
        timings = layout.get("timings") or {}
        payload = store.get_case(case_id)["payload"]
        bits = _payload_bits(payload)
        if not bits["trade_name"]:
            store.set_status(case_id, statuses.NEED_HUMAN, reason="payload 缺少名称字号")
            return

        store.set_status(
            case_id,
            statuses.LOGGED_IN,
            customer_message="已跳过扫码，正在当前桌面窗口填报",
        )

        titles = layout.get("window_titles") or ["上海企业登记", "Chrome"]
        win = find_browser_window(titles)
        focus_window(win)
        _sleep_ms(timings.get("after_focus_ms", 500))

        driver = DesktopDriver(win, layout)
        driver.screenshot(store.case_dir(case_id), "desktop-start")

        store.set_status(case_id, statuses.NAME_CHECKING)
        name_result = _fill_name(driver, bits, timings, case_id)
        if not name_result.get("trade_name"):
            store.set_status(
                case_id,
                statuses.NEED_HUMAN,
                reason="桌面 RPA 未能定位/填写字号，请确认名称申报页在前台并校准 layout.yaml",
                result={"desktopName": name_result},
            )
            return

        store.set_status(
            case_id,
            statuses.FILLING_ESTABLISHMENT,
            result={
                "approvedName": bits["preview"],
                "desktopName": name_result,
            },
        )

        # 重新取窗口矩形（页面可能导航）
        win2 = find_browser_window(titles)
        focus_window(win2)
        driver = DesktopDriver(win2, layout)

        est_result = _fill_establishment(driver, bits, timings, case_id)
        missing = []
        if not est_result.get("agent_name"):
            missing.append("经办人姓名")
        if not est_result.get("id_number"):
            missing.append("证件号码")
        if not est_result.get("saved"):
            missing.append("暂存按钮")

        result = {
            "approvedName": bits["preview"],
            "desktopName": name_result,
            "desktopEstablishment": est_result,
            "missing": missing,
            "note": "法人证照上传为第二期；请人工核对住所/经营范围后本人提交",
        }

        if missing:
            store.set_status(
                case_id,
                statuses.NEED_HUMAN,
                reason="桌面填报未完整：" + "、".join(missing) + "。请查看 screenshots/desktop-*.png 并校准坐标",
                result=result,
                customer_message="自动填报需人工核对，请查看截图后继续",
            )
            return

        store.set_status(
            case_id,
            statuses.READY_FOR_USER_SUBMIT,
            result=result,
            customer_message=statuses.CUSTOMER_MESSAGES[statuses.READY_FOR_USER_SUBMIT],
        )
    except Exception as e:
        try:
            store.set_status(case_id, statuses.NEED_HUMAN, reason=f"桌面 RPA 异常: {e}")
        except Exception:
            pass
    finally:
        store.release_worker(case_id)
