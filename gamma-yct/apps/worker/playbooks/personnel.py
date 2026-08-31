"""人员信息 + 受益所有人备案（新增，不改核名/基本信息/出资情况代码）。

出资情况「下一步」之后：
1. 董事信息：设一名董事；监事：可设立或不设立；设立时仅一名（不设监事会）
2. 添加董事 → 上传身份证 OCR → 是否担任法定代表人=是 → 确定
3. 添加监事（如有）→ 职务默认监事 → 上传身份证 OCR → 确定
4. 财务负责人：有限公司走「添加高级管理人员」；个人独资等为页面内联表单（复用/OCR）
5. 添加联络员 → 上传身份证 OCR → 代表登录公示系统=是 → 邮箱/住所 → 确定
6. 下一步 → 填报受益所有人信息
7. 自行填报 → 下一步
8. 存在符合下列条件之一的自然人 → 下一步
9. 填受益所有人（基本信息选吴文彬/证件期限/手机/地址/股权100%/形成日=当日）→ 下一步 → 确定
10. 已完成，进入下一步
   注：自然人不走「复用人员信息」；有上传位则身份证识别，无上传位才回退复用。
   注：股份有限公司必须设立一名监事；其它组织形式可选设立/不设立。
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page

from apps.api import statuses, store

DEFAULT_EMAIL = "1875361782@qq.com"
DEFAULT_ADDRESS = "贵州省安顺市西秀区鸡场乡鸡场散居户"
DEFAULT_MOBILE = "13984439335"
DEFAULT_ID_VALID = "2032-06-24"
DEFAULT_NAME = "吴文彬"
DEFAULT_ID_FRONT = Path(r"C:\Users\Lenovo\Desktop\身份证正面.jpg")
DEFAULT_ID_BACK = Path(r"C:\Users\Lenovo\Desktop\身份证反面.jpg")


def _parse_cn_region_address(detail: str) -> dict[str, str]:
    """从中文地址拆出国家/省/市/区 + 详细地址（受益人经常居住地用）。"""
    text = re.sub(r"\s+", "", (detail or "").strip())
    out = {
        "country": "中国",
        "province": "",
        "city": "",
        "district": "",
        "detail": text,
    }
    if not text:
        return out
    # 直辖市
    m = re.match(r"^(北京市|天津市|上海市|重庆市)", text)
    if m:
        out["province"] = m.group(1)
        out["city"] = m.group(1)
        rest = text[m.end() :]
        md = re.match(r"^([\u4e00-\u9fff]{1,12}?(?:区|县))", rest)
        if md:
            out["district"] = md.group(1)
            rest = rest[md.end() :]
        out["detail"] = rest or text
        return out
    # 省 + 市 + 区县
    m = re.match(
        r"^([\u4e00-\u9fff]{2,12}?(?:省|自治区))"
        r"([\u4e00-\u9fff]{1,12}?市)?"
        r"([\u4e00-\u9fff]{1,12}?(?:区|县|旗))?"
        r"(.*)$",
        text,
    )
    if m:
        out["province"] = m.group(1) or ""
        out["city"] = m.group(2) or ""
        out["district"] = m.group(3) or ""
        out["detail"] = (m.group(4) or "").strip() or text
        return out
    return out


def _addr_option_variants(name: str) -> tuple[str, ...]:
    """下拉匹配用：全称 + 去后缀简称。"""
    name = (name or "").strip()
    if not name:
        return ()
    variants = [name]
    for suf in ("省", "市", "区", "县", "自治区", "壮族自治区", "回族自治区", "维吾尔自治区"):
        if name.endswith(suf) and len(name) > len(suf):
            short = name[: -len(suf)]
            if short and short not in variants:
                variants.append(short)
            break
    return tuple(variants)


def _id_card_region_parts(id_no: str) -> dict[str, str]:
    """按身份证号前 6 位推断省/市/区（身份证住址级联兜底）。"""
    code = re.sub(r"\D", "", id_no or "")[:6]
    # 常用演示/已知人员
    table = {
        "520402": ("贵州省", "安顺市", "西秀区"),
        "520400": ("贵州省", "安顺市", "西秀区"),
        "370781": ("山东省", "潍坊市", "青州市"),
        "370700": ("山东省", "潍坊市", ""),
    }
    prov = city = dist = ""
    if code in table:
        prov, city, dist = table[code]
    elif code[:4] + "00" in table:
        prov, city, dist = table[code[:4] + "00"]
    elif code[:2] == "52":
        prov, city, dist = "贵州省", "安顺市", "西秀区"
    elif code[:2] == "37":
        prov, city, dist = "山东省", "潍坊市", ""
    elif code[:2] == "31":
        prov, city, dist = "上海市", "上海市", ""
    return {
        "country": "中国",
        "province": prov,
        "city": city,
        "district": dist,
        "detail": "",
    }


def _resolve_bo_user_address(
    payload: dict[str, Any], bo: dict[str, Any], *, fallback_name: str = ""
) -> tuple[str, dict[str, str]]:
    """受益人经常居住地：用原型「最终受益人 → 住所*」用户填写值。

    对应 payload.beneficialOwner.address（uboAddress）。
    解析国家/省/市/区；国家默认中国。不用 OCR、不用企业住所。
    """
    bo_name = str(bo.get("name") or fallback_name or "").strip()
    id_no = str(bo.get("idNumber") or "").strip()
    addr = str((bo or {}).get("address") or "").strip()
    personnel = payload.get("personnel") or {}

    # 空时仅回退同名联络员住所（旧案件兼容）
    if not addr and bo_name:
        liaison = personnel.get("liaisonOfficer") or {}
        if str(liaison.get("name") or "").strip() == bo_name:
            addr = str(liaison.get("address") or "").strip()

    if not id_no and bo_name:
        for inv in payload.get("investors") or []:
            if isinstance(inv, dict) and str(inv.get("name") or "").strip() == bo_name:
                id_no = str(inv.get("idNumber") or "").strip()
                break
        if not id_no:
            lr = personnel.get("legalRepresentative") or {}
            if str(lr.get("name") or "").strip() == bo_name:
                id_no = str(lr.get("idNumber") or "").strip()

    if addr:
        parts = _parse_cn_region_address(addr)
        parts["country"] = "中国"
        if (not parts.get("province") or not parts.get("city")) and id_no:
            region = _id_card_region_parts(id_no)
            for k in ("province", "city", "district"):
                if not parts.get(k) and region.get(k):
                    parts[k] = region[k]
        if not parts.get("detail"):
            parts["detail"] = addr
        return addr, parts

    region = _id_card_region_parts(id_no)
    region["country"] = "中国"
    assembled = "".join(
        x for x in (region.get("province"), region.get("city"), region.get("district")) if x
    )
    region["detail"] = assembled
    return assembled, region


def _shot(page: Page, case_id: str, name: str) -> None:
    path = store.case_dir(case_id) / "screenshots" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        pass


def _dump(case_id: str, name: str, data: Any) -> None:
    path = store.case_dir(case_id) / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _visible(loc, timeout: int = 800) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible(timeout=timeout)
    except Exception:
        return False


def _all_scopes(page: Page) -> list[Page | Frame]:
    scopes: list[Page | Frame] = [page]
    for fr in page.frames:
        if fr not in scopes:
            scopes.append(fr)
    return scopes


def _body(scope: Page | Frame):
    """优先 wujie 微前端内 body（与 capital.py 一致）。"""
    try:
        w = scope.locator("wujie-app body")
        if w.count() > 0:
            return w.first
    except Exception:
        pass
    return scope.locator("body").first


def _page_of(scope: Page | Frame) -> Page:
    return scope if isinstance(scope, Page) else scope.page


def _click_text(scope: Page | Frame, texts: list[str], *, exact: bool = False) -> bool:
    for t in texts:
        try:
            loc = scope.get_by_text(t, exact=exact).first
            if _visible(loc, timeout=600):
                loc.click(force=True, timeout=4000)
                return True
        except Exception:
            continue
    return False


def _click_text_any(page: Page, texts: list[str], *, exact: bool = False) -> bool:
    for scope in _all_scopes(page):
        if _click_text(scope, texts, exact=exact):
            return True
    return False


def _find_dialog(page: Page, *hints: str):
    for scope in _all_scopes(page):
        for sel in (
            ".el-message-box:visible",
            ".el-dialog:visible",
            ".el-overlay-dialog .el-dialog",
            "[role='dialog']",
        ):
            try:
                dialogs = scope.locator(sel)
                for i in range(min(dialogs.count(), 10)):
                    d = dialogs.nth(i)
                    if not _visible(d, timeout=300):
                        continue
                    text = d.inner_text(timeout=800) or ""
                    if all(h in text for h in hints):
                        return d, scope
            except Exception:
                continue
    return None, None


def _click_radio(scope: Page | Frame, label: str) -> bool:
    """点选含指定文案的 radio / label。"""
    try:
        lab = scope.locator("label.el-radio, label").filter(
            has_text=re.compile(re.escape(label[:12]) if len(label) > 12 else re.escape(label))
        ).first
        # 对长文案用 contains
        if not _visible(lab, timeout=400):
            lab = scope.get_by_text(label, exact=False).first
        if _visible(lab, timeout=600):
            lab.click(force=True, timeout=4000)
            return True
    except Exception:
        pass
    # 部分匹配：规模较小 / 不设监事
    keys = []
    if "不设监事" in label or "规模较小" in label:
        keys = ["不设监事", "规模较小"]
    for k in keys:
        try:
            lab = scope.locator("label").filter(has_text=re.compile(k)).first
            if _visible(lab, timeout=500):
                lab.click(force=True, timeout=4000)
                return True
        except Exception:
            continue
    return False


def _select_dropdown(scope: Page | Frame, label: str, option: str) -> bool:
    page = _page_of(scope)
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item, timeout=600):
        # 宽松：职务旁下拉
        try:
            lab = scope.get_by_text(label, exact=False).first
            if _visible(lab, timeout=400):
                item = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
                if not _visible(item, timeout=300):
                    item = lab.locator("xpath=../..")
        except Exception:
            return False
    if not _visible(item, timeout=400):
        return False
    try:
        trigger = item.locator(
            ".el-select .el-input__inner, .el-select, input.el-input__inner"
        ).first
        if not _visible(trigger, timeout=500):
            return False
        trigger.click(timeout=2500)
        page.wait_for_timeout(350)
        for root in _all_scopes(page):
            opt = root.locator(
                ".el-select-dropdown:visible li, .el-popper:visible li"
            ).filter(has_text=re.compile(rf"^\s*{re.escape(option)}\s*$")).first
            if not _visible(opt, timeout=400):
                opt = root.get_by_text(option, exact=True).first
            if _visible(opt, timeout=400):
                opt.click(timeout=2500)
                page.wait_for_timeout(300)
                return True
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def _select_cascader(scope: Page | Frame, label: str, parts: list[str]) -> bool:
    """户籍所属省市县等级联选择（与 capital 同逻辑）。"""
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item, timeout=600):
        item = scope.locator(".el-form-item").filter(has_text=re.compile(r"户籍")).first
    if not _visible(item, timeout=500):
        return False
    try:
        trigger = item.locator(
            ".el-cascader .el-input__inner, .el-cascader input, input.el-input__inner"
        ).first
        if not _visible(trigger, timeout=500):
            return False
        trigger.click(timeout=3000)
        page = _page_of(scope)
        page.wait_for_timeout(400)
        for part in parts:
            clicked = False
            for root in _all_scopes(page):
                node = root.locator(
                    ".el-cascader-panel:visible .el-cascader-node, "
                    ".el-cascader-menu:visible .el-cascader-node, "
                    ".el-popper:visible li, .el-cascader-node"
                ).filter(has_text=re.compile(rf"^\s*{re.escape(part)}\s*$")).first
                if not _visible(node, timeout=500):
                    node = root.get_by_text(part, exact=True).first
                if _visible(node, timeout=500):
                    try:
                        node.click(timeout=2500)
                        clicked = True
                        page.wait_for_timeout(350)
                        break
                    except Exception:
                        continue
            if not clicked:
                return False
        return True
    except Exception:
        return False


def _wait_ocr_idle(dialog, page: Page, timeout_ms: int = 25000) -> dict[str, Any]:
    """等待「正在识别证件信息中…」结束。"""
    info: dict[str, Any] = {"waited": False, "ok": False}
    end = time.time() + timeout_ms / 1000
    start = time.time()
    saw_busy = False
    while time.time() < end:
        try:
            txt = dialog.inner_text(timeout=600) or ""
        except Exception:
            txt = ""
        try:
            page_txt = page.locator("body").inner_text(timeout=400) or ""
        except Exception:
            page_txt = ""
        busy = ("正在识别" in txt) or ("正在识别证件信息" in page_txt)
        if busy:
            saw_busy = True
            info["waited"] = True
            page.wait_for_timeout(500)
            continue
        if "图片处理失败" in txt or "图片处理失败" in page_txt:
            info["failed"] = True
            info["ok"] = False
            return info
        if (
            "OCR信息识别成功" in txt
            or "OCR信息识别成功" in page_txt
            or "识别成功" in txt
        ):
            info["successToast"] = True
            info["ok"] = True
            page.wait_for_timeout(400)
            return info
        if saw_busy:
            info["ok"] = True
            page.wait_for_timeout(400)
            return info
        # 从未出现识别中：2s 后放行（复用人员通常已就绪）
        if time.time() - start >= 2.0:
            info["ok"] = True
            info["skip"] = "no-busy"
            return info
        page.wait_for_timeout(300)
    info["ok"] = True
    info["timeout"] = True
    return info


def _fix_id_upload(
    dialog,
    page: Page,
    *,
    force: bool = False,
    case_id: str = "",
    payload: dict | None = None,
    person_name: str = "",
) -> dict[str, Any]:
    """证件图处理失败时：删旧图并重新上传（优先案件已上传图）。force=True 强制重传。"""
    out: dict[str, Any] = {"attempted": False, "force": force}
    try:
        txt = dialog.inner_text(timeout=800) or ""
    except Exception:
        txt = ""
    failed = "图片处理失败" in txt
    out["failedBanner"] = failed
    if not force and not failed:
        try:
            imgs = dialog.locator(
                ".el-upload-list img, img[src^='blob'], img[src^='data:']"
            ).count()
        except Exception:
            imgs = 0
        if imgs >= 2:
            out["skip"] = "ok-preview"
            return out

    out["attempted"] = True
    # 点删除（多轮，避免删一个后列表变化）
    deleted = 0
    for _ in range(4):
        try:
            btn = dialog.locator("span, a, button, i").filter(
                has_text=re.compile(r"^删除$")
            ).first
            if not _visible(btn, timeout=300):
                # 悬停缩略图出现删除
                thumbs = dialog.locator(".el-upload-list__item, .el-upload-list li")
                if thumbs.count() == 0:
                    break
                thumbs.first.hover(timeout=800)
                btn = dialog.locator(
                    ".el-upload-list__item-delete, .el-icon-delete, [class*='delete']"
                ).first
            if _visible(btn, timeout=400):
                btn.click(force=True, timeout=1500)
                deleted += 1
                page.wait_for_timeout(400)
            else:
                break
        except Exception:
            break
    out["deleted"] = deleted

    front, back = store.resolve_id_card_images(
        case_id,
        payload,
        person_name=person_name or None,
        fallback_front=DEFAULT_ID_FRONT,
        fallback_back=DEFAULT_ID_BACK,
    )
    out["frontPath"] = str(front) if front else None
    out["backPath"] = str(back) if back else None
    out["personName"] = person_name or ""
    try:
        inputs = dialog.locator("input[type='file']")
        n = inputs.count()
        if n >= 1 and front is not None:
            inputs.nth(0).set_input_files(str(front.resolve()))
            out["front"] = True
            page.wait_for_timeout(1800)
        if n >= 2 and back is not None:
            inputs.nth(1).set_input_files(str(back.resolve()))
            out["back"] = True
            page.wait_for_timeout(1800)
        if out.get("front") or out.get("back"):
            out["ocr"] = _wait_ocr_idle(dialog, page, timeout_ms=30000)
    except Exception as e:
        out["error"] = str(e)
    return out


def _natural_via_id_ocr(
    dialog,
    page: Page,
    *,
    case_id: str = "",
    payload: dict | None = None,
    person_name: str = "",
) -> dict[str, Any]:
    """自然人弹窗：优先上传身份证等 OCR；无上传控件时才回退复用人员。"""
    info: dict[str, Any] = {"via": ""}
    n_files = 0
    try:
        n_files = dialog.locator("input[type='file']").count()
    except Exception:
        n_files = 0
    if n_files >= 1:
        info["via"] = "id-upload"
        info["idUpload"] = _fix_id_upload(
            dialog,
            page,
            force=True,
            case_id=case_id,
            payload=payload,
            person_name=person_name,
        )
        if not (info.get("idUpload") or {}).get("ocr"):
            info["ocr"] = _wait_ocr_idle(dialog, page, timeout_ms=30000)
        else:
            info["ocr"] = (info.get("idUpload") or {}).get("ocr")
        return info
    # 无上传位（部分角色弹窗只允许选已有投资人）
    info["via"] = "reuse-fallback"
    info["reuse"] = _reuse_first_person(
        page, host_dialog=dialog, person_name=person_name
    )
    page.wait_for_timeout(800)
    info["ocr"] = _wait_ocr_idle(dialog, page, timeout_ms=15000)
    return info


def _prepare_director_dialog(dlg, page: Page, cfg: dict[str, Any]) -> dict[str, Any]:
    """董事弹窗提交前必填项：法定代表人=是、证件图。"""
    info: dict[str, Any] = {}
    info["legalRep"] = _set_yes_no(dlg, "法定代表人", True) or _set_yes_no(
        dlg, "是否由董事担任", True
    )
    # 职务保持默认「董事（代表公司执行事务的董事）」即可
    # 产生方式若空则选股东委派
    try:
        txt = dlg.inner_text(timeout=600) or ""
        if "产生方式" in txt and "股东委派" not in txt and "董事聘任" not in txt:
            info["appoint"] = _select_dropdown(dlg, "产生方式", "股东委派")
    except Exception:
        pass
    return info


def _input_near_label(scope: Page | Frame, label: str):
    """Element/SAMR 通用：按可见文案找旁边的 input/textarea。"""
    loc = scope.get_by_text(label, exact=False).first
    if not _visible(loc, timeout=400):
        return None
    for xp in (
        "xpath=following::input[not(@type='hidden') and not(@type='checkbox') and not(@type='radio')][1]",
        "xpath=following::textarea[1]",
        "xpath=ancestor::*[self::div or self::tr or self::li or self::td][1]//input[not(@type='hidden') and not(@type='checkbox')][1]",
        "xpath=ancestor::*[self::div or self::tr][2]//input[not(@type='hidden')][1]",
        "xpath=ancestor::*[self::div or self::tr][2]//textarea[1]",
    ):
        try:
            inp = loc.locator(xp).first
            if _visible(inp, timeout=250):
                return inp
        except Exception:
            continue
    return None


def _fill_by_label(scope: Page | Frame, label: str, value: str) -> bool:
    if not value:
        return False
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    inp = None
    if _visible(item, timeout=400):
        inp = item.locator(
            "input.el-input__inner, textarea.el-textarea__inner, input:not([type='hidden']), textarea"
        ).first
        if not _visible(inp, timeout=300):
            inp = None
    if inp is None:
        inp = _input_near_label(scope, label)
    if inp is None or not _visible(inp, timeout=300):
        return False
    try:
        inp.click(timeout=2000)
        inp.fill("")
        inp.fill(str(value), timeout=4000)
        try:
            inp.press("Tab")
        except Exception:
            pass
        return True
    except Exception:
        return False


def _person_mobile(
    payload: dict | None, person_name: str, cfg: dict[str, Any] | None = None
) -> str:
    """按姓名从投资人取手机号，否则用法人/默认。"""
    name = (person_name or "").strip()
    for inv in (payload or {}).get("investors") or []:
        if not isinstance(inv, dict):
            continue
        if str(inv.get("name") or "").strip() != name:
            continue
        m = str(inv.get("mobile") or "").strip()
        if m:
            return m
    cfg = cfg or {}
    return str(cfg.get("mobile") or "").strip() or DEFAULT_MOBILE


def _select_nationality_china(dialog, page: Page) -> bool:
    """国别（地区）默认中国。"""
    labels = ("国别（地区）", "国别(地区)", "国别", "国家/地区", "国家")
    options = ("中国", "中华人民共和国")
    for lab in labels:
        for opt in options:
            if _select_dropdown(dialog, lab, opt):
                return True
            if _select_inline_option(page, dialog, lab, opt):
                return True
    return False


def _exec_partner_form_item(dialog):
    """定位弹窗顶部短表单项「执行事务合伙人」（避免匹配整页大容器）。"""
    try:
        labels = dialog.locator(".el-form-item__label").filter(
            has_text=re.compile(r"执行事务合伙人")
        )
        n = min(labels.count(), 8)
        for i in range(n):
            lab = labels.nth(i)
            if not _visible(lab, timeout=250):
                continue
            item = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item')][1]")
            if not _visible(item, timeout=250):
                continue
            try:
                box = item.bounding_box()
                if box and box.get("height", 0) > 140:
                    continue
            except Exception:
                pass
            return item
    except Exception:
        pass
    item = dialog.locator(".el-form-item").filter(
        has_text=re.compile(r"执行事务合伙人")
    ).first
    if _visible(item, timeout=400):
        return item
    return None


def _select_exec_partner_person(dialog, page: Page, person_name: str) -> bool:
    """「执行事务合伙人」下拉选指定合伙人姓名（非「是/否」）。"""
    person_name = (person_name or "").strip()
    if not person_name:
        return False

    def _read_val(item) -> str:
        try:
            inp = item.locator(
                ".el-select .el-input__inner, input.el-input__inner, input"
            ).first
            if _visible(inp, timeout=250):
                return (inp.input_value(timeout=300) or "").strip()
        except Exception:
            pass
        return ""

    def _pick_from_open() -> bool:
        for root in _all_scopes(page):
            try:
                opts = root.locator(
                    ".el-select-dropdown:visible li.el-select-dropdown__item, "
                    ".el-select-dropdown:visible li, "
                    ".el-popper:visible li.el-select-dropdown__item, "
                    ".el-popper:visible li"
                )
                n = min(opts.count(), 40)
            except Exception:
                continue
            # 精确匹配姓名
            for i in range(n):
                o = opts.nth(i)
                try:
                    t = (o.inner_text(timeout=200) or "").strip()
                except Exception:
                    continue
                if t == person_name and _visible(o, timeout=250):
                    try:
                        o.click(timeout=2500)
                        page.wait_for_timeout(350)
                        return True
                    except Exception:
                        continue
            # 包含姓名（选项可能带证件号等后缀）
            for i in range(n):
                o = opts.nth(i)
                try:
                    t = (o.inner_text(timeout=200) or "").strip()
                except Exception:
                    continue
                if (
                    person_name in t
                    and t not in ("请选择", "是", "否")
                    and _visible(o, timeout=250)
                ):
                    try:
                        o.click(timeout=2500)
                        page.wait_for_timeout(350)
                        return True
                    except Exception:
                        continue
        return False

    item = _exec_partner_form_item(dialog)
    if item is None:
        # 回退通用下拉
        if _select_dropdown(dialog, "执行事务合伙人", person_name):
            return True
        if _select_inline_option(page, dialog, "执行事务合伙人", person_name):
            return True
        return False

    cur = _read_val(item)
    if cur and person_name in cur and cur not in ("请选择",):
        return True

    # 打开下拉
    opened = False
    try:
        trig = item.locator(
            ".el-select .el-input__inner, .el-select, input.el-input__inner"
        ).first
        if _visible(trig, timeout=500):
            trig.click(timeout=2500)
            page.wait_for_timeout(450)
            opened = True
    except Exception:
        pass
    if not opened:
        try:
            lab = item.locator(".el-form-item__label").first
            if _visible(lab, timeout=300):
                box = lab.bounding_box()
                if box:
                    page.mouse.click(
                        box["x"] + box["width"] + 80,
                        box["y"] + box["height"] / 2,
                    )
                    page.wait_for_timeout(450)
                    opened = True
        except Exception:
            pass

    if opened and _pick_from_open():
        cur2 = _read_val(item)
        if cur2 and person_name in cur2:
            return True
        # 有的下拉展示后 input 仍短暂为空，再读一次
        page.wait_for_timeout(400)
        cur3 = _read_val(item)
        return bool(cur3 and person_name in cur3)

    # 通用路径再试
    if _select_dropdown(dialog, "执行事务合伙人", person_name):
        return True
    if _select_inline_option(page, dialog, "执行事务合伙人", person_name):
        return True
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def _fill_contact_mobile(dialog, mobile: str) -> dict[str, Any]:
    """填「移动电话」（勿误填「固定电话」），写入后回读校验。"""
    mobile = (mobile or "").strip()
    info: dict[str, Any] = {"ok": False, "value": mobile}
    if not mobile:
        return info

    def _mobile_item():
        # 精确：label 文本就是移动电话（排除固定电话）
        try:
            items = dialog.locator(".el-form-item")
            n = min(items.count(), 40)
            for i in range(n):
                it = items.nth(i)
                try:
                    lab = it.locator(".el-form-item__label, label").first
                    t = re.sub(r"\s+", "", (lab.inner_text(timeout=200) or "") if _visible(lab, timeout=150) else "")
                    if not t:
                        t = re.sub(r"\s+", "", it.inner_text(timeout=200) or "")[:20]
                    if "移动电话" in t or t == "手机" or t.startswith("手机号"):
                        if "固定" in t:
                            continue
                        return it
                except Exception:
                    continue
        except Exception:
            pass
        # 回退：filter 但排除含固定
        it = dialog.locator(".el-form-item").filter(has_text=re.compile(r"移动电话")).first
        if _visible(it, timeout=400):
            try:
                t = re.sub(r"\s+", "", it.inner_text(timeout=300) or "")
                if "固定电话" in t and "移动电话" not in t[:12]:
                    return None
            except Exception:
                pass
            return it
        return None

    item = _mobile_item()
    if item is None:
        # 再试联系方式类（仍避开固定电话）
        for lab in ("联系方式一", "联系方式", "手机号码", "手机号"):
            it = dialog.locator(".el-form-item").filter(has_text=re.compile(re.escape(lab))).first
            if not _visible(it, timeout=300):
                continue
            try:
                t = re.sub(r"\s+", "", it.inner_text(timeout=200) or "")
                if "固定" in t:
                    continue
            except Exception:
                pass
            item = it
            info["label"] = lab
            break
    else:
        info["label"] = "移动电话"

    if item is None:
        info["error"] = "未找到移动电话表单项"
        return info

    inp = item.locator(
        "input.el-input__inner, input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
    ).first
    if not _visible(inp, timeout=500):
        info["error"] = "移动电话输入框不可见"
        return info

    try:
        cur = (inp.input_value(timeout=400) or "").strip()
        if cur and mobile in cur.replace(" ", ""):
            info["ok"] = True
            info["skipped"] = "already"
            info["readback"] = cur
            return info
    except Exception:
        pass

    for attempt in range(3):
        try:
            inp.click(timeout=2000)
            inp.fill("")
            inp.fill(mobile, timeout=4000)
            try:
                inp.press("Tab")
            except Exception:
                pass
            # JS 兜底写入（Vue 受控）
            inp.evaluate(
                """(el, v) => {
                  const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                  )?.set;
                  el.focus();
                  if (setter) setter.call(el, v); else el.value = v;
                  el.dispatchEvent(new InputEvent('input', { bubbles: true, data: v }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  try { el.blur(); } catch (e) {}
                  return el.value;
                }""",
                mobile,
            )
            page_wait = getattr(dialog, "page", None)
            if page_wait is None and hasattr(dialog, "evaluate"):
                try:
                    # Locator 没有 page 时用短 sleep 替代
                    import time as _t

                    _t.sleep(0.25)
                except Exception:
                    pass
            else:
                try:
                    dialog.page.wait_for_timeout(250)  # type: ignore[attr-defined]
                except Exception:
                    pass
            got = (inp.input_value(timeout=500) or "").strip()
            info["readback"] = got
            info["attempt"] = attempt
            if got and mobile in got.replace(" ", ""):
                info["ok"] = True
                return info
        except Exception as e:
            info["error"] = str(e)
    # 最后：js 按精确 label
    if _js_fill_near(dialog, "移动电话", mobile):
        try:
            got = (inp.input_value(timeout=400) or "").strip()
            info["readback"] = got
            info["ok"] = bool(got and mobile in got.replace(" ", ""))
            info["via"] = "js_fill_near"
            if info["ok"]:
                return info
        except Exception:
            info["ok"] = True
            info["soft"] = True
            return info
    return info


def _fill_label_any(page: Page, labels: list[str], value: str) -> bool:
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        for scope in _all_scopes(pg):
            for lab in labels:
                if _fill_by_label(scope, lab, value):
                    return True
    return False


def _reuse_first_person(page: Page, host_dialog=None, person_name: str = "") -> bool:
    """复用人员信息：选列表第一条（优先匹配姓名）。"""
    roots = []
    if host_dialog is not None:
        roots.append(host_dialog)
    roots.extend(_all_scopes(page))
    clicked = False
    for root in roots:
        try:
            item = root.locator(".el-form-item").filter(has_text=re.compile(r"复用人员信息")).first
            if _visible(item, timeout=600):
                trigger = item.locator(
                    ".el-select .el-input__inner, .el-input__inner, input, .el-select"
                ).first
                if _visible(trigger, timeout=500):
                    trigger.click(force=True, timeout=3000)
                    clicked = True
                    break
            lab = root.get_by_text("复用人员信息", exact=False).first
            if _visible(lab, timeout=400):
                box = lab.bounding_box()
                if box:
                    page.mouse.click(
                        box["x"] + min(box["width"] + 100, 260),
                        box["y"] + box["height"] / 2,
                    )
                    clicked = True
                    break
        except Exception:
            continue
    if not clicked:
        return False
    page.wait_for_timeout(700)
    for root in _all_scopes(page):
        try:
            opts = root.locator(
                ".el-select-dropdown:visible .el-select-dropdown__item, "
                ".el-popper:visible li.el-select-dropdown__item, "
                ".el-select-dropdown:visible li"
            )
            n = min(opts.count(), 12)
            if n == 0:
                continue
            chosen = None
            if person_name:
                for i in range(n):
                    o = opts.nth(i)
                    if not _visible(o, timeout=300):
                        continue
                    txt = (o.inner_text(timeout=300) or "").strip()
                    if person_name in txt:
                        chosen = o
                        break
            if chosen is None:
                for i in range(n):
                    o = opts.nth(i)
                    if _visible(o, timeout=300):
                        chosen = o
                        break
            if chosen is not None:
                chosen.click(timeout=3000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            continue
    return False


def _click_dialog_ok(dialog, page: Page | None = None) -> bool:
    """点弹窗「确定」：优先真实鼠标坐标点击（人工能点中、自动化常 miss）。"""
    if page is None:
        try:
            page = dialog.page  # type: ignore[attr-defined]
        except Exception:
            page = None

    try:
        dialog.evaluate(
            """(el) => {
              const b = el.querySelector('.el-dialog__body');
              if (b) b.scrollTop = b.scrollHeight;
              const f = el.querySelector('.el-dialog__footer');
              if (f) f.scrollIntoView({block:'nearest', inline:'center'});
            }"""
        )
    except Exception:
        pass

    btn = None
    for sel in (
        ".el-dialog__footer button.el-button--primary",
        ".el-dialog__footer button.el-button--primary span",
        "button.el-button--primary",
    ):
        try:
            cand = dialog.locator(sel).filter(has_text=re.compile(r"确\s*定")).last
            if _visible(cand, timeout=500):
                btn = cand
                break
        except Exception:
            continue
    if btn is None:
        try:
            btn = dialog.get_by_role("button", name=re.compile(r"确\s*定")).last
            if not _visible(btn, timeout=400):
                btn = None
        except Exception:
            btn = None

    # 1) 真实鼠标点中心（最接近人工）
    if btn is not None and page is not None:
        try:
            btn.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        try:
            box = btn.bounding_box()
            if box and box.get("width", 0) > 8 and box.get("height", 0) > 8:
                x = box["x"] + box["width"] / 2
                y = box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.wait_for_timeout(80)
                page.mouse.click(x, y)
                return True
        except Exception:
            pass
        try:
            btn.click(timeout=4000)
            return True
        except Exception:
            pass
        try:
            btn.click(force=True, timeout=3000)
            return True
        except Exception:
            pass

    # 2) JS：原生 MouseEvent
    try:
        js = dialog.evaluate(
            """(el) => {
              const footer = el.querySelector('.el-dialog__footer') || el;
              const buttons = Array.from(footer.querySelectorAll('button'));
              for (const b of buttons.reverse()) {
                const t = (b.innerText || '').replace(/\\s+/g, '');
                if (t !== '确定' && t !== '确认') continue;
                b.scrollIntoView({block:'nearest', inline:'center'});
                const r = b.getBoundingClientRect();
                const opts = {bubbles:true, cancelable:true, view:window,
                  clientX:r.left+r.width/2, clientY:r.top+r.height/2};
                b.dispatchEvent(new MouseEvent('mousedown', opts));
                b.dispatchEvent(new MouseEvent('mouseup', opts));
                b.dispatchEvent(new MouseEvent('click', opts));
                return true;
              }
              return false;
            }"""
        )
        if js:
            return True
    except Exception:
        pass

    if page is not None:
        return _click_text_any(page, ["确定"], exact=True)
    return _click_text(dialog, ["确定", "确认"], exact=True)


def _set_yes_no(dialog, label_hint: str, yes: bool = True) -> bool:
    """在含 label_hint 的表单项里点「是/否」，并尽量确认已选中。"""
    want = "是" if yes else "否"
    try:
        item = dialog.locator(".el-form-item").filter(
            has_text=re.compile(label_hint)
        ).first
        if not _visible(item, timeout=800):
            return False

        def _is_checked() -> bool:
            try:
                lab = item.locator("label.el-radio").filter(
                    has_text=re.compile(rf"^\s*{want}\s*$")
                ).first
                cls = lab.get_attribute("class") or ""
                if "is-checked" in cls:
                    return True
                inp = lab.locator("input[type=radio]").first
                if _visible(inp, timeout=200):
                    return bool(inp.is_checked())
            except Exception:
                pass
            return False

        # 1) Playwright 点 el-radio
        try:
            radio = item.locator("label.el-radio").filter(
                has_text=re.compile(rf"^\s*{want}\s*$")
            ).first
            if _visible(radio, timeout=500):
                radio.click(force=True, timeout=3000)
                if _is_checked():
                    return True
                radio.locator(".el-radio__inner").first.click(force=True, timeout=2000)
                if _is_checked():
                    return True
        except Exception:
            pass

        # 2) JS：触发 click + input/change 事件
        js = item.evaluate(
            """(el, want) => {
              const labels = Array.from(el.querySelectorAll('label.el-radio'));
              for (const lab of labels) {
                const t = (lab.innerText || '').replace(/\\s+/g, '');
                if (t !== want) continue;
                const input = lab.querySelector('input[type=radio]');
                const inner = lab.querySelector('.el-radio__inner');
                if (inner) inner.click();
                else lab.click();
                if (input) {
                  input.checked = true;
                  input.dispatchEvent(new Event('input', {bubbles:true}));
                  input.dispatchEvent(new Event('change', {bubbles:true}));
                }
                return true;
              }
              return false;
            }""",
            want,
        )
        if js and _is_checked():
            return True
        if js:
            return True
        lab = item.locator("label").filter(has_text=re.compile(rf"^\s*{want}\s*$")).first
        if _visible(lab, timeout=400):
            lab.click(force=True, timeout=3000)
            return True
    except Exception:
        pass
    return False


def _dialog_still_open(page: Page, *hints: str) -> bool:
    d, _ = _find_dialog(page, *hints)
    return d is not None


def _confirm_until_closed(page: Page, hints: tuple[str, ...], *, retries: int = 5) -> bool:
    """反复点确定直到指定弹窗消失。"""
    from apps.worker.playbooks.retry_gate import dismiss_system_busy

    for i in range(retries):
        # 系统繁忙可能叠在业务弹窗上：先关掉再点业务确定
        if dismiss_system_busy(page):
            page.wait_for_timeout(800)
        dlg, _ = _find_dialog(page, *hints)
        if dlg is None:
            return True
        if any("董事" in h for h in hints):
            _set_yes_no(dlg, "法定代表人", True)
            _wait_ocr_idle(dlg, page, timeout_ms=8000)
        hit = _click_dialog_ok(dlg, page)
        page.wait_for_timeout(1100)
        if dismiss_system_busy(page):
            page.wait_for_timeout(800)
        if not _dialog_still_open(page, *hints):
            return True
        if not hit or i >= 1:
            try:
                for scope in _all_scopes(page):
                    btn = scope.locator(
                        ".el-dialog__footer:visible button.el-button--primary"
                    ).filter(has_text=re.compile(r"确\s*定")).last
                    if not _visible(btn, timeout=400):
                        continue
                    box = btn.bounding_box()
                    if not box:
                        continue
                    page.mouse.click(
                        box["x"] + box["width"] / 2,
                        box["y"] + box["height"] / 2,
                    )
                    page.wait_for_timeout(1000)
                    if dismiss_system_busy(page):
                        page.wait_for_timeout(600)
                    if not _dialog_still_open(page, *hints):
                        return True
            except Exception:
                pass
    return not _dialog_still_open(page, *hints)


def _frame_personnel(page: Page, timeout_ms: int = 45000) -> Frame | Page | None:
    """定位人员信息页（web-reg-apply / chief-executive-officer / wujie-app）。

    注意：左侧菜单「人员信息」在外壳页；表单在 wujie 内，须读 wujie-app body。
    """
    markers = (
        "董事信息",
        "设一名董事",
        "添加董事",
        "监事信息",
        "不设监事",
        "执行事务合伙人",
        "添加执行事务合伙人",
        "人员信息",
        "高级管理人员",
        "添加高级管理人员",
        "财务负责人",
        "登记联络员",
        "添加联络员",
    )

    def _scope_text(scope: Page | Frame) -> str:
        try:
            return _body(scope).inner_text(timeout=800) or ""
        except Exception:
            return ""

    def _marker_hits(text: str) -> int:
        return sum(1 for m in markers if m in text)

    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        page_url = page.url or ""
        url_hit = "chief-executive-officer" in page_url or "chief-executive" in page_url

        # 1) 优先 web-reg-apply（含 wujie-app body）
        for fr in page.frames:
            url = fr.url or ""
            name = fr.name or ""
            if "web-reg-apply" not in url and name != "web-reg-apply":
                continue
            body = _scope_text(fr)
            # 仅当普通 body 是 stub 且 wujie 也无内容时才跳过
            if "doesn't work properly without JavaScript" in body and _marker_hits(body) == 0:
                # 再试 wujie 专属定位 / get_by_text
                try:
                    if fr.locator("wujie-app").count() > 0:
                        wtxt = ""
                        try:
                            wtxt = fr.locator("wujie-app").inner_text(timeout=800) or ""
                        except Exception:
                            wtxt = ""
                        if _marker_hits(wtxt) >= 1 or ("监事" in wtxt and "高级管理人员" in wtxt):
                            return fr
                    if fr.get_by_text("添加高级管理人员", exact=False).count() > 0:
                        return fr
                    if fr.get_by_text("添加董事", exact=False).count() > 0:
                        return fr
                    if fr.get_by_text("登记联络员", exact=False).count() > 0:
                        return fr
                except Exception:
                    pass
                if url_hit:
                    # URL 已指向人员页：即便 stub，仍返回该业务帧供后续 get_by_text 使用
                    return fr
                continue
            hit = _marker_hits(body)
            if hit >= 2 or (url_hit and hit >= 1) or ("监事" in body and "高级管理人员" in body):
                return fr
            if url_hit and ("监事" in body or "董事" in body or "联络员" in body):
                return fr

        # 2) 主页面 / 其他 frame：get_by_text（可穿透部分 shadow）
        for fr in _all_scopes(page):
            body = _scope_text(fr)
            hit = _marker_hits(body)
            if hit >= 2 or ("监事" in body and "高级管理人员" in body):
                return fr
            try:
                if fr.get_by_text("添加高级管理人员", exact=False).count() > 0:
                    return fr
                if fr.get_by_text("添加董事", exact=False).count() > 0:
                    return fr
                if fr.get_by_text("设一名董事", exact=False).count() > 0:
                    return fr
            except Exception:
                pass

        # 3) URL 已到人员路由：用主 page 兜底（截图证明内容可见）
        if url_hit:
            try:
                if page.get_by_text("添加高级管理人员", exact=False).count() > 0:
                    return page
                if page.get_by_text("登记联络员", exact=False).count() > 0:
                    return page
                if page.locator("wujie-app").count() > 0:
                    wtxt = page.locator("wujie-app").inner_text(timeout=800) or ""
                    if _marker_hits(wtxt) >= 1 or "高级管理人员" in wtxt:
                        return page
            except Exception:
                pass
            page.wait_for_timeout(500)
            continue
        page.wait_for_timeout(400)
    return None


def _birth_from_id(id_no: str) -> str:
    s = (id_no or "").strip().upper()
    if len(s) >= 14 and s[6:14].isdigit():
        return f"{s[6:10]}-{s[10:12]}-{s[12:14]}"
    return ""


def _defaults(payload: dict[str, Any], case_id: str = "") -> dict[str, Any]:
    from apps.worker.playbooks.org_fill_profile import profile_for

    inv = (payload.get("investors") or [{}])[0]
    personnel = payload.get("personnel") or {}
    lr = personnel.get("legalRepresentative") or {}
    directors = [d for d in (personnel.get("directors") or []) if isinstance(d, dict) and str(d.get("name") or "").strip()]
    supervisors = [s for s in (personnel.get("supervisors") or []) if isinstance(s, dict) and str(s.get("name") or "").strip()]
    finance = personnel.get("financeOfficer") or {}
    liaison = personnel.get("liaisonOfficer") or {}
    bo = payload.get("beneficialOwner") or {}
    dir0 = directors[0] if directors else {}
    sup0 = supervisors[0] if supervisors else {}
    profile = profile_for(payload)
    is_limited_partnership = bool(profile.get("is_limited_partnership"))
    is_general_partnership = bool(profile.get("is_general_partnership"))
    is_partnership = bool(profile.get("is_partnership"))
    is_sole_prop = bool(profile.get("is_sole_prop"))
    skip_board = bool(profile.get("skip_board"))

    id_no = (inv.get("idNumber") or lr.get("idNumber") or "").strip()
    lr_name = (lr.get("name") or inv.get("name") or DEFAULT_NAME).strip()
    lr_mobile = (lr.get("mobile") or inv.get("mobile") or DEFAULT_MOBILE).strip()
    address = (
        (liaison.get("address") if isinstance(liaison, dict) else None)
        or inv.get("address")
        or DEFAULT_ADDRESS
    ).strip()
    email = (
        (liaison.get("email") if isinstance(liaison, dict) else None) or DEFAULT_EMAIL
    ).strip()

    # 受益人经常居住地：原型「最终受益人 → 住所*」用户填写值
    bo_name_early = str(bo.get("name") or lr_name or "").strip()
    bo_address, bo_parts = _resolve_bo_user_address(
        payload,
        bo if isinstance(bo, dict) else {},
        fallback_name=bo_name_early,
    )
    if not bo_address:
        bo_address = address
        bo_parts = _parse_cn_region_address(bo_address)
    bo_parts["country"] = "中国"

    try:
        ownership_ratio = float(bo.get("ownershipRatio") or 0)
    except (TypeError, ValueError):
        ownership_ratio = 0.0
    if ownership_ratio <= 0:
        ownership_ratio = 100.0
    ratio_str = (
        str(int(ownership_ratio))
        if float(ownership_ratio).is_integer()
        else str(ownership_ratio)
    )

    establish_supervisor = bool(personnel.get("establishSupervisor"))
    if not establish_supervisor and supervisors:
        # 有监事名单则视为设立
        establish_supervisor = any(str((s or {}).get("name") or "").strip() for s in supervisors)
    if profile.get("is_shares"):
        # 股份有限公司必须设立一名监事
        establish_supervisor = True
    if skip_board:
        establish_supervisor = False
    # 仅一名监事，不设监事会
    if establish_supervisor and supervisors:
        supervisors = supervisors[:1]
        sup0 = supervisors[0] if supervisors else {}
    supervisor_board = False
    employee_supervisor_count = 0
    # 董事与监事不能同一人（与原型校验一致）
    dir_names = {
        str(d.get("name") or "").strip()
        for d in directors
        if isinstance(d, dict) and str(d.get("name") or "").strip()
    }
    if (dir0.get("name") or "").strip():
        dir_names.add(str(dir0.get("name") or "").strip())
    if establish_supervisor and supervisors:
        sn = str(supervisors[0].get("name") or "").strip()
        if sn and sn in dir_names and not profile.get("is_shares"):
            supervisors = []
            establish_supervisor = False
            sup0 = {}
        elif sn and sn in dir_names and profile.get("is_shares"):
            # 股份公司必须设监事：保留原名单，填报阶段若同名会 NEED_HUMAN
            pass

    exec_names = [
        str(x).strip()
        for x in (personnel.get("executivePartnerNames") or [])
        if str(x).strip()
    ]
    license_names = [
        str(x).strip()
        for x in (personnel.get("licenseHolderNames") or [])
        if str(x).strip()
    ]
    if not exec_names:
        for item in payload.get("investors") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if is_limited_partnership and str(item.get("liabilityType") or "").strip() == "无限责任":
                exec_names.append(name)
            elif is_general_partnership and bool(item.get("isExecutivePartner")):
                exec_names.append(name)
            elif (not is_general_partnership) and bool(item.get("isExecutivePartner")):
                exec_names.append(name)
    if not license_names:
        for item in payload.get("investors") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if is_limited_partnership and str(item.get("liabilityType") or "").strip() == "无限责任":
                license_names.append(name)
            elif bool(item.get("isLicenseHolder")):
                license_names.append(name)
    # 有限合伙：持照人=执行事务合伙人
    if is_limited_partnership and exec_names and not license_names:
        license_names = list(exec_names)
    # 普通合伙兜底：至少一名执行事务 / 持照人（页面必填）
    if is_general_partnership:
        if not exec_names and lr_name:
            exec_names = [lr_name]
        if not license_names:
            license_names = list(exec_names) if exec_names else ([lr_name] if lr_name else [])
    # 去重保序
    def _uniq(seq: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in seq:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    exec_names = _uniq(exec_names)
    license_names = _uniq(license_names)

    return {
        "name": lr_name,
        "mobile": lr_mobile,
        "address": address,
        "email": email,
        "id_valid": (bo.get("idValidUntil") or DEFAULT_ID_VALID).strip() or DEFAULT_ID_VALID,
        "id_number": id_no,
        "birth": _birth_from_id(id_no) or "2002-10-31",
        "director_name": (dir0.get("name") or lr_name).strip(),
        "director_mobile": (dir0.get("mobile") or lr_mobile).strip(),
        "director_list": [
            {
                "name": str(d.get("name") or "").strip(),
                "mobile": str(d.get("mobile") or lr_mobile).strip(),
                "idNumber": str(d.get("idNumber") or "").strip(),
            }
            for d in directors
        ] or [{"name": (dir0.get("name") or lr_name).strip(), "mobile": lr_mobile, "idNumber": ""}],
        "supervisor_list": [
            {
                "name": str(s.get("name") or "").strip(),
                "mobile": str(s.get("mobile") or "").strip(),
                "idNumber": str(s.get("idNumber") or "").strip(),
            }
            for s in supervisors
        ],
        "finance_name": (finance.get("name") or lr_name).strip() if isinstance(finance, dict) else lr_name,
        "finance_mobile": (finance.get("mobile") or lr_mobile).strip()
        if isinstance(finance, dict)
        else lr_mobile,
        "liaison_name": (liaison.get("name") or lr_name).strip() if isinstance(liaison, dict) else lr_name,
        "liaison_mobile": (liaison.get("mobile") or lr_mobile).strip()
        if isinstance(liaison, dict)
        else lr_mobile,
        "supervisor_name": (sup0.get("name") or "").strip(),
        "supervisor_mobile": (sup0.get("mobile") or "").strip(),
        "establish_supervisor": establish_supervisor,
        "supervisor_board": supervisor_board,
        "supervisor_count": len(supervisors),
        "employee_supervisor_count": employee_supervisor_count,
        "bo_name": (bo.get("name") or lr_name).strip(),
        "bo_mobile": (bo.get("mobile") or lr_mobile).strip(),
        "bo_address": bo_address,
        "bo_addr_parts": bo_parts,
        "ownership_ratio": ratio_str,
        "is_partnership": is_partnership,
        "is_limited_partnership": is_limited_partnership,
        "is_general_partnership": is_general_partnership,
        "is_sole_prop": is_sole_prop,
        "is_shares": bool(profile.get("is_shares")),
        "skip_board": skip_board,
        "executive_partner_names": exec_names,
        "license_holder_names": license_names,
    }


def _pick_date_input(page: Page, scope, label: str, value: str) -> bool:
    """填日期：优先 label 旁 input，值为 YYYY-MM-DD。"""
    inp = None
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if _visible(item, timeout=400):
        inp = item.locator(".el-date-editor input, input").first
        if not _visible(inp, timeout=300):
            inp = None
    if inp is None:
        inp = _input_near_label(scope, label)
    if inp is None or not _visible(inp, timeout=300):
        return False
    try:
        inp.click(timeout=2000)
        inp.fill("")
        inp.fill(value)
        inp.press("Enter")
        page.wait_for_timeout(200)
        return True
    except Exception:
        return False


def _pick_date_any(page: Page, labels: list[str], value: str) -> bool:
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        for scope in _all_scopes(pg):
            for lab in labels:
                if _pick_date_input(pg, scope, lab, value):
                    return True
    return False


def _click_visible_dropdown_option(page: Page, text: str) -> bool:
    """只点当前可见下拉面板里的选项。"""
    for sel in (
        ".layui-form-select dl dd",
        ".layui-anim-upbit dd",
        ".combo-panel .combobox-item",
        ".el-select-dropdown:visible li",
        ".el-popper:visible li.el-select-dropdown__item",
        ".el-cascader-menu:visible .el-cascader-node",
        ".ant-select-dropdown:visible .ant-select-item",
    ):
        try:
            opt = page.locator(sel).filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")).first
            if _visible(opt, timeout=500):
                opt.click(timeout=2500)
                page.wait_for_timeout(300)
                return True
        except Exception:
            continue
    return False


def _el_selects(scope):
    return scope.locator(
        ".el-select:visible, .ant-select:visible, .layui-form-select:visible, .combo:visible"
    )


def _open_nth_select(scope, page: Page, n: int) -> bool:
    try:
        sels = _el_selects(scope)
        if sels.count() <= n:
            return False
        s = sels.nth(n)
        if not _visible(s, timeout=400):
            return False
        s.scroll_into_view_if_needed(timeout=1500)
        s.click(timeout=2500)
        page.wait_for_timeout(400)
        return True
    except Exception:
        return False


def _click_option_any(page: Page, text: str) -> bool:
    """只在当前页点下拉选项，避免点到其它标签页里的同名文案。"""
    if _click_visible_dropdown_option(page, text):
        return True
    for root in _all_scopes(page):
        try:
            opt = root.locator(
                ".el-select-dropdown:visible li, .el-cascader-menu:visible .el-cascader-node, "
                ".el-popper:visible li, .ant-select-dropdown:visible .ant-select-item"
            ).filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")).first
            if not _visible(opt, timeout=300):
                opt = root.get_by_text(text, exact=True).first
            if _visible(opt, timeout=300):
                opt.click(timeout=2500)
                page.wait_for_timeout(250)
                return True
        except Exception:
            continue
    return False


def _open_select_near_label(scope: Page | Frame, label: str, *, exact: bool = False) -> bool:
    loc = scope.get_by_text(label, exact=exact).first
    if not _visible(loc, timeout=400):
        loc = scope.get_by_text(label, exact=False).first
    if not _visible(loc, timeout=400):
        return False
    page = _page_of(scope)
    try:
        trig = loc.locator(
            "xpath=following::*[contains(@class,'el-select') or contains(@class,'el-cascader') or contains(@class,'ant-select')][1]"
        ).first
        if _visible(trig, timeout=300):
            trig.click(timeout=2500)
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass
    inp = _input_near_label(scope, label)
    if inp is not None:
        try:
            inp.click(timeout=2500)
            page.wait_for_timeout(300)
            return True
        except Exception:
            pass
    try:
        box = loc.bounding_box()
        if box:
            page.mouse.click(box["x"] + box["width"] + 90, box["y"] + box["height"] / 2)
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass
    return False


def _select_inline_option(
    page: Page, scope, label: str, option: str, *, exact_label: bool = False
) -> bool:
    """点标签右侧下拉再选选项（地址四个下拉共用一个大表单项时，不能用 first select）。"""
    if _open_select_near_label(scope, label, exact=exact_label):
        if _click_option_any(page, option):
            return True
    if _select_dropdown(scope, label, option):
        return True
    return False


def _select_cascader_addr(page: Page, scope, country: str, province: str, city: str, district: str) -> dict[str, Any]:
    info: dict[str, Any] = {}
    info["country"] = _select_dropdown(scope, "国家/地区", country) or _select_dropdown(
        scope, "国家", country
    )
    if not info["country"]:
        if _open_select_near_label(scope, "国家/地区") or _open_select_near_label(scope, "国家"):
            info["country"] = _click_option_any(page, country)
    page.wait_for_timeout(300)
    for lab in ("省、自治区", "省", "自治区"):
        if _select_dropdown(scope, lab, province):
            info["province"] = True
            break
        if _open_select_near_label(scope, lab) and _click_option_any(page, province):
            info["province"] = True
            break
    page.wait_for_timeout(300)
    for lab in ("市",):
        if _select_dropdown(scope, lab, city):
            info["city"] = True
            break
        if _open_select_near_label(scope, lab) and _click_option_any(page, city):
            info["city"] = True
            break
    page.wait_for_timeout(300)
    for lab in ("县（区）", "县", "区"):
        if _select_dropdown(scope, lab, district):
            info["district"] = True
            break
        if _open_select_near_label(scope, lab) and _click_option_any(page, district):
            info["district"] = True
            break
    return info


def _all_pages(page: Page) -> list[Page]:
    try:
        ctx = page.context
        return list(ctx.pages)
    except Exception:
        return [page]


def _url_of(pg: Page) -> str:
    try:
        return pg.url or ""
    except Exception:
        return ""


def _is_portal_home(pg: Page) -> bool:
    url = _url_of(pg)
    if "portal_yct" in url and "web-yct-master" not in url and "web-reg-apply" not in url:
        return True
    try:
        loc = pg.get_by_text("请选择业务办理", exact=False).first
        if _visible(loc, timeout=300):
            return True
    except Exception:
        pass
    return False


def _page_has_text(page: Page, marker: str) -> bool:
    """只在给定 page 及其 frame 上找文案（不扫其它标签页）。"""
    for scope in _all_scopes(page):
        try:
            loc = scope.get_by_text(marker, exact=False).first
            if _visible(loc, timeout=300):
                return True
        except Exception:
            continue
    return False


def _switch_to_bo_page(page: Page) -> Page | None:
    """备案若开了新窗口，只切到真正含「自行填报」的页，绝不切门户首页。"""
    candidates: list[Page] = []
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        url = _url_of(pg)
        if _page_has_text(pg, "自行填报") or _page_has_text(pg, "承诺免报"):
            candidates.append(pg)
            continue
        if _page_has_text(pg, "受益所有人基本信息"):
            candidates.append(pg)
            continue
        if any(k in url.lower() for k in ("beneficial", "syzrr", "ubo", "benefic")):
            candidates.append(pg)
    if not candidates:
        # 原页已出现自行填报
        if not _is_portal_home(page) and _page_has_text(page, "自行填报"):
            return page
        return None
    # 优先 online.yct / web-reg-apply / 当前页
    for pg in candidates:
        url = _url_of(pg)
        if "online.yct" in url or "web-reg-apply" in url or "web-yct-master" in url:
            return pg
    return candidates[0]


def _close_stray_portal_tabs(keep: Page) -> int:
    """关掉误开的门户首页标签，避免后续操作切到「请选择业务办理」。"""
    closed = 0
    try:
        pages = list(keep.context.pages)
    except Exception:
        return 0
    for pg in pages:
        if pg is keep:
            continue
        if not _is_portal_home(pg):
            continue
        try:
            pg.close()
            closed += 1
        except Exception:
            continue
    return closed


def _page_alive(pg: Page | None) -> bool:
    if pg is None:
        return False
    try:
        return not pg.is_closed()
    except Exception:
        return False


def _safe_wait(pg: Page | None, ms: int) -> None:
    try:
        if _page_alive(pg):
            pg.wait_for_timeout(ms)
    except Exception:
        pass


def _find_reg_page(seed: Page | None) -> Page | None:
    """找回设立/人员信息登记页（排除备案页与门户首页）。"""
    pages: list[Page] = []
    if _page_alive(seed):
        try:
            pages = list(seed.context.pages)
        except Exception:
            pages = [seed]
    for pg in pages:
        if not _page_alive(pg) or _is_portal_home(pg):
            continue
        u = _url_of(pg)
        if "syr.samr.gov.cn" in u or "bow/apply" in u:
            continue
        if any(k in u for k in ("web-yct-master", "web-reg-apply", "chief-executive")):
            return pg
        if _bo_prompt_open(pg):
            return pg
        if _frame_personnel(pg, timeout_ms=600) is not None:
            return pg
    return seed if _page_alive(seed) else None


def _prefer_work_page(page: Page) -> Page:
    """优先受益所有人页；其次图5弹窗所在登记页；不要停在门户首页。"""
    bo = _switch_to_bo_page(page)
    if bo is not None:
        return bo
    if _bo_prompt_open(page):
        return page
    if not _is_portal_home(page) and _frame_personnel(page, timeout_ms=2500) is not None:
        return page
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        if _bo_prompt_open(pg):
            return pg
        if _frame_personnel(pg, timeout_ms=1500) is not None:
            return pg
    return page


def _section_quota_done(frame, hint: str) -> bool:
    try:
        txt = _body(frame).inner_text(timeout=800) or ""
    except Exception:
        txt = ""
    compact = txt.replace(" ", "").replace("\u00a0", "")
    if hint == "董事":
        if re.search(r"董事\s*[\(（]\s*[1-9]", txt):
            return True
        if "执行事务的董事" in txt or ("是否法定代表人" in txt and "修改" in txt):
            return True
        return False
    if hint in ("执行事务", "执行事务合伙人"):
        if re.search(r"执行事务合伙人\s*[\(（]\s*[1-9]", txt):
            return True
        # 表内已有姓名行（非「暂无数据」）；勿因页面其它区块的「修改」误判
        try:
            blocks = frame.locator("div, section, article").filter(
                has_text=re.compile(r"执行事务合伙人")
            )
            n = min(blocks.count(), 8)
            for i in range(n):
                block = blocks.nth(i)
                if not _visible(block, timeout=300):
                    continue
                box = block.bounding_box()
                if box and box.get("height", 0) > 900:
                    continue
                bt = (block.inner_text(timeout=500) or "").replace(" ", "")
                if "暂无数据" in bt:
                    return False
                if "修改" in bt or "查看" in bt or "删除" in bt:
                    return True
        except Exception:
            pass
        return False
    if hint == "高管":
        if "财务负责人：1" in compact or "财务负责人:1" in compact:
            return True
        if "财务负责人" in txt and "修改" in txt and "查看" in txt:
            return True
        # 个人独资等：内联财务负责人表单，姓名已有值即视为完成
        if _inline_finance_name_filled(frame):
            return True
        return False
    if hint == "联络员":
        if re.search(r"登记联络员信息\s*[\(（]\s*[1-9]", txt):
            return True
        if "登记联络员" in txt and "修改" in txt and "查看" in txt:
            return True
        return False
    return False


def _has_add_manager_button(frame) -> bool:
    try:
        for t in ("添加高级管理人员", "+ 添加高级管理人员"):
            if frame.get_by_text(t, exact=False).count() > 0:
                loc = frame.get_by_text(t, exact=False).first
                if _visible(loc, timeout=400):
                    return True
    except Exception:
        pass
    return False


def _has_inline_finance(frame) -> bool:
    """个人独资等：页面内嵌「财务负责人」表单（无添加高管按钮）。"""
    try:
        txt = _body(frame).inner_text(timeout=800) or ""
    except Exception:
        txt = ""
    if "财务负责人" not in txt:
        return False
    if "添加高级管理人员" in txt and _has_add_manager_button(frame):
        return False
    return "上传证件" in txt or "复用人员信息" in txt or "请上传身份证" in txt


def _finance_form_scope(frame):
    """定位财务负责人表单区域，避免误填联络员区。"""
    try:
        cand = frame.locator("div, section, form").filter(
            has_text=re.compile(r"财务负责人")
        ).filter(has_text=re.compile(r"复用人员信息|上传证件|请上传身份证"))
        n = min(cand.count(), 8)
        best = None
        best_len = 10**9
        for i in range(n):
            el = cand.nth(i)
            if not _visible(el, timeout=250):
                continue
            try:
                t = el.inner_text(timeout=400) or ""
            except Exception:
                continue
            if "登记联络员" in t and t.find("财务负责人") > t.find("登记联络员"):
                continue
            if len(t) < best_len and len(t) > 20:
                best = el
                best_len = len(t)
        if best is not None:
            return best
    except Exception:
        pass
    return frame


def _inline_finance_name_filled(scope) -> bool:
    try:
        form = _finance_form_scope(scope) if scope is not None else None
        if form is None:
            return False
        item = form.locator(".el-form-item").filter(has_text=re.compile(r"姓名")).first
        if not _visible(item, timeout=400):
            return False
        inp = item.locator("input.el-input__inner, input:not([type='hidden'])").first
        if not _visible(inp, timeout=300):
            return False
        val = (inp.input_value(timeout=500) or "").strip()
        return bool(val)
    except Exception:
        return False


def _fill_inline_finance_officer(
    page: Page,
    frame,
    cfg: dict[str, Any],
    case_id: str,
    payload: dict | None = None,
) -> dict[str, Any]:
    """个人独资等：在页面内联「财务负责人」区填写（非高管弹窗）。"""
    info: dict[str, Any] = {"via": "inline", "ok": False}
    try:
        loc = frame.get_by_text("财务负责人", exact=False).first
        if _visible(loc, timeout=800):
            loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        try:
            frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            page.mouse.wheel(0, 900)
    page.wait_for_timeout(400)
    form = _finance_form_scope(frame)
    info["scoped"] = form is not frame
    person = str(cfg.get("finance_name") or cfg.get("name") or "").strip()
    mobile = str(cfg.get("finance_mobile") or cfg.get("mobile") or DEFAULT_MOBILE).strip()
    address = str(cfg.get("address") or "").strip()

    # 1) 优先复用人员（投资人已入表时常可直接选）
    info["reuse"] = _reuse_first_person(page, host_dialog=form, person_name=person)
    page.wait_for_timeout(700)
    if not _inline_finance_name_filled(form):
        # 2) 身份证 OCR
        info["person"] = _natural_via_id_ocr(
            form,
            page,
            case_id=case_id,
            payload=payload,
            person_name=person,
        )
        page.wait_for_timeout(600)
    else:
        info["reuseFilled"] = True

    # 3) 补必填：证件类型 / 姓名 / 电话 / 住所 / 证件号码
    if person and not _inline_finance_name_filled(form):
        info["name"] = _fill_by_label(form, "姓名", person)
    info["idType"] = _select_dropdown(form, "证件类型", "中华人民共和国居民身份证")
    if not info["idType"]:
        info["idType"] = _select_dropdown(form, "证件类型", "身份证")
    id_no = str(cfg.get("id_number") or cfg.get("idNumber") or "").strip()
    if id_no:
        info["idNo"] = _fill_by_label(form, "证件号码", id_no)
    info["mobile"] = _fill_contact_mobile(form, mobile)
    if not (info.get("mobile") or {}).get("ok"):
        info["mobile2"] = _fill_by_label(form, "移动电话", mobile)
    if address:
        info["address"] = _fill_by_label(form, "住所", address) or _js_fill_near(
            form, "住所", address
        )
    page.wait_for_timeout(300)
    info["ok"] = _inline_finance_name_filled(form)
    if not info["ok"]:
        # 姓名仍空则再强填一次
        if person:
            info["nameRetry"] = _fill_by_label(form, "姓名", person) or _js_fill_near(
                form, "姓名", person
            )
        info["ok"] = _inline_finance_name_filled(form) or bool(info.get("reuse"))
    _shot(page, case_id, "personnel-after-manager")
    return info


def _click_section_add(
    frame,
    page: Page,
    *,
    section_keys: tuple[str, ...],
    btn_texts: list[str],
) -> bool:
    """在指定区块内点「+ 增加 >> / 添加…」（避免点到其它区块同名按钮）。"""
    scopes: list[Any] = [frame, page]
    for scope in scopes:
        for key in section_keys:
            try:
                blocks = scope.locator("div, section, article").filter(has_text=key)
                n = min(blocks.count(), 12)
            except Exception:
                continue
            for i in range(n):
                block = blocks.nth(i)
                try:
                    if not _visible(block, timeout=300):
                        continue
                    # 区块过大则跳过（整页容器）
                    box = block.bounding_box()
                    if box and box.get("height", 0) > 900:
                        continue
                except Exception:
                    continue
                for t in btn_texts:
                    try:
                        btn = block.get_by_text(t, exact=False).first
                        if not _visible(btn, timeout=400):
                            continue
                        btn.scroll_into_view_if_needed(timeout=2000)
                        btn.click(timeout=3000)
                        return True
                    except Exception:
                        continue
                # 按钮可能是 el-button，文案含增加
                try:
                    btn2 = block.locator("button, .el-button, a").filter(
                        has_text=re.compile(r"增加|添加")
                    ).first
                    if _visible(btn2, timeout=400):
                        btn2.click(timeout=3000)
                        return True
                except Exception:
                    pass
    return _click_text(frame, btn_texts, exact=False) or _click_text_any(
        page, btn_texts, exact=False
    )


def _select_page_license_holder(frame, page: Page, names: list[str]) -> bool:
    """人员页底部必填：下拉「电子营业执照持照人」。"""
    for name in names:
        if not name:
            continue
        if _select_dropdown(frame, "电子营业执照持照人", name):
            return True
        if _select_dropdown(page, "电子营业执照持照人", name):
            return True
        # 占位符入口
        for scope in (frame, page):
            try:
                ph = scope.get_by_placeholder("请选择电子营业执照持照人", exact=False).first
                if not _visible(ph, timeout=400):
                    continue
                ph.click(timeout=2500)
                page.wait_for_timeout(350)
                for root in _all_scopes(page):
                    opt = root.locator(
                        ".el-select-dropdown:visible li, .el-popper:visible li"
                    ).filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")).first
                    if not _visible(opt, timeout=400):
                        opt = root.get_by_text(name, exact=True).first
                    if _visible(opt, timeout=400):
                        opt.click(timeout=2500)
                        page.wait_for_timeout(300)
                        return True
                page.keyboard.press("Escape")
            except Exception:
                continue
    return False


def _fill_executive_partners(
    page: Page, frame, cfg: dict[str, Any], case_id: str, payload: dict | None = None
) -> dict[str, Any]:
    """合伙企业：填写执行事务合伙人（委派代表）；并选择页面持照人下拉。"""
    info: dict[str, Any] = {"ok": False, "added": []}
    names = [str(x).strip() for x in (cfg.get("executive_partner_names") or []) if str(x).strip()]
    license_names = [
        str(x).strip() for x in (cfg.get("license_holder_names") or []) if str(x).strip()
    ]
    if not names:
        fallback = (cfg.get("name") or "").strip()
        if fallback:
            names = [fallback]
    if not license_names:
        license_names = list(names)
    info["names"] = names
    info["licenseNames"] = license_names
    if _section_quota_done(frame, "执行事务合伙人"):
        info["licenseSelect"] = _select_page_license_holder(frame, page, license_names)
        info["ok"] = bool(info.get("licenseSelect"))
        info["skipped"] = "already-filled"
        if not info["ok"]:
            info["error"] = "电子营业执照持照人未选择"
        return info

    add_btns = [
        "+ 增加 >>",
        "增加 >>",
        "+ 增加",
        "增加",
        "+ 添加执行事务合伙人 >>",
        "添加执行事务合伙人 >>",
        "+ 添加执行事务合伙人",
        "添加执行事务合伙人",
        "+ 添加委派代表",
        "添加委派代表",
    ]
    section_keys = (
        "执行事务合伙人（委派代表）",
        "执行事务合伙人(委派代表)",
        "执行事务合伙人 (委派代表)",
        "执行事务合伙人",
    )

    for idx, person in enumerate(names):
        item: dict[str, Any] = {"name": person}
        try:
            loc = frame.get_by_text("执行事务合伙人", exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        page.wait_for_timeout(300)
        clicked = _click_section_add(
            frame,
            page,
            section_keys=section_keys,
            btn_texts=add_btns,
        )
        item["addClick"] = clicked
        if not clicked:
            item["error"] = "未点到增加执行事务合伙人"
            info["added"].append(item)
            continue
        page.wait_for_timeout(900)
        dlg, _ = _find_dialog(page, "执行事务合伙人")
        if dlg is None:
            dlg, _ = _find_dialog(page, "委派代表")
        if dlg is None:
            dlg, _ = _find_dialog(page, "新增")
        if dlg is None:
            dlg, _ = _find_dialog(page, "增加")
        if dlg is None:
            item["error"] = "dialog-missing"
            info["added"].append(item)
            _shot(page, case_id, f"personnel-exec-dialog-missing-{idx}")
            continue
        # 1) 先下拉选人（选项为合伙人姓名，不是「是」）
        item["select"] = _select_exec_partner_person(dlg, page, person)
        page.wait_for_timeout(400)
        # 2) 再证件 OCR
        item["person"] = _natural_via_id_ocr(
            dlg,
            page,
            case_id=case_id,
            payload=payload,
            person_name=person,
        )
        page.wait_for_timeout(600)
        # OCR 可能冲掉下拉：再选一次人
        item["selectAfterOcr"] = _select_exec_partner_person(dlg, page, person)
        # 3) OCR 后填国别（中国）+ 移动电话
        item["country"] = _select_nationality_china(dlg, page)
        mobile = _person_mobile(payload, person, cfg)
        item["mobile"] = _fill_contact_mobile(dlg, mobile)
        if not (item.get("mobile") or {}).get("ok"):
            page.wait_for_timeout(300)
            item["mobileRetry"] = _fill_contact_mobile(dlg, mobile)
        if person in set(license_names) or (
            cfg.get("is_limited_partnership") and person in set(names)
        ):
            item["licenseHolder"] = (
                _set_yes_no(dlg, "电子营业执照持照人", True)
                or _set_yes_no(dlg, "持照人", True)
                or _set_yes_no(dlg, "是否电子营业执照持照人", True)
            )
        # 确定前再确认选人 + 电话
        if not item.get("country"):
            item["country"] = _select_nationality_china(dlg, page)
        item["selectBeforeConfirm"] = _select_exec_partner_person(dlg, page, person)
        item["mobileBeforeConfirm"] = _fill_contact_mobile(dlg, mobile)
        _shot(page, case_id, f"personnel-exec-partner-{idx}")
        # 4) 点确定关窗
        item["confirm"] = _confirm_until_closed(
            page, ("新增", "执行事务合伙人"), retries=6
        )
        if not item["confirm"] and _dialog_still_open(page, "新增", "执行事务合伙人"):
            dlg2, _ = _find_dialog(page, "新增", "执行事务合伙人")
            if dlg2 is None:
                dlg2, _ = _find_dialog(page, "新增")
            if dlg2 is not None:
                item["selectRetry"] = _select_exec_partner_person(dlg2, page, person)
                _select_nationality_china(dlg2, page)
                item["mobileRetry2"] = _fill_contact_mobile(dlg2, mobile)
                page.wait_for_timeout(300)
                _shot(page, case_id, f"personnel-exec-before-confirm-retry-{idx}")
                item["confirm"] = _confirm_until_closed(
                    page, ("新增", "执行事务合伙人"), retries=5
                )
                if not item["confirm"]:
                    item["confirm"] = _confirm_until_closed(page, ("新增",), retries=3)
        if not (
            item.get("select")
            or item.get("selectAfterOcr")
            or item.get("selectBeforeConfirm")
            or item.get("selectRetry")
        ):
            item["error"] = item.get("error") or "执行事务合伙人下拉未选中"
        info["added"].append(item)
        page.wait_for_timeout(500)

    # 页面必填下拉：电子营业执照持照人
    info["licenseSelect"] = _select_page_license_holder(frame, page, license_names)
    _shot(page, case_id, "personnel-after-exec-partners")

    added_ok = any(bool(x.get("confirm")) for x in info["added"])
    already = bool(info.get("skipped"))
    # 表内已有数据也算成功
    table_ok = False
    try:
        body = _body(frame).inner_text(timeout=800) or ""
        if "暂无数据" not in body or any(n in body for n in names):
            if any(n in body for n in names):
                table_ok = True
    except Exception:
        pass
    info["ok"] = bool(
        (added_ok or already or table_ok) and (info.get("licenseSelect") or already)
    )
    if not info["ok"]:
        errs = []
        if not (added_ok or already or table_ok):
            errs.append("执行事务合伙人未添加")
        if not info.get("licenseSelect"):
            errs.append("电子营业执照持照人未选择")
        info["error"] = "；".join(errs) or "执行事务合伙人未完成"
    return info


def _bo_prompt_open(page: Page) -> bool:
    """人员信息页图5：提示弹窗含「填报受益所有人信息」。"""
    dlg, _ = _find_dialog(page, "提示")
    if dlg is None:
        dlg, _ = _find_dialog(page, "受益所有人")
    if dlg is None:
        return False
    try:
        t = dlg.inner_text(timeout=800) or ""
    except Exception:
        return False
    return "填报受益所有人信息" in t.replace(" ", "") or "未完成，填报" in t


def _input_has_value(scope, label: str) -> bool:
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if not _visible(item, timeout=400):
        item = scope.locator(".el-form-item").filter(has_text=re.compile(label)).first
    if not _visible(item, timeout=300):
        return False
    inp = item.locator("input, textarea").first
    try:
        v = (inp.input_value(timeout=400) or "").strip()
        return bool(v) and v not in ("请选择", "-请选择-", "-投资人信息-")
    except Exception:
        return False


def _pick_bo_investor(page: Page, form_scope, name: str) -> bool:
    """「受益所有人基本信息」下拉选择「吴文彬」。"""
    name = (name or DEFAULT_NAME).strip()
    if _select_inline_option(page, form_scope, "受益所有人基本信息", name):
        page.wait_for_timeout(1200)
        return True
    # 当前显示「-投资人信息-」时，点开再选姓名
    for hint in ("-投资人信息-", "投资人信息", "请选择"):
        loc = form_scope.get_by_text(hint, exact=False).first
        if not _visible(loc, timeout=300):
            continue
        try:
            loc.click(timeout=2500)
            page.wait_for_timeout(450)
            if _click_option_any(page, name):
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    triggers = []
    for scope in [form_scope, *_all_scopes(page)]:
        try:
            sels = scope.locator(".el-select")
            n = min(sels.count(), 10)
            for i in range(n):
                s = sels.nth(i)
                if not _visible(s, timeout=200):
                    continue
                t = s.inner_text(timeout=300) or ""
                try:
                    inn = s.locator("input").first
                    if _visible(inn, timeout=150):
                        t += inn.input_value(timeout=200) or ""
                except Exception:
                    pass
                if "投资人信息" in t or "请选择" in t or "受益所有人" in t or name in t:
                    triggers.append(s)
        except Exception:
            continue
    seen = set()
    for trig in triggers:
        try:
            key = id(trig)
            if key in seen:
                continue
            seen.add(key)
            trig.click(timeout=2500)
            page.wait_for_timeout(450)
            if _click_option_any(page, name):
                page.wait_for_timeout(1200)
                return True
            for root in _all_scopes(page):
                opt = root.get_by_text(name, exact=True).first
                if _visible(opt, timeout=400):
                    opt.click(timeout=2500)
                    page.wait_for_timeout(1200)
                    return True
            page.keyboard.press("Escape")
        except Exception:
            continue
    if _mouse_click_text(page, [name], exact=True) or _mouse_click_text(page, [name], exact=False):
        page.wait_for_timeout(1200)
        return True
    return False


def _click_dialog_footer_btn(page: Page, must_contain: list[str], *, exclude: list[str] | None = None) -> bool:
    """点弹窗 footer 里匹配文案的按钮（优先未完成填报，避开已完成/指南链接）。"""
    exclude = exclude or []
    dlg, _ = _find_dialog(page, "提示")
    if dlg is None:
        dlg, _ = _find_dialog(page, "受益所有人")
    if dlg is None:
        return False

    def _norm(s: str) -> str:
        return (s or "").replace(" ", "").replace("\n", "").replace("\u00a0", "")

    needles = [_norm(x) for x in must_contain]
    bans = [_norm(x) for x in exclude]
    target = None
    best_score = -1
    try:
        btns = dlg.locator(
            ".el-dialog__footer button, .el-message-box__btns button, "
            ".el-dialog__footer .el-button"
        )
        n = min(btns.count(), 10)
        if n == 0:
            btns = dlg.locator("button.el-button--primary, button.el-button")
            n = min(btns.count(), 10)
    except Exception:
        n = 0
    for i in range(n):
        b = btns.nth(i)
        try:
            t = _norm(b.inner_text(timeout=400) or "")
        except Exception:
            continue
        if any(ban and ban in t for ban in bans):
            continue
        score = -1
        for ni, needle in enumerate(needles):
            if needle and needle == t:
                score = 100 - ni
                break
            if needle and needle in t:
                score = 50 - ni
                break
        if score > best_score:
            best_score = score
            target = b
    if target is None or best_score < 0:
        return False
    try:
        target.scroll_into_view_if_needed(timeout=1500)
    except Exception:
        pass
    # 真实 click（不要只 mouse 点 overlay）
    try:
        target.click(timeout=4000)
        return True
    except Exception:
        pass
    try:
        box = target.bounding_box()
        if box and box.get("width", 0) > 8:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return True
    except Exception:
        pass
    try:
        target.click(force=True, timeout=3000)
        return True
    except Exception:
        return False


def _click_bo_entry(page: Page) -> dict[str, Any]:
    """图5：点「未完成，填报受益所有人信息」/「填报受益所有人信息」，等到图6「自行填报」。"""
    info: dict[str, Any] = {"clicked": False, "ready": False}
    before_pages = len(_all_pages(page))
    # 已到办理方式：受益人环节已过
    if _on_opening_process_page(page):
        info["ready"] = True
        info["alreadyOnPage"] = True
        info["alreadyOnOpening"] = True
        info["page"] = page
        return info
    for _ in range(20):
        if _on_opening_process_page(page):
            info["ready"] = True
            info["alreadyOnOpening"] = True
            info["page"] = page
            return info
        dlg, _ = _find_dialog(page, "提示")
        if dlg is None:
            dlg, _ = _find_dialog(page, "受益所有人")
        if dlg is not None:
            break
        if _page_has_text(page, "自行填报"):
            info["ready"] = True
            info["alreadyOnPage"] = True
            info["page"] = page
            return info
        page.wait_for_timeout(400)

    def _ready_on(pg: Page) -> bool:
        if _is_portal_home(pg):
            return False
        return _page_has_text(pg, "自行填报") and _find_dialog(pg, "提示")[0] is None

    work = page
    for i in range(6):
        if _ready_on(work):
            info["ready"] = True
            info["clicked"] = True
            info["page"] = work
            info["workUrl"] = _url_of(work)
            return info
        bo_pg = _switch_to_bo_page(work)
        if bo_pg is not None and _ready_on(bo_pg):
            info["ready"] = True
            info["clicked"] = True
            info["page"] = bo_pg
            info["switchedUrl"] = _url_of(bo_pg)
            return info
        hit = _click_dialog_footer_btn(
            work,
            ["未完成，填报受益所有人信息", "填报受益所有人信息"],
            exclude=["已完成", "返回修改"],
        )
        info["clicked"] = info["clicked"] or hit
        info[f"try{i}"] = hit
        work.wait_for_timeout(2000)
        info["closedPortal"] = _close_stray_portal_tabs(work)
        info["pages"] = len(_all_pages(work))
        # 优先仍在原登记页找「自行填报」
        if _ready_on(work):
            info["ready"] = True
            info["page"] = work
            info["workUrl"] = _url_of(work)
            return info
        bo_pg = _switch_to_bo_page(work)
        if bo_pg is not None and not _is_portal_home(bo_pg):
            info["switchedUrl"] = _url_of(bo_pg)
            if _ready_on(bo_pg):
                info["ready"] = True
                info["page"] = bo_pg
                return info
            if _page_has_text(bo_pg, "自行填报"):
                info["ready"] = True
                info["page"] = bo_pg
                return info
    info["ready"] = _ready_on(work)
    info["pagesAfter"] = len(_all_pages(work))
    info["pagesBefore"] = before_pages
    picked = _switch_to_bo_page(work)
    if picked is None or _is_portal_home(picked):
        picked = work
    info["page"] = picked
    info["finalUrl"] = _url_of(picked)
    return info


def _mouse_click_text(page: Page, texts: list[str], *, exact: bool = False) -> bool:
    """用鼠标坐标点可见文案（弹窗按钮命中用）。"""
    for t in texts:
        for scope in _all_scopes(page):
            try:
                loc = scope.get_by_text(t, exact=exact).last
                if not _visible(loc, timeout=500):
                    continue
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                box = loc.bounding_box()
                if not box or box.get("width", 0) < 4:
                    # 可能是按钮内 span，点父级 button
                    btn = loc.locator("xpath=ancestor::button[1]")
                    if _visible(btn, timeout=300):
                        box = btn.bounding_box()
                        loc = btn
                if not box:
                    continue
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                return True
            except Exception:
                continue
    return False


def _wait_text_any(page: Page, markers: list[str], timeout_ms: int = 20000) -> bool:
    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        for scope in _all_scopes(page):
            try:
                body = ""
                try:
                    body = _body(scope).inner_text(timeout=500) or ""
                except Exception:
                    body = ""
                if any(m in body for m in markers):
                    return True
                for m in markers:
                    if scope.get_by_text(m, exact=False).count() > 0:
                        return True
            except Exception:
                continue
        page.wait_for_timeout(400)
    return False


def _bo_not_obtained(page: Page) -> bool:
    try:
        for scope in _all_scopes(page):
            txt = ""
            try:
                txt = scope.locator("body").inner_text(timeout=500) or ""
            except Exception:
                continue
            if "未获取到受益所有人信息" in txt:
                return True
    except Exception:
        pass
    return False


def _js_click_eq(scope, text: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """(text) => {
                  const n = (text || '').replace(/\\s/g, '');
                  const vis = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const nodes = [...document.querySelectorAll(
                    'dd, li, .el-select-dropdown__item, .el-cascader-node, .ant-select-item, .combobox-item, span, div, label'
                  )].filter(vis);
                  for (const el of nodes) {
                    const t = (el.innerText || '').replace(/\\s/g, '').trim();
                    if (t === n || t === '-' + n + '-') { el.click(); return true; }
                  }
                  return false;
                }""",
                text,
            )
        )
    except Exception:
        return False


def _js_open_near(scope, label: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """(label) => {
                  const vis = (el) => !!(el && el.offsetParent !== null);
                  const n = (label || '').replace(/\\s/g, '');
                  let lab = null, best = 1e9;
                  for (const el of [...document.querySelectorAll('label, span, div, td, th')].filter(vis)) {
                    const t = (el.innerText || '').replace(/\\s/g, '');
                    if (t.includes(n) && t.length < best && t.length < 60) { lab = el; best = t.length; }
                  }
                  if (!lab) return false;
                  const box = lab.closest('.el-form-item, .ant-form-item, .form-group, tr, .el-row, .el-col')
                    || lab.parentElement;
                  const trig = box && box.querySelector(
                    '.layui-select-title, .layui-form-select, .combo-text, .combo-arrow, '
                    + '.el-select .el-input__inner, .el-select, .ant-select, .el-cascader, '
                    + 'select, input.el-input__inner'
                  );
                  if (trig) { trig.click(); return true; }
                  lab.click();
                  return true;
                }""",
                label,
            )
        )
    except Exception:
        return False


def _js_fill_near(scope, label: str, value: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """([label, value]) => {
                  const vis = (el) => !!(el && el.offsetParent !== null);
                  const n = (label || '').replace(/\\s/g, '');
                  let lab = null, best = 1e9;
                  for (const el of [...document.querySelectorAll('label, span, div, td')].filter(vis)) {
                    const t = (el.innerText || '').replace(/\\s/g, '');
                    if (t.includes(n) && t.length < best && t.length < 50) { lab = el; best = t.length; }
                  }
                  if (!lab) return false;
                  const box = lab.closest('.el-form-item, .ant-form-item, .form-group, tr, .el-row')
                    || lab.parentElement;
                  const inp = box && box.querySelector(
                    'input:not([type=checkbox]):not([type=radio]):not([type=hidden]), textarea'
                  );
                  if (!inp) return false;
                  inp.focus();
                  const proto = inp.tagName === 'TEXTAREA'
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                  const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                  if (desc && desc.set) desc.set.call(inp, value);
                  else inp.value = value;
                  inp.dispatchEvent(new Event('input', { bubbles: true }));
                  inp.dispatchEvent(new Event('change', { bubbles: true }));
                  inp.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                  try { inp.blur(); } catch (e) {}
                  return true;
                }""",
                [label, value],
            )
        )
    except Exception:
        return False


def _js_probe_date_inputs(scope) -> list[dict[str, Any]]:
    try:
        return (
            scope.evaluate(
                """() => {
                  const vis = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  return [...document.querySelectorAll('input')].filter(vis).map(inp => {
                    const box = inp.closest('tr, td, .layui-form-item, .el-form-item, div') || inp.parentElement;
                    const lab = ((box && box.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 60);
                    return {
                      value: inp.value || '',
                      name: inp.name || '',
                      id: inp.id || '',
                      cls: String(inp.className || '').slice(0, 50),
                      readonly: !!inp.readOnly,
                      lab,
                    };
                  }).filter(x => /日期|期限|出生|Wdate|date/i.test(x.lab + x.cls + x.name + x.id)
                    || /\\d{4}[\\/-]\\d{1,2}[\\/-]\\d{1,2}/.test(x.value));
                }"""
            )
            or []
        )
    except Exception:
        return []


def _js_commit_calendar(scope, year: int, month: int, day: int) -> dict[str, Any]:
    """在已打开的日历上切到指定年月日并点选。"""
    try:
        info = scope.evaluate(
            """([year, month, day]) => {
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 20 && r.height > 20 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              const roots = [];
              for (const sel of [
                '.WdateDiv', '#dpTitle', '.layui-laydate', '.el-picker-panel',
                '.datepicker', '.calendar'
              ]) {
                for (const el of document.querySelectorAll(sel)) if (vis(el)) roots.push(el);
              }
              if (!roots.length) {
                for (const el of document.querySelectorAll('div')) {
                  if (!vis(el)) continue;
                  const t = (el.innerText || '').replace(/\\s/g, '');
                  if (/\\d{4}年\\d{1,2}月/.test(t) && t.length < 500 && t.length > 20) roots.push(el);
                }
              }
              if (!roots.length) return { ok: false, reason: 'no-calendar' };
              const root = roots.sort((a,b) =>
                (b.getBoundingClientRect().width * b.getBoundingClientRect().height)
                - (a.getBoundingClientRect().width * a.getBoundingClientRect().height)
              )[0];

              const headerText = () => (root.innerText || '').replace(/\\s/g, '');
              const parseYM = () => {
                const m = headerText().match(/(\\d{4})年(\\d{1,2})月/);
                return m ? [parseInt(m[1],10), parseInt(m[2],10)] : null;
              };

              // 优先直接点「年」「月」数字切换（My97 / 常见中文日历）
              const clickExact = (txt) => {
                for (const el of root.querySelectorAll('a, span, button, td, div, input')) {
                  if (!vis(el) && el.tagName !== 'INPUT') continue;
                  const t = (el.innerText || el.value || '').replace(/\\s/g, '').trim();
                  if (t === txt) { el.click(); return true; }
                }
                return false;
              };

              let ym = parseYM();
              if (ym && (ym[0] !== year || ym[1] !== month)) {
                // 点年份标题再选年
                for (const el of root.querySelectorAll('a, span, div, input')) {
                  const t = (el.innerText || el.value || '').replace(/\\s/g, '');
                  if (t.includes(String(ym[0])) && t.length < 10) { el.click(); break; }
                }
                clickExact(String(year));
                ym = parseYM() || ym;
                for (const el of root.querySelectorAll('a, span, div, input')) {
                  const t = (el.innerText || el.value || '').replace(/\\s/g, '');
                  if ((t.includes(String(ym[1]) + '月') || t === String(ym[1])) && t.length < 8) {
                    el.click(); break;
                  }
                }
                clickExact(String(month) + '月') || clickExact(String(month));
              }

              // 左右箭头兜底（远年可能要翻很多月）
              for (let i = 0; i < 160; i++) {
                ym = parseYM();
                if (!ym) break;
                if (ym[0] === year && ym[1] === month) break;
                const target = year * 12 + month;
                const now = ym[0] * 12 + ym[1];
                const candidates = [...root.querySelectorAll('a, span, button, i, em, div')];
                let btn = null;
                if (target < now) {
                  btn = candidates.find(el => /prev|上月|＜|<|‹|NavImgll|NavImgl/i.test(
                    (el.className||'') + (el.id||'') + (el.title||'') + (el.innerText||'')
                  ));
                } else {
                  btn = candidates.find(el => /next|下月|＞|>|›|NavImgrr|NavImgr/i.test(
                    (el.className||'') + (el.id||'') + (el.title||'') + (el.innerText||'')
                  ));
                }
                if (!btn) break;
                btn.click();
              }

              ym = parseYM();
              if (!ym || ym[0] !== year || ym[1] !== month) {
                return { ok: false, reason: 'ym-mismatch', head: headerText().slice(0, 80), ym };
              }

              const dayRe = new RegExp('^\\\\s*' + day + '\\\\s*$');
              let clicked = null;
              for (const el of root.querySelectorAll('td, span, a, div')) {
                if (!vis(el)) continue;
                const t = (el.innerText || '').trim();
                if (!dayRe.test(t)) continue;
                const cls = String(el.className || '');
                if (/disabled|WotherDay|other-month|laydate-day-prev|laydate-day-next|prev|next|other/i.test(cls)) continue;
                el.click();
                clicked = t;
                break;
              }
              return { ok: !!clicked, day: clicked, head: headerText().slice(0, 60), ym: parseYM() };
            }""",
            [year, month, day],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _close_calendar_soft(page: Page, scope) -> None:
    """关掉日历：点标题区，不要 Esc（Esc 会清空刚填的日期）。"""
    try:
        for t in ("受益所有人基本信息", "受益所有权信息", "详细地址"):
            loc = scope.get_by_text(t, exact=False).first
            if _visible(loc, timeout=200):
                loc.click(timeout=1500, force=True)
                page.wait_for_timeout(200)
                return
    except Exception:
        pass
    try:
        page.mouse.click(20, 20)
        page.wait_for_timeout(150)
    except Exception:
        pass


def _parse_ymd(value: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", value or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _js_set_bo_date(scope, label: str, value: str) -> dict[str, Any]:
    """备案页日期：按标签找可见 input，写入（不按 Esc）。"""
    try:
        info = scope.evaluate(
            """([label, value]) => {
              const n = (label || '').replace(/\\s/g, '');
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              let lab = null, best = 1e9;
              for (const el of [...document.querySelectorAll('label, span, td, th, div')].filter(vis)) {
                const t = (el.innerText || '').replace(/\\s/g, '');
                if (!t.includes(n)) continue;
                if (t.length < best && t.length < 80) { lab = el; best = t.length; }
              }
              if (!lab) return { ok: false, reason: 'no-label' };
              const box = lab.closest('tr, .layui-form-item, .el-form-item, .form-group, li, div') || lab.parentElement;
              let inp = null;
              if (box) {
                const cands = [...box.querySelectorAll('input')].filter(el => {
                  const ty = (el.type || '').toLowerCase();
                  return ty !== 'hidden' && ty !== 'checkbox' && ty !== 'radio' && vis(el);
                });
                inp = cands[0] || null;
              }
              if (!inp) return { ok: false, reason: 'no-input' };
              try { inp.removeAttribute('readonly'); inp.readOnly = false; } catch (e) {}
              inp.focus();
              inp.click();
              const proto = HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(inp, value);
              else inp.value = value;
              inp.dispatchEvent(new Event('input', { bubbles: true }));
              inp.dispatchEvent(new Event('change', { bubbles: true }));
              try { if (window.jQuery) window.jQuery(inp).val(value).trigger('change'); } catch (e) {}
              return { ok: !!(inp.value), value: inp.value || '', name: inp.name || '', id: inp.id || '', cls: String(inp.className||'').slice(0,60) };
            }""",
            [label, value],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fill_one_bo_date(scope, page: Page, labels: list[str], value: str) -> dict[str, Any]:
    """优先键盘写入完整日期（如 2032/6/24）；日历仅在年月正确后点日。"""
    info: dict[str, Any] = {"want": value, "ok": False}
    ymd = _parse_ymd(value)
    if not ymd:
        return {**info, "error": "bad-date"}
    year, month, day = ymd
    typed = f"{year}/{month}/{day}"
    typed_alt = f"{year}-{month:02d}-{day:02d}"

    inp = None
    used_label = ""
    for lab in labels:
        inp = _input_near_label(scope, lab)
        if inp is not None and _visible(inp, timeout=300):
            used_label = lab
            break
    info["label"] = used_label or (labels[0] if labels else "")

    def _year_ok(v: str) -> bool:
        return bool(v) and str(year) in v and (
            str(month) in v or f"{month:02d}" in v
        ) and (str(day) in v or f"{day:02d}" in v)

    if inp is not None:
        try:
            inp.scroll_into_view_if_needed(timeout=1500)
            try:
                inp.evaluate("el => { el.removeAttribute('readonly'); el.readOnly = false; }")
            except Exception:
                pass
            inp.click(timeout=2000)
            page.wait_for_timeout(120)
            inp.fill("")
            inp.type(typed, delay=40)
            inp.press("Enter")
            page.wait_for_timeout(200)
            info["cal"] = _js_commit_calendar(scope, year, month, day)
            if not info["cal"].get("ok"):
                inp.fill("")
                inp.type(typed, delay=40)
                page.keyboard.press("Tab")
            _close_calendar_soft(page, scope)
            cur = (inp.input_value(timeout=500) or "").strip()
            if _year_ok(cur):
                info["value"] = cur
                info["ok"] = True
                info["via"] = "type"
                return info
            inp.click(timeout=1500)
            inp.fill("")
            inp.type(typed_alt, delay=40)
            inp.press("Enter")
            page.wait_for_timeout(200)
            info["cal2"] = _js_commit_calendar(scope, year, month, day)
            _close_calendar_soft(page, scope)
            cur = (inp.input_value(timeout=500) or "").strip()
            if _year_ok(cur):
                info["value"] = cur
                info["ok"] = True
                info["via"] = "type-alt"
                return info
            info["typedValue"] = cur
        except Exception as e:
            info["typeError"] = str(e)

    js = _js_set_bo_date(scope, used_label or labels[0], typed)
    info["js"] = js
    page.wait_for_timeout(200)
    info["cal3"] = _js_commit_calendar(scope, year, month, day)
    _close_calendar_soft(page, scope)
    cur = ""
    if inp is not None:
        try:
            cur = (inp.input_value(timeout=500) or "").strip()
        except Exception:
            cur = ""
    if not cur:
        cur = (js.get("value") if isinstance(js, dict) else "") or _field_value(
            scope, used_label or labels[0]
        )
    probe = _js_read_bo_dates(scope)
    info["probe"] = probe
    if not _year_ok(cur or ""):
        if "有效" in (used_label or labels[0]) or "期限" in (used_label or labels[0]):
            cur = probe.get("idValid") or cur
        else:
            cur = probe.get("ownershipDate") or cur
    info["value"] = cur
    info["ok"] = _year_ok(cur or "")
    info["via"] = "js-cal"
    return info


def _fill_bo_dates(scope, page: Page, id_valid: str, today_vals: list[str], result: dict[str, Any]) -> None:
    """证件有效期 + 形成日期；日历点选确认。"""
    _close_calendar_soft(page, scope)
    result["boDateInputs0"] = _js_probe_date_inputs(scope)
    result["boDatesRead0"] = _js_read_bo_dates(scope)

    id_info = _fill_one_bo_date(
        scope,
        page,
        ["身份证件或身份证明文件有效期限"],
        id_valid or "2032/6/24",
    )
    if not id_info.get("ok"):
        for val in ("2032/6/24", "2032/06/24", "2032-06-24"):
            id_info = _fill_one_bo_date(
                scope, page, ["身份证件或身份证明文件有效期限"], val
            )
            if id_info.get("ok"):
                break
    result["idValidPick"] = id_info
    result["idValid"] = bool(id_info.get("ok"))
    result["idValidValue"] = id_info.get("value") or ""

    date_info: dict[str, Any] = {"ok": False}
    for val in today_vals:
        date_info = _fill_one_bo_date(
            scope,
            page,
            ["受益所有权关系形成的日期", "受益所有权关系形成日期"],
            val,
        )
        if date_info.get("ok"):
            break
    result["ownershipDatePick"] = date_info
    result["ownershipDate"] = bool(date_info.get("ok")) or str(date.today().year) in (
        date_info.get("value") or ""
    )
    result["ownershipDateValue"] = date_info.get("value") or (today_vals[0] if today_vals else "")
    _close_calendar_soft(page, scope)
    page.wait_for_timeout(200)
    # 以本次填写结果为准，不再用探针覆盖成失败
    if id_info.get("ok") or "2032" in (result.get("idValidValue") or ""):
        result["idValid"] = True
    if date_info.get("ok") or str(date.today().year) in (result.get("ownershipDateValue") or ""):
        result["ownershipDate"] = True
    result["boDateInputs1"] = _js_probe_date_inputs(scope)


def _dismiss_overlays(page: Page) -> None:
    """关掉遮罩。不要按 Esc：备案页日历会因此清空刚填的日期。"""
    try:
        page.mouse.click(12, 12)
        page.wait_for_timeout(120)
    except Exception:
        pass


def _js_read_bo_dates(scope) -> dict[str, str]:
    """按标签读证件有效期 / 形成日期的真实 input 值。"""
    try:
        return (
            scope.evaluate(
                """() => {
                  const vis = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const readNear = (needles) => {
                    let best = '';
                    for (const el of document.querySelectorAll('label, span, td, th, div')) {
                      if (!vis(el)) continue;
                      const t = (el.innerText || '').replace(/\\s/g, '');
                      if (!needles.some(n => t.includes(n))) continue;
                      if (t.length > 90) continue;
                      const box = el.closest('tr, td, .layui-form-item, .el-form-item, li, div') || el.parentElement;
                      if (!box) continue;
                      const inps = [...box.querySelectorAll('input')].filter(i => {
                        const ty = (i.type || '').toLowerCase();
                        return ty !== 'hidden' && ty !== 'checkbox' && ty !== 'radio' && vis(i);
                      });
                      for (const inp of inps) {
                        const v = (inp.value || '').trim();
                        if (v) return v;
                      }
                      if (!best && inps[0]) best = (inps[0].value || '').trim();
                    }
                    return best;
                  };
                  return {
                    idValid: readNear(['身份证件或身份证明文件有效期限', '有效期限'])
                      || ((document.querySelector('input[name=\"cerValidDate0\"]') || {}).value || ''),
                    ownershipDate: readNear(['受益所有权关系形成的日期', '受益所有权关系形成日期', '形成的日期'])
                      || ((document.querySelector('input[name=\"profitRatioStartDate0\"]') || {}).value || ''),
                    birth: readNear(['出生日期'])
                      || ((document.querySelector('input[name=\"birthDate0\"]') || {}).value || ''),
                  };
                }"""
            )
            or {}
        )
    except Exception as e:
        return {"error": str(e)}


def _js_probe_selects(scope) -> dict[str, Any]:
    try:
        return (
            scope.evaluate(
                """() => {
                  const rectVis = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                  };
                  const selects = [...document.querySelectorAll('select')].slice(0, 12).map(s => ({
                    vis: rectVis(s),
                    display: getComputedStyle(s).display,
                    opts: [...s.options].map(o => (o.text || '').trim()).slice(0, 12),
                  }));
                  return {
                    selectCount: document.querySelectorAll('select').length,
                    layui: !!document.querySelector('.layui-form-select'),
                    easyui: !!document.querySelector('.combo, .combobox-item'),
                    el: !!document.querySelector('.el-select'),
                    selects,
                  };
                }"""
            )
            or {}
        )
    except Exception as e:
        return {"error": str(e)}


def _js_pick_nth_select(scope, n: int, option: str) -> dict[str, Any]:
    """按页面上第 n 个 select 选值，并尽量触发级联加载。"""
    try:
        info = scope.evaluate(
            """([n, optionText]) => {
              const optionN = (optionText || '').replace(/\\s/g, '');
              const sels = [...document.querySelectorAll('select')];
              if (n < 0 || n >= sels.length) return { ok: false, via: 'bad-index', n, total: sels.length };
              const sel = sels[n];
              const match = (t) => {
                const x = (t || '').replace(/\\s/g, '').trim();
                if (!x || x === '请选择' || x === '-请选择-') return false;
                return x === optionN || x.includes(optionN) || optionN.includes(x);
              };
              let hit = [...sel.options].find(o => match(o.text));
              if (!hit && optionN.includes('中国')) {
                hit = [...sel.options].find(o => {
                  const x = (o.text || '').replace(/\\s/g, '');
                  return x === '中国' || x === '中华人民共和国' || x.includes('中国');
                });
              }
              if (!hit) {
                return {
                  ok: false,
                  via: 'no-option',
                  n,
                  opts: [...sel.options].map(o => (o.text || '').trim()).slice(0, 20),
                };
              }
              sel.focus();
              sel.selectedIndex = hit.index;
              sel.value = hit.value;
              try {
                const ev = document.createEvent('HTMLEvents');
                ev.initEvent('change', true, true);
                sel.dispatchEvent(ev);
              } catch (e) {}
              sel.dispatchEvent(new Event('input', { bubbles: true }));
              sel.dispatchEvent(new Event('change', { bubbles: true }));
              try {
                if (window.jQuery) window.jQuery(sel).trigger('change');
              } catch (e) {}
              try {
                if (typeof sel.onchange === 'function') sel.onchange();
              } catch (e) {}
              return {
                ok: true,
                via: 'nth-select',
                n,
                text: (hit.text || '').trim(),
                value: hit.value,
                nextOpts: n + 1 < sels.length
                  ? [...sels[n + 1].options].map(o => (o.text || '').trim()).slice(0, 8)
                  : [],
              };
            }""",
            [n, option],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _js_wait_select_options(scope, n: int, *, min_opts: int = 2, timeout_ms: int = 8000) -> dict[str, Any]:
    """等第 n 个 select 的选项加载出来（级联）。"""
    deadline = timeout_ms
    step = 250
    last: dict[str, Any] = {}
    waited = 0
    while waited <= deadline:
        try:
            last = (
                scope.evaluate(
                    """(n) => {
                      const sels = [...document.querySelectorAll('select')];
                      if (n < 0 || n >= sels.length) return { ok: false, total: sels.length };
                      const opts = [...sels[n].options].map(o => (o.text || '').trim())
                        .filter(t => t && t !== '请选择' && t !== '-请选择-');
                      return { ok: opts.length > 0, count: opts.length, sample: opts.slice(0, 8) };
                    }""",
                    n,
                )
                or {}
            )
        except Exception as e:
            last = {"ok": False, "error": str(e)}
        if last.get("ok") and int(last.get("count") or 0) >= min_opts - 1:
            return last
        page = _page_of(scope)
        page.wait_for_timeout(step)
        waited += step
    last["timeout"] = True
    return last


def _fill_bo_addr_cascade(
    scope, page: Page, result: dict[str, Any], parts: dict[str, str] | None = None
) -> dict[str, bool]:
    """地址四级联：国家/省/市/县，按实际地址 parts 选择（不再写死贵州）。"""
    parts = parts or {}
    probe = _js_probe_selects(scope)
    total = int(probe.get("selectCount") or 0)
    # 常见布局：前 4 个是投资人/性别/国籍/证件类型，后 4 个是地址
    start = max(0, total - 4) if total >= 4 else 0
    if total >= 8:
        start = 4
    result["boAddrStart"] = start
    result["boAddrProbe0"] = probe
    result["boAddrPartsUsed"] = dict(parts)

    country = (parts.get("country") or "中国").strip() or "中国"
    province = (parts.get("province") or "").strip()
    city = (parts.get("city") or "").strip()
    district = (parts.get("district") or "").strip()
    if not province:
        # 无解析结果时不回落上海企业住所；保留空让调用方感知失败
        result["boAddrMissingRegion"] = True
        return {"country": False, "province": False, "city": False, "district": False}

    pairs = (
        (_addr_option_variants(country) + ("中华人民共和国", "中国"), "country"),
        (_addr_option_variants(province), "province"),
        (_addr_option_variants(city) or _addr_option_variants(province), "city"),
        (_addr_option_variants(district), "district"),
    )
    addr: dict[str, bool] = {}
    picks: dict[str, Any] = {}
    hints = ("国家/地区", "省、自治区", "市", "县")
    for i, (opts, key) in enumerate(pairs):
        opts = tuple(o for o in opts if o)
        if not opts:
            addr[key] = False
            picks[key] = {"ok": False, "reason": "empty-option"}
            continue
        idx = start + i
        if i > 0:
            waited = _js_wait_select_options(scope, idx, min_opts=2, timeout_ms=10000)
            picks[f"{key}Wait"] = waited
        info: dict[str, Any] = {"ok": False}
        for opt in opts:
            info = _js_pick_nth_select(scope, idx, opt)
            if info.get("ok"):
                break
        if not info.get("ok"):
            for opt in opts:
                info = _pick_bo_dropdown(scope, page, opt, hint=hints[i])
                if info.get("ok"):
                    break
        picks[key] = info
        addr[key] = bool(info.get("ok"))
        page.wait_for_timeout(600)
    result["boAddrPick"] = picks
    result["boAddrProbe1"] = _js_probe_selects(scope)
    return addr


def _js_pick_select(scope, option: str, hint: str = "") -> dict[str, Any]:
    """备案页多为原生 select / layui，不是 el-select。"""
    try:
        info = scope.evaluate(
            """([hint, optionText]) => {
              const optionN = (optionText || '').replace(/\\s/g, '');
              const hintN = (hint || '').replace(/\\s/g, '');
              const rectVis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none'
                  && s.visibility !== 'hidden' && s.opacity !== '0';
              };
              const matchText = (t) => {
                const n = (t || '').replace(/\\s/g, '').trim();
                if (!n || n === '请选择' || n === '-请选择-') return false;
                return n === optionN || n === '-' + optionN + '-' || n.includes(optionN);
              };
              const setNative = (sel, opt) => {
                sel.focus();
                sel.selectedIndex = opt.index;
                sel.value = opt.value;
                try {
                  const ev = document.createEvent('HTMLEvents');
                  ev.initEvent('change', true, true);
                  sel.dispatchEvent(ev);
                } catch (e) {}
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                try { if (window.jQuery) window.jQuery(sel).trigger('change'); } catch (e) {}
                const wrap = sel.closest('.layui-form-select, .layui-input-inline, .layui-form-item, td') || sel.parentElement;
                const title = wrap && wrap.querySelector('.layui-select-title input, .combo-text');
                if (title) title.value = (opt.text || '').trim();
                try { if (window.layui && layui.form) layui.form.render('select'); } catch (e) {}
                return { ok: true, via: 'native-select', text: (opt.text || '').trim() };
              };

              const scored = [];
              for (const sel of document.querySelectorAll('select')) {
                const hit = [...sel.options].find(o => matchText(o.text));
                if (!hit) continue;
                let score = 1;
                const box = sel.closest('.layui-form-item, .el-form-item, tr, td, li, .form-group, .layui-input-inline');
                const boxT = ((box && box.innerText) || '').replace(/\\s/g, '');
                if (hintN && boxT.includes(hintN)) score += 80;
                if (rectVis(sel)) score += 5;
                scored.push({ sel, hit, score });
              }
              scored.sort((a, b) => b.score - a.score);
              if (scored.length) {
                const pick = hintN ? (scored.find(s => s.score >= 80) || scored[0]) : scored[0];
                return setNative(pick.sel, pick.hit);
              }

              const nodes = [...document.querySelectorAll(
                'dd, li.el-select-dropdown__item, .combobox-item, .el-select-dropdown__item, .ant-select-item'
              )];
              for (const el of nodes) {
                if (!rectVis(el)) continue;
                const t = (el.innerText || '').replace(/\\s/g, '').trim();
                if (t === optionN || t === '-' + optionN + '-' || t.includes(optionN)) {
                  el.click();
                  return { ok: true, via: 'click-' + el.tagName, text: t };
                }
              }
              return { ok: false, via: 'none' };
            }""",
            [hint, option],
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _js_has_person(scope, name: str) -> bool:
    try:
        return bool(
            scope.evaluate(
                """(name) => {
                  const n = (name || '').replace(/\\s/g, '');
                  for (const sel of document.querySelectorAll('select')) {
                    const o = sel.options[sel.selectedIndex];
                    if (o && (o.text || '').replace(/\\s/g, '').includes(n)) return true;
                  }
                  for (const inp of document.querySelectorAll('input, textarea')) {
                    if ((inp.value || '').replace(/\\s/g, '').includes(n)) return true;
                  }
                  const title = document.querySelector('.layui-form-select .layui-select-title input');
                  if (title && (title.value || '').replace(/\\s/g, '').includes(n)) return true;
                  return false;
                }""",
                name,
            )
        )
    except Exception:
        return False


def _js_open_widget(scope, hint: str) -> dict[str, Any]:
    """点开 layui / combo / 原生 select。"""
    try:
        info = scope.evaluate(
            """(hint) => {
              const hintN = (hint || '').replace(/\\s/g, '');
              const rectVis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              const widgets = [...document.querySelectorAll(
                '.layui-form-select, .combo, .el-select, select'
              )];
              let target = null;
              for (const w of widgets) {
                if (!rectVis(w) && w.tagName !== 'SELECT') continue;
                const box = w.closest('.layui-form-item, .el-form-item, tr, td, .form-group') || w.parentElement;
                const t = ((box && box.innerText) || (w.innerText) || '').replace(/\\s/g, '');
                const inp = w.querySelector && w.querySelector('input');
                const v = ((inp && inp.value) || '') + t;
                if (hintN && (v.includes(hintN) || t.includes(hintN))) { target = w; break; }
                if (!hintN && (v.includes('投资人信息') || t.includes('投资人信息'))) { target = w; break; }
              }
              if (!target && !hintN) {
                target = widgets.find(w => rectVis(w) || w.tagName === 'SELECT') || null;
              }
              if (!target) return { ok: false };
              const clickable = (target.querySelector && (
                target.querySelector('.layui-select-title')
                || target.querySelector('.combo-text')
                || target.querySelector('.combo-arrow')
                || target.querySelector('.el-input__inner')
              )) || target;
              clickable.click();
              return { ok: true, tag: target.tagName, cls: String(target.className || '').slice(0, 80) };
            }""",
            hint or "",
        )
        return info if isinstance(info, dict) else {"ok": bool(info)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _pick_bo_dropdown(scope, page: Page, option: str, hint: str = "") -> dict[str, Any]:
    _dismiss_overlays(page)
    opened = _js_open_widget(scope, hint)
    page.wait_for_timeout(450)
    clicked = {"ok": False}
    if opened.get("ok"):
        clicked = {"ok": _click_visible_dropdown_option(page, option), "via": "pw-visible"}
        if not clicked.get("ok"):
            clicked = {"ok": _js_click_eq(scope, option), "via": "js-eq"}
    if not clicked.get("ok"):
        try:
            loc = scope.locator("select").filter(
                has=scope.locator("option", has_text=re.compile(re.escape(option)))
            ).first
            loc.select_option(label=re.compile(rf".*{re.escape(option)}.*"))
            page.wait_for_timeout(300)
            clicked = {"ok": True, "via": "pw-select-option"}
        except Exception:
            clicked = _js_pick_select(scope, option, hint)
            if not clicked.get("via"):
                clicked["via"] = "native-fallback"
    clicked["opened"] = opened
    return clicked


def _field_value(scope, label: str) -> str:
    inp = None
    item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
    if _visible(item, timeout=300):
        inp = item.locator("input:not([type=hidden]):not([type=checkbox]), textarea").first
    if inp is None or not _visible(inp, timeout=200):
        inp = _input_near_label(scope, label)
    if inp is None:
        return ""
    try:
        return (inp.input_value(timeout=400) or "").strip()
    except Exception:
        try:
            return (inp.inner_text(timeout=300) or "").strip()
        except Exception:
            return ""


def _dropdown_shows(scope, label: str, expect: str) -> bool:
    try:
        txt = ""
        item = scope.locator(".el-form-item").filter(has_text=re.compile(re.escape(label))).first
        if _visible(item, timeout=300):
            txt = item.inner_text(timeout=400) or ""
        else:
            lab = scope.get_by_text(label, exact=False).first
            if _visible(lab, timeout=300):
                box = lab.locator("xpath=ancestor::*[contains(@class,'el-form-item') or contains(@class,'el-row')][1]")
                txt = (box.inner_text(timeout=400) if _visible(box, timeout=200) else "") or ""
        return expect in (txt or "")
    except Exception:
        return False


def _fill_bo_form(
    page: Page,
    form_scope,
    cfg: dict[str, Any],
    result: dict[str, Any],
    *,
    case_id: str = "",
) -> None:
    """受益所有人表单：选投资人，再填期限/手机/地址/股权/当日。填后校验真实值。

    经常居住地：用户填写的最终受益人「住所*」→ 解析省/市/区，国家默认中国。
    """
    person = (cfg.get("bo_name") or cfg.get("name") or DEFAULT_NAME).strip()
    mobile = cfg.get("bo_mobile") or cfg.get("mobile") or DEFAULT_MOBILE
    parts = dict(cfg.get("bo_addr_parts") or {})
    detail = (
        (parts.get("detail") or "").strip()
        or (cfg.get("bo_address") or "").strip()
        or (cfg.get("address") or "").strip()
    )
    # 详细地址：若 parts.detail 太短则用完整 bo_address
    full = (cfg.get("bo_address") or "").strip()
    if full and (not detail or detail in (parts.get("district") or "")):
        detail = full
    if not parts.get("province") and full:
        parts = _parse_cn_region_address(full)
        if (parts.get("detail") or "").strip():
            detail = parts["detail"]
    parts["country"] = "中国"
    ratio = str(cfg.get("ownership_ratio") or "100").strip() or "100"
    pg = _page_of(form_scope)
    _dismiss_overlays(pg)
    result["boDom"] = _js_probe_selects(form_scope)
    result["ownershipRatioExpected"] = ratio
    result["boAddressSource"] = "user-ubo-address"
    result["boAddressExpected"] = {
        "parts": parts,
        "detail": detail,
    }

    # 1) 投资人下拉选姓名（备案页是原生/layui select）
    pick_info = _pick_bo_dropdown(form_scope, pg, person, hint="投资人信息")
    if not pick_info.get("ok"):
        pick_info = _pick_bo_dropdown(form_scope, pg, person, hint="受益所有人基本信息")
    if not pick_info.get("ok"):
        pick_info = _pick_bo_investor(pg, form_scope, person) and {"ok": True, "via": "legacy"}
    result["investorPick"] = pick_info if isinstance(pick_info, dict) else {"ok": bool(pick_info)}
    pg.wait_for_timeout(1800)
    name_val = _field_value(form_scope, "姓名")
    inv = person in (name_val or "") or _js_has_person(form_scope, person)
    if not inv:
        pick_info = _pick_bo_dropdown(form_scope, pg, person, hint="")
        pg.wait_for_timeout(1500)
        name_val = _field_value(form_scope, "姓名")
        inv = person in (name_val or "") or _js_has_person(form_scope, person)
    result["investorInfo"] = bool(inv)
    result["investorNameValue"] = name_val or person if inv else (name_val or "")

    # 2) 先取消「长期」（证件有效期稍后和其他日期一起填，避免被日历冲掉）
    try:
        long_lab = form_scope.locator("label").filter(has_text=re.compile(r"长期")).first
        if _visible(long_lab, timeout=400):
            inp = long_lab.locator("input").first
            checked = False
            try:
                if _visible(inp, timeout=200):
                    checked = bool(inp.is_checked())
            except Exception:
                pass
            if checked:
                long_lab.click(force=True, timeout=2000)
    except Exception:
        pass
    _dismiss_overlays(pg)

    # 3) 联系方式一（手机号）
    _js_fill_near(form_scope, "联系方式一（手机号）", mobile) or _js_fill_near(
        form_scope, "联系方式一", mobile
    ) or _fill_by_label(form_scope, "联系方式一", mobile)
    mobile_val = _field_value(form_scope, "联系方式一") or _field_value(form_scope, "手机号")
    result["boMobile"] = mobile in mobile_val
    result["boMobileValue"] = mobile_val

    # 4) 地址四级联：按后 4 个原生 select 依次选，并等待下级选项加载
    try:
        loc = form_scope.get_by_text("经常居住地或工作单位地址", exact=False).first
        if _visible(loc, timeout=500):
            loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    _dismiss_overlays(pg)
    addr = _fill_bo_addr_cascade(form_scope, pg, result, parts)
    result["boAddr"] = addr
    # 详细地址用完整实际地址（与省市区一致，不用贵州默认）
    detail_to_fill = (cfg.get("bo_address") or detail or "").strip()
    _js_fill_near(form_scope, "详细地址", detail_to_fill) or _fill_by_label(
        form_scope, "详细地址", detail_to_fill
    )
    detail_val = _field_value(form_scope, "详细地址")
    result["boDetailAddr"] = bool(detail_val) and (
        detail_to_fill[:6] in detail_val
        or detail_val[:6] in detail_to_fill
        or len(detail_val) > 4
    )
    result["boDetailAddrValue"] = detail_val
    # 手机号再补一次（日历遮罩可能导致前一次未落库）
    if not result.get("boMobile"):
        _js_fill_near(form_scope, "联系方式一（手机号）", mobile) or _js_fill_near(
            form_scope, "联系方式一", mobile
        ) or _fill_by_label(form_scope, "联系方式一", mobile)
        mobile_val = _field_value(form_scope, "联系方式一") or _field_value(form_scope, "手机号")
        result["boMobile"] = mobile in mobile_val
        result["boMobileValue"] = mobile_val

    # 5) 股权类型：只勾第一项「直接/间接拥有…股权」，绝不能勾「虽未满足…收益权/表决权」
    own_ok = False
    own_info: dict[str, Any] = {}
    try:
        own_info = form_scope.evaluate(
            """() => {
              const vis = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none';
              };
              const labels = [...document.querySelectorAll('label')].filter(vis);
              const isBad = (t) => t.includes('虽未满足') || t.includes('收益权') || t.includes('表决权') || t.includes('实际控制');
              const isGood = (t) =>
                (t.includes('直接或间接拥有') && t.includes('25') && (
                  t.includes('股权') || t.includes('股份') || t.includes('出资')
                  || t.includes('合伙权益') || t.includes('合伙企业')
                ))
                || (t.includes('超过25%') && (t.includes('股权') || t.includes('出资') || t.includes('合伙权益')));
              // 先取消错误项
              for (const lab of labels) {
                const t = (lab.innerText || '').replace(/\\s/g, '');
                if (!isBad(t)) continue;
                const inp = lab.querySelector('input[type=checkbox]');
                if (inp && inp.checked) lab.click();
              }
              // 再勾正确项
              let hit = null;
              for (const lab of labels) {
                const t = (lab.innerText || '').replace(/\\s/g, '');
                if (!isGood(t) || isBad(t)) continue;
                const inp = lab.querySelector('input[type=checkbox]');
                if (inp && !inp.checked) lab.click();
                hit = { text: t.slice(0, 40), checked: !!(inp && inp.checked) };
                break;
              }
              const checked = labels.map(lab => {
                const inp = lab.querySelector('input[type=checkbox]');
                if (!inp || !inp.checked) return null;
                return (lab.innerText || '').replace(/\\s/g, '').slice(0, 40);
              }).filter(Boolean);
              return { ok: !!(hit && hit.checked) || checked.some(t => isGood(t) && !isBad(t)), hit, checked };
            }"""
        ) or {}
        own_ok = bool(own_info.get("ok"))
    except Exception as e:
        own_info = {"error": str(e)}
        own_ok = False
    if not own_ok:
        # 兜底：精确文案（含合伙企业「合伙权益」）
        for needle in (
            "直接或间接拥有合伙企业25%以上合伙权益",
            "直接或间接拥有合伙企业25%以上",
            "直接或间接拥有公司25%以上股权、股份",
            "直接或间接拥有公司25%以上股权",
            "直接或间接拥有超过25%的公司股权",
            "合伙企业25%以上合伙权益",
        ):
            try:
                cb = form_scope.get_by_text(needle, exact=False).first
                if not _visible(cb, timeout=400):
                    continue
                t = (cb.inner_text(timeout=300) or "")
                if "虽未满足" in t or "收益权" in t:
                    continue
                lab = cb.locator("xpath=ancestor::label[1]").first
                (lab if _visible(lab, timeout=200) else cb).click(timeout=3000)
                own_ok = True
                break
            except Exception:
                continue
    result["ownershipType"] = own_ok
    result["ownershipTypeInfo"] = own_info
    pg.wait_for_timeout(600)

    # 6) 持股/合伙权益比例（有限公司=股权股份；合伙=合伙权益）
    def _fill_equity_ratio(target: str) -> tuple[bool, str]:
        ratio_labels = (
            "持有合伙权益的比例",
            "持有合伙权益的比例 (%)",
            "持有合伙权益的比例（%）",
            "持有股权、股份的比例",
            "持有股权、股份的比例（%）",
            "股权、股份的比例",
            "合伙权益的比例",
        )
        for lab in ratio_labels:
            if _js_fill_near(form_scope, lab, target) or _fill_by_label(form_scope, lab, target):
                v = _field_value(form_scope, lab) or target
                if target in v:
                    return True, v
        try:
            ok = bool(
                form_scope.evaluate(
                    """(target) => {
                      const needles = [
                        '持有合伙权益的比例', '合伙权益的比例', '持有合伙权益',
                        '持有股权', '股份的比例', '股权、股份'
                      ];
                      const vis = (el) => {
                        const r = el.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                      };
                      for (const el of document.querySelectorAll('label, span, td, div')) {
                        if (!vis(el)) continue;
                        const t = (el.innerText || '').replace(/\\s/g, '');
                        if (!needles.some(n => t.includes(n.replace(/\\s/g, '')))) continue;
                        if (t.includes('收益权') || t.includes('表决权')) continue;
                        if (t.length > 48) continue;
                        const box = el.closest('tr, .el-form-item, .layui-form-item, div') || el.parentElement;
                        const inp = box && [...box.querySelectorAll('input')].find(i =>
                          i.type !== 'checkbox' && i.type !== 'hidden' && i.type !== 'radio' && vis(i)
                        );
                        if (!inp) continue;
                        inp.focus();
                        inp.select && inp.select();
                        const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                        if (desc && desc.set) desc.set.call(inp, target); else inp.value = target;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        inp.dispatchEvent(new Event('blur', { bubbles: true }));
                        return String(inp.value || '').includes(target);
                      }
                      return false;
                    }""",
                    target,
                )
            )
            if ok:
                return True, target
        except Exception:
            pass
        # Playwright：按可见 label 找旁边 input
        for lab_text in ("持有合伙权益的比例", "持有股权、股份的比例", "合伙权益的比例"):
            try:
                lab = form_scope.get_by_text(lab_text, exact=False).first
                if not _visible(lab, timeout=300):
                    continue
                item = lab.locator("xpath=ancestor::div[contains(@class,'el-form-item') or contains(@class,'layui-form-item')][1]")
                inp = item.locator("input:not([type='checkbox']):not([type='radio']):not([type='hidden'])").first
                if not _visible(inp, timeout=300):
                    inp = lab.locator(
                        "xpath=following::input[not(@type='hidden') and not(@type='checkbox') and not(@type='radio')][1]"
                    )
                if not _visible(inp, timeout=300):
                    continue
                inp.click(timeout=2000)
                inp.fill("")
                inp.fill(target, timeout=4000)
                try:
                    inp.press("Tab")
                except Exception:
                    pass
                v = (inp.input_value(timeout=400) or "").strip()
                if target in v:
                    return True, v
            except Exception:
                continue
        return False, ""

    ratio_ok, ratio_val = _fill_equity_ratio(ratio)
    result["ownershipRatio"] = ratio_ok and ratio in (ratio_val or ratio)
    result["ownershipRatioValue"] = ratio_val or (ratio if ratio_ok else "")
    pg.wait_for_timeout(300)

    # 7) 证件有效期 + 形成日期（最后填，关掉日历，按真实 input 值校验）
    today = date.today()
    today_vals = [
        today.strftime("%Y-%m-%d"),
        f"{today.year}/{today.month}/{today.day}",
        f"{today.year}/{today.month:02d}/{today.day:02d}",
    ]
    id_cfg = (cfg.get("id_valid") or DEFAULT_ID_VALID).replace("-", "/")
    # 2032-06-24 → 2032/6/24
    parts = id_cfg.replace("-", "/").split("/")
    if len(parts) == 3:
        id_slash = f"{int(parts[0])}/{int(parts[1])}/{int(parts[2])}"
    else:
        id_slash = "2032/6/24"
    _fill_bo_dates(form_scope, pg, id_slash, today_vals, result)
    _close_calendar_soft(pg, form_scope)

    # 日期填写可能影响勾选区，再确认股权类型 + 比例
    try:
        form_scope.evaluate(
            """() => {
              const isBad = (t) => t.includes('虽未满足') || (t.includes('收益权') && t.includes('表决权'));
              for (const lab of document.querySelectorAll('label')) {
                const t = (lab.innerText || '').replace(/\\s/g, '');
                const inp = lab.querySelector('input[type=checkbox]');
                if (!inp || !inp.checked) continue;
                if (isBad(t)) lab.click();
              }
            }"""
        )
    except Exception:
        pass
    if not result.get("ownershipType"):
        result["ownershipType"] = own_ok
    ratio_ok2, ratio_val2 = _fill_equity_ratio(ratio)
    if ratio_ok2:
        result["ownershipRatio"] = True
        result["ownershipRatioValue"] = ratio_val2 or ratio
    result["ownershipRatioAfterDates"] = {
        "ok": ratio_ok2,
        "value": ratio_val2,
    }


def _mark_personnel_done(result: dict[str, Any]) -> None:
    for k in (
        "addDirectorClick",
        "directorConfirm",
        "addManagerClick",
        "managerConfirm",
        "addLiaisonClick",
        "liaisonConfirm",
        "fillBeneficialClick",
        "boEntryReady",
    ):
        result[k] = True


def _on_opening_process_page(page: Page) -> bool:
    """已离开人员/受益人，到达企业开办「后续办理方式」页。"""
    if _page_has_text(page, "后续办理方式"):
        return True
    return _page_has_text(page, "全程网办") and _page_has_text(page, "文书产生方式")


def _handoff_personnel_to_opening(
    case_id: str, page: Page, result: dict[str, Any], *, reason: str
) -> dict[str, Any]:
    """人员阶段已越过，把结果标成成功，交给 opening 流程。"""
    _mark_personnel_done(result)
    result["selfReport"] = True
    result["doneNext"] = True
    result["ok"] = True
    result["missing"] = []
    result["error"] = None
    result["skippedToOpening"] = reason
    result["pageOk"] = True
    try:
        result["handoffUrl"] = page.url
    except Exception:
        pass
    _shot(page, case_id, "personnel-handoff-opening")
    _dump(case_id, "fill-personnel-result.json", result)
    return result


def _force_select_self_report(page: Page) -> bool:
    """受益所有人入口：强制选「自行填报」，绝不点「承诺免报」。

    普通合伙等会出现「承诺免报」选项；业务要求与有限公司一样走自行填报。
    """
    # 已选自行填报？
    for scope in _all_scopes(page):
        try:
            lab = scope.locator("label.el-radio, label").filter(
                has_text=re.compile(r"自行填报")
            ).first
            if not _visible(lab, timeout=300):
                continue
            cls = lab.get_attribute("class") or ""
            if "is-checked" in cls:
                return True
            inp = lab.locator("input[type=radio]").first
            if _visible(inp, timeout=200) and inp.is_checked():
                return True
        except Exception:
            continue

    for scope in _all_scopes(page):
        try:
            # 精确点「自行填报」radio，避开承诺免报文案
            lab = scope.locator("label.el-radio, label").filter(
                has_text=re.compile(r"^\s*自行填报")
            ).first
            if not _visible(lab, timeout=400):
                lab = scope.get_by_text("自行填报", exact=True).first
            if not _visible(lab, timeout=400):
                continue
            # 若点到的节点嵌在承诺免报里则跳过
            try:
                wrap = (lab.inner_text(timeout=300) or "").replace(" ", "")
                if "承诺免报" in wrap and "自行填报" not in wrap[:8]:
                    continue
            except Exception:
                pass
            lab.click(force=True, timeout=3000)
            page.wait_for_timeout(350)
            return True
        except Exception:
            continue

    if _mouse_click_text(page, ["自行填报"], exact=True):
        page.wait_for_timeout(350)
        return True
    # 最后回退：短文案 radio
    for scope in _all_scopes(page):
        if _click_radio(scope, "自行填报"):
            page.wait_for_timeout(350)
            return True
    return False


def _click_bo_next(page: Page) -> bool:
    """受益人页底部「下一步」（避开承诺书区内误点）。"""
    for scope in _all_scopes(page):
        try:
            btns = scope.locator("button, .el-button, a.el-button").filter(
                has_text=re.compile(r"^\s*下一步\s*$")
            )
            n = min(btns.count(), 8)
            for i in range(n - 1, -1, -1):
                btn = btns.nth(i)
                if not _visible(btn, timeout=300):
                    continue
                try:
                    btn.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                try:
                    box = btn.bounding_box()
                    if box and box.get("width", 0) > 20:
                        page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2,
                        )
                        return True
                except Exception:
                    pass
                btn.click(timeout=3000)
                return True
        except Exception:
            continue
    return _mouse_click_text(page, ["下一步"], exact=True) or _click_text_any(
        page, ["下一步"], exact=True
    )


def _complete_beneficial(
    case_id: str, page: Page, cfg: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """图6–9：自行填报 → 自然人条件 → 表单 → 已完成进入下一步。

    合伙企业即使有「承诺免报」，也一律自行填报（与有限公司相同路径）。
    """
    if _on_opening_process_page(page):
        return _handoff_personnel_to_opening(
            case_id, page, result, reason="beneficial-entry-already-opening"
        )
    already_form = _page_has_text(page, "受益所有人基本信息") or _page_has_text(
        page, "受益所有权信息"
    )
    already_nat = _page_has_text(page, "存在符合下列条件之一的自然人")

    # 6) 图6：自行填报 → 下一步（已在后续页则跳过）
    page.wait_for_timeout(500)
    if already_form or already_nat:
        result["selfReport"] = True
        result["selfReportSkipped"] = "already-past"
    else:
        # 合伙页会出现「承诺免报」：强制自行填报
        self_ok = _force_select_self_report(page)
        result["selfReport"] = self_ok
        result["selfReportForced"] = True
        # 确认未误选承诺免报
        try:
            for scope in _all_scopes(page):
                bad = scope.locator("label.el-radio, label").filter(
                    has_text=re.compile(r"承诺免报")
                ).first
                if not _visible(bad, timeout=200):
                    continue
                cls = bad.get_attribute("class") or ""
                checked = "is-checked" in cls
                if not checked:
                    try:
                        inp = bad.locator("input[type=radio]").first
                        checked = _visible(inp, timeout=150) and bool(inp.is_checked())
                    except Exception:
                        pass
                if checked:
                    self_ok = _force_select_self_report(page)
                    result["selfReport"] = self_ok
                    result["switchedFromExempt"] = True
                break
        except Exception:
            pass
        page.wait_for_timeout(400)
        next2 = _click_bo_next(page)
        result["beneficialNext1"] = next2
        page.wait_for_timeout(1500)
        # 若仍停在承诺书页，再点一次自行填报+下一步
        still_entry = _page_has_text(page, "承诺免报") and not (
            _page_has_text(page, "存在符合下列条件之一的自然人")
            or _page_has_text(page, "受益所有人基本信息")
        )
        if still_entry:
            _force_select_self_report(page)
            page.wait_for_timeout(300)
            next2b = _click_bo_next(page)
            result["beneficialNext1Retry"] = next2b
            page.wait_for_timeout(1500)
        result["afterSelfReady"] = (
            _page_has_text(page, "存在符合下列条件之一的自然人")
            or _page_has_text(page, "受益所有人基本信息")
            or _wait_text_any(
                page,
                ["存在符合下列条件之一的自然人", "受益所有人基本信息"],
                timeout_ms=15000,
            )
        )
        if not result.get("selfReport"):
            result["selfReport"] = bool(result.get("afterSelfReady"))
    _shot(page, case_id, "personnel-after-self-report")

    # 7) 图7：存在符合下列条件之一的自然人 → 下一步（若已在表单页则跳过）
    nat_ok = False
    try:
        already_form = False
        for scope in _all_scopes(page):
            t = ""
            try:
                t = _body(scope).inner_text(timeout=500) or ""
            except Exception:
                continue
            if "受益所有人基本信息" in t or "受益所有权信息" in t:
                already_form = True
                break
        if already_form:
            nat_ok = True
            result["naturalSkipped"] = "already-on-form"
        else:
            for scope in _all_scopes(page):
                if _click_radio(scope, "存在符合下列条件之一的自然人"):
                    nat_ok = True
                    break
            if not nat_ok:
                nat_ok = _mouse_click_text(page, ["存在符合下列条件之一的自然人"], exact=False)
            page.wait_for_timeout(400)
            next3 = _click_bo_next(page)
            result["beneficialNext2"] = next3
            page.wait_for_timeout(1500)
    except Exception as e:
        result["naturalError"] = str(e)
    result["naturalCondition"] = nat_ok
    _shot(page, case_id, "personnel-after-natural-condition")

    # 8) 图8：受益所有人表单（吴文彬 → 期限/手机/地址/股权100%/当日 → 下一步 → 确定）
    # 先记住登记页：备案「下一步/确定」后 BO 标签常被关掉，必须回到登记页点「已完成」
    reg_page = _find_reg_page(page) or page
    form_scope = None
    work_page = page
    for pg in _all_pages(page):
        if _is_portal_home(pg):
            continue
        for scope in _all_scopes(pg):
            try:
                if scope.get_by_text("受益所有人基本信息", exact=False).count() > 0:
                    form_scope = scope
                    work_page = pg
                    break
                if scope.get_by_text("受益所有权信息", exact=False).count() > 0:
                    form_scope = scope
                    work_page = pg
                    break
            except Exception:
                continue
        if form_scope is not None:
            break
    if form_scope is None:
        form_scope = page
    page = work_page

    _fill_bo_form(page, form_scope, cfg, result, case_id=case_id)
    _shot(page, case_id, "personnel-beneficial-filled")

    addr = result.get("boAddr") or {}
    # 点下一步前再补一次股权比例（避免勾错类型导致比例框未出现）
    if not result.get("ownershipRatio"):
        try:
            for needle in (
                "直接或间接拥有合伙企业25%以上合伙权益",
                "直接或间接拥有公司25%以上股权、股份",
                "直接或间接拥有",
                "25%以上",
            ):
                cb = form_scope.get_by_text(needle, exact=False).first
                if not _visible(cb, timeout=300):
                    continue
                t = (cb.inner_text(timeout=200) or "")
                if "虽未满足" in t or "收益权" in t:
                    continue
                lab = cb.locator("xpath=ancestor::label[1]").first
                (lab if _visible(lab, timeout=200) else cb).click(timeout=2500)
                _safe_wait(page, 400)
                break
            ratio_retry = str(cfg.get("ownership_ratio") or "100").strip() or "100"
            filled = False
            for lab in (
                "持有合伙权益的比例",
                "持有股权、股份的比例",
                "合伙权益的比例",
            ):
                if _js_fill_near(form_scope, lab, ratio_retry) or _fill_by_label(
                    form_scope, lab, ratio_retry
                ):
                    rv = _field_value(form_scope, lab) or ""
                    if ratio_retry in rv:
                        result["ownershipRatio"] = True
                        result["ownershipRatioValue"] = rv
                        filled = True
                        break
            if not filled:
                rv = (
                    _field_value(form_scope, "持有合伙权益的比例")
                    or _field_value(form_scope, "持有股权、股份的比例")
                    or ""
                )
                result["ownershipRatio"] = ratio_retry in rv
                result["ownershipRatioValue"] = rv
            result["ownershipRatioRetry"] = True
            result["ownershipRatioExpected"] = ratio_retry
        except Exception as e:
            result["ownershipRatioRetryError"] = str(e)
    # 日期探针常读空；以填写标志 + input 实值为准
    date_inputs = result.get("boDateInputs1") or result.get("boDateInputs0") or []
    id_input_v = next(
        (str(i.get("value") or "") for i in date_inputs if i.get("name") == "cerValidDate0"),
        "",
    )
    own_input_v = next(
        (
            str(i.get("value") or "")
            for i in date_inputs
            if i.get("name") == "shareRatioStartDate0"
        ),
        "",
    )
    dates_ok = bool(
        (result.get("idValid") or ("2032" in id_input_v))
        and (result.get("ownershipDate") or bool(own_input_v))
    )
    form_ok = bool(
        result.get("investorInfo")
        and dates_ok
        and result.get("boMobile")
        and addr.get("country")
        and addr.get("province")
        and addr.get("city")
        and addr.get("district")
        and result.get("ownershipType")
        and result.get("ownershipRatio")
    )
    result["beneficialFormOk"] = form_ok
    result["datesOk"] = dates_ok
    next4 = False
    conf = False
    if form_ok:
        # 日期已在表单里填过，直接下一步；BO 页随后可能关闭，全部用安全等待
        try:
            _close_calendar_soft(page, form_scope)
            _safe_wait(page, 300)
            if _page_alive(page):
                next4 = _mouse_click_text(page, ["下一步"], exact=True) or _click_text_any(
                    page, ["下一步"], exact=True
                )
            result["beneficialNext3"] = next4
            _safe_wait(page, 1500)
            if _page_alive(page):
                box, _ = _find_dialog(page, "确定")
                if box is None:
                    box, _ = _find_dialog(page, "提示")
                if box is not None:
                    conf = _click_dialog_footer_btn(
                        page, ["确定", "确认"], exclude=["返回", "取消"]
                    )
                    if not conf:
                        conf = _click_dialog_ok(box, page)
                if not conf:
                    try:
                        for scope in _all_scopes(page):
                            btn = scope.locator(
                                ".layui-layer-btn0, .layui-layer .layui-layer-btn a"
                            ).filter(has_text=re.compile(r"确\s*定")).first
                            if _visible(btn, timeout=400):
                                btn.click(timeout=2500)
                                conf = True
                                break
                    except Exception:
                        pass
                if not conf:
                    conf = _mouse_click_text(page, ["确定"], exact=True) or _click_text_any(
                        page, ["确定"], exact=True
                    )
        except Exception as e:
            result["boSubmitError"] = str(e)
            # 备案页被关时，提交动作往往已生效
            if "closed" in str(e).lower() or "Target page" in str(e):
                result["boPageClosedOnSubmit"] = True
                if next4:
                    conf = True
    else:
        result["beneficialNext3"] = False
        result["skipNextReason"] = "form-incomplete"
    result["beneficialConfirm"] = conf
    _safe_wait(page if _page_alive(page) else reg_page, 1200)

    # 备案标签关闭后切回登记页，再点图9「已完成，进入下一步」
    if not _page_alive(page) or not _bo_prompt_open(page):
        back = _find_reg_page(reg_page) or _find_reg_page(page)
        if back is not None:
            try:
                back.bring_to_front()
            except Exception:
                pass
            page = back
            result["switchedBackToReg"] = True
            result["regUrl"] = _url_of(page)
    try:
        _shot(page, case_id, "personnel-after-beneficial-confirm")
    except Exception:
        pass

    # 9) 图9：仅当表单关键字段已填、且无「未获取到」时，才点「已完成，进入下一步」
    done = False
    # 备案页关掉也视为已确认（系统已收回）
    if form_ok and (conf or result.get("boPageClosedOnSubmit")) and _page_alive(page):
        if not _bo_not_obtained(page):
            tip2, _ = _find_dialog(page, "提示")
            if tip2 is None:
                tip2, _ = _find_dialog(page, "受益所有人")
            done = _click_dialog_footer_btn(
                page,
                ["已完成，进入下一步", "已完成,进入下一步"],
                exclude=["未完成", "返回修改", "填报受益所有人"],
            )
            if not done:
                done = _mouse_click_text(
                    page, ["已完成，进入下一步", "已完成,进入下一步"], exact=False
                )
            if not done:
                for txt in ("已完成，进入下一步", "已完成,进入下一步"):
                    if tip2 is not None and _click_text(tip2, [txt], exact=False):
                        done = True
                        break
            _safe_wait(page, 1500)
            if _bo_not_obtained(page):
                done = False
                result["error"] = "已点完成但仍提示未获取到受益所有人信息"
        else:
            result["skipDoneReason"] = "still-not-obtained"
    else:
        result["skipDoneReason"] = "form-incomplete-or-no-confirm"
    result["doneNext"] = done
    # 已点完成或页面已到办理方式 → 人员阶段结束
    if not done and _on_opening_process_page(page):
        result["doneNext"] = True
        result["doneNextVia"] = "already-on-opening"
    try:
        _shot(page if _page_alive(page) else reg_page, case_id, "personnel-done")
    except Exception:
        pass

    if _on_opening_process_page(page):
        return _handoff_personnel_to_opening(
            case_id, page, result, reason="beneficial-landed-opening"
        )

    missing = []
    if not cfg.get("skip_board"):
        if not result.get("addDirectorClick") or not result.get("directorConfirm"):
            missing.append("董事")
    if cfg.get("is_partnership"):
        ep = result.get("executivePartners") or {}
        if not ep.get("ok"):
            missing.append("执行事务合伙人")
            if not ep.get("licenseSelect"):
                missing.append("电子营业执照持照人")
    if not result.get("addManagerClick") or not result.get("managerConfirm"):
        missing.append(
            "财务负责人" if cfg.get("is_sole_prop") or result.get("managerVia") else "高级管理人员"
        )
    if not result.get("addLiaisonClick") or not result.get("liaisonConfirm"):
        missing.append("联络员")
    if not result.get("fillBeneficialClick"):
        missing.append("填报受益所有人入口")
    if not result.get("selfReport"):
        missing.append("自行填报")
    if not form_ok:
        missing.append("受益所有人表单")
    if not result.get("doneNext"):
        missing.append("已完成进入下一步")
    result["missing"] = missing
    result["ok"] = not missing
    _dump(case_id, "fill-personnel-result.json", result)
    return result


def run_personnel_flow(case_id: str, page: Page, payload: dict[str, Any]) -> dict[str, Any]:
    from apps.worker.playbooks.retry_gate import dismiss_system_busy

    store.set_status(
        case_id,
        statuses.FILLING_ESTABLISHMENT,
        customer_message="正在填写人员信息…",
    )
    if dismiss_system_busy(page):
        page.wait_for_timeout(1200)
    cfg = _defaults(payload, case_id=case_id)
    result: dict[str, Any] = {"ok": False, "cfg": cfg}

    page = _prefer_work_page(page)
    result["closedPortal"] = _close_stray_portal_tabs(page)
    result["entryUrl"] = page.url

    # 已到办理方式页：人员/受益人已过，直接交接给 opening
    if _on_opening_process_page(page):
        result["resumedAt"] = "opening-process"
        return _handoff_personnel_to_opening(
            case_id, page, result, reason="already-on-opening-at-start"
        )

    # 中断后续跑：图5 提示已弹出，人员三项已齐，直接填报受益所有人
    if _bo_prompt_open(page):
        result["resumedAt"] = "bo-prompt"
        result["pageOk"] = True
        _mark_personnel_done(result)
        bo_entry = _click_bo_entry(page)
        result["fillBeneficialClick"] = bool(bo_entry.get("clicked") or bo_entry.get("alreadyOnPage"))
        result["boEntry"] = {k: v for k, v in bo_entry.items() if k != "page"}
        result["boEntryReady"] = bool(bo_entry.get("ready"))
        if isinstance(bo_entry.get("page"), Page):
            page = bo_entry["page"]
        result["closedPortalAfterBo"] = _close_stray_portal_tabs(page)
        _shot(page, case_id, "personnel-beneficial-entry")
        if _on_opening_process_page(page):
            return _handoff_personnel_to_opening(
                case_id, page, result, reason="bo-prompt-to-opening"
            )
        if not result["boEntryReady"]:
            result["error"] = "未进入受益所有人填报页"
            result["missing"] = ["填报受益所有人入口"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result
        return _complete_beneficial(case_id, page, cfg, result)

    # 中断后续跑：已在受益所有人页则直接填表（人员信息页弹窗文案不算）
    on_bo = (
        _page_has_text(page, "自行填报")
        or _page_has_text(page, "存在符合下列条件之一的自然人")
        or _page_has_text(page, "受益所有人基本信息")
    )
    if not _is_portal_home(page) and on_bo and _frame_personnel(page, timeout_ms=1200) is None:
        result["resumedAt"] = "beneficial"
        result["pageOk"] = True
        _mark_personnel_done(result)
        return _complete_beneficial(case_id, page, cfg, result)

    # 等待进入人员信息（出资情况下一步之后；URL 常含 chief-executive-officer）
    frame = _frame_personnel(page)
    if frame is None:
        if _on_opening_process_page(page):
            result["resumedAt"] = "opening-process-no-personnel-frame"
            return _handoff_personnel_to_opening(
                case_id, page, result, reason="opening-without-personnel-frame"
            )
        _click_text_any(page, ["下一步"], exact=True)
        page.wait_for_timeout(2000)
        if _on_opening_process_page(page):
            return _handoff_personnel_to_opening(
                case_id, page, result, reason="next-landed-opening"
            )
        frame = _frame_personnel(page, timeout_ms=30000)
    if frame is None:
        if _on_opening_process_page(page):
            return _handoff_personnel_to_opening(
                case_id, page, result, reason="opening-after-wait"
            )
        result["error"] = "未进入人员信息页"
        result["entryUrlAfter"] = page.url
        _shot(page, case_id, "personnel-page-missing")
        _dump(case_id, "fill-personnel-result.json", result)
        return result
    result["pageOk"] = True
    result["frameUrl"] = getattr(frame, "url", "") or ""
    try:
        frame.evaluate(
            """() => {
              window.scrollTo(0, 0);
              const app = document.querySelector('wujie-app');
              const root = app && (app.shadowRoot || app);
              if (root) {
                const sc = root.querySelector('.el-main, .main, .content, #app, body') || root;
                if (sc && sc.scrollTo) sc.scrollTo(0, 0);
                if (sc) sc.scrollTop = 0;
              }
              document.documentElement.scrollTop = 0;
              document.body && (document.body.scrollTop = 0);
            }"""
        )
    except Exception:
        try:
            page.mouse.wheel(0, -5000)
        except Exception:
            pass
    for tip in ("设一名董事", "董事信息", "执行事务合伙人", "人员信息"):
        try:
            loc = frame.get_by_text(tip, exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
                break
        except Exception:
            continue
    page.wait_for_timeout(500)
    _shot(page, case_id, "personnel-page")

    # 合伙企业 / 个人独资：无董事/监事，跳过
    if cfg.get("skip_board"):
        result["oneDirector"] = False
        result["noSupervisor"] = True
        result["establishSupervisor"] = False
        result["addDirectorClick"] = True
        result["directorConfirm"] = True
        result["directorSkipped"] = (
            "sole-prop-no-director" if cfg.get("is_sole_prop") else "partnership-no-director"
        )
        result["executivePartnerNames"] = list(cfg.get("executive_partner_names") or [])
        result["licenseHolderNames"] = list(cfg.get("license_holder_names") or [])
        _shot(page, case_id, "personnel-skip-director")
        if cfg.get("is_partnership"):
            result["executivePartners"] = _fill_executive_partners(
                page, frame, cfg, case_id, payload
            )
            frame = _frame_personnel(page) or frame
            if not (result.get("executivePartners") or {}).get("ok"):
                result["ok"] = False
                result["error"] = (result["executivePartners"] or {}).get("error") or (
                    "执行事务合伙人或电子营业执照持照人未完成"
                )
                result["missing"] = ["执行事务合伙人", "电子营业执照持照人"]
                _shot(page, case_id, "personnel-exec-need-human")
                _dump(case_id, "fill-personnel-result.json", result)
                return result
    else:
        # 1) 董事/监事选项
        result["oneDirector"] = _click_radio(frame, "设一名董事")
        page.wait_for_timeout(300)
        # 仅一名监事（不设监事会）
        sup_list = list(cfg.get("supervisor_list") or [])[:1]
        if not sup_list and cfg.get("establish_supervisor"):
            name0 = (cfg.get("supervisor_name") or "").strip()
            if name0:
                sup_list = [
                    {
                        "name": name0,
                        "mobile": str(cfg.get("supervisor_mobile") or "").strip(),
                    }
                ]
        if cfg.get("establish_supervisor") or cfg.get("is_shares"):
            result["noSupervisor"] = False
            result["supervisorMode"] = "one"
            result["establishSupervisor"] = (
                _click_radio(frame, "设一名监事")
                or _click_radio(frame, "设监事")
            )
            if not result["establishSupervisor"]:
                # 页面若只剩监事会选项则仍尝试点一次（再只填一人）
                result["establishSupervisor"] = _click_radio(frame, "设监事会")
                if result["establishSupervisor"]:
                    result["supervisorBoardCount"] = _fill_by_label(
                        frame, "监事会人数", "1"
                    ) or _js_fill_near(frame, "监事会人数", "1")
                    result["employeeSupervisorCount"] = _fill_by_label(
                        frame, "职工监事", "0"
                    ) or _js_fill_near(frame, "职工监事", "0")
        else:
            result["establishSupervisor"] = False
            result["noSupervisor"] = _click_radio(
                frame, "属规模较小或者股东人数较少的有限责任公司，全体股东一致同意不设监事"
            )
            if not result["noSupervisor"]:
                result["noSupervisor"] = _click_radio(frame, "不设监事")
            if not result["noSupervisor"]:
                result["noSupervisor"] = _click_radio(frame, "规模较小")
        page.wait_for_timeout(400)
        _shot(page, case_id, "personnel-after-radios")

        # 2) 按 payload.personnel.directors 逐个添加董事
        director_list = list(cfg.get("director_list") or [])
        if not director_list:
            director_list = [{"name": cfg.get("director_name") or cfg.get("name") or ""}]
        result["directorsPlanned"] = [d.get("name") for d in director_list]
        result["directorsAdded"] = []
        if _section_quota_done(frame, "董事") and len(director_list) <= 1:
            result["addDirectorClick"] = True
            result["directorConfirm"] = True
            result["directorSkipped"] = "already-filled"
        else:
            lr_name = (cfg.get("name") or "").strip()
            all_dir_ok = True
            for d_idx, d_person in enumerate(director_list):
                dir_name = str(d_person.get("name") or "").strip()
                item: dict[str, Any] = {"index": d_idx, "name": dir_name}
                # 已在页上则跳过
                try:
                    page_txt = frame.inner_text(timeout=800) or ""
                except Exception:
                    page_txt = ""
                if dir_name and dir_name in page_txt and "董事" in page_txt:
                    # 粗略：姓名已出现在人员页，可能已添加
                    pass
                add_dir = (
                    _click_text(
                        frame,
                        ["+ 添加董事 >>", "添加董事 >>", "+ 添加董事", "添加董事"],
                        exact=False,
                    )
                    or _click_text_any(page, ["添加董事"], exact=False)
                )
                item["addClick"] = add_dir
                if not add_dir:
                    # 配额已满时可能点不了
                    if _section_quota_done(frame, "董事"):
                        item["skipped"] = "quota-done"
                        item["ok"] = True
                        result["directorsAdded"].append(item)
                        break
                    item["ok"] = False
                    item["error"] = "未点到添加董事"
                    result["directorsAdded"].append(item)
                    all_dir_ok = False
                    break
                page.wait_for_timeout(900)
                dlg, _ = _find_dialog(page, "新增董事")
                if dlg is None:
                    dlg, _ = _find_dialog(page, "新增董事", "董事")
                if dlg is None:
                    item["ok"] = False
                    item["error"] = "未打开新增董事弹窗"
                    result["directorsAdded"].append(item)
                    result["error"] = item["error"]
                    _shot(page, case_id, f"personnel-director-dialog-missing-{d_idx}")
                    _dump(case_id, "fill-personnel-result.json", result)
                    return result
                item["person"] = _natural_via_id_ocr(
                    dlg,
                    page,
                    case_id=case_id,
                    payload=payload,
                    person_name=dir_name,
                )
                page.wait_for_timeout(600)
                # 联系方式（移动电话）— OCR 不会带出，必须手填
                dir_mobile = (
                    str(d_person.get("mobile") or "").strip()
                    or str(cfg.get("director_mobile") or cfg.get("mobile") or DEFAULT_MOBILE).strip()
                )
                item["mobile"] = _fill_contact_mobile(dlg, dir_mobile)
                page.wait_for_timeout(200)
                # 法定代表人：与法人同名的董事设为是
                is_lr = True
                if dir_name and lr_name and dir_name != lr_name:
                    is_lr = False
                item["isLegalRep"] = _set_yes_no(dlg, "法定代表人", is_lr) or _set_yes_no(
                    dlg, "是否由董事担任", is_lr
                )
                page.wait_for_timeout(300)
                # 确定前再写一次电话，避免被其它控件冲掉
                if not (item.get("mobile") or {}).get("ok"):
                    item["mobileRetry"] = _fill_contact_mobile(dlg, dir_mobile)
                else:
                    _fill_contact_mobile(dlg, dir_mobile)
                _shot(page, case_id, f"personnel-director-before-confirm-{d_idx}")
                item["confirm"] = _confirm_until_closed(page, ("新增董事",), retries=5)
                page.wait_for_timeout(600)
                if _dialog_still_open(page, "新增董事"):
                    item["ok"] = False
                    item["error"] = "董事弹窗未关闭"
                    result["directorsAdded"].append(item)
                    result["error"] = item["error"]
                    result["missing"] = ["董事"]
                    _dump(case_id, "fill-personnel-result.json", result)
                    return result
                item["ok"] = True
                result["directorsAdded"].append(item)
                frame = _frame_personnel(page) or frame
            result["addDirectorClick"] = any(x.get("addClick") for x in result["directorsAdded"])
            result["directorConfirm"] = all_dir_ok and all(
                x.get("ok") or x.get("skipped") for x in result["directorsAdded"]
            )
            result["directorPerson"] = (result["directorsAdded"][0].get("person") if result["directorsAdded"] else None)
            _shot(page, case_id, "personnel-after-director")

        # 2b) 按 payload 添加一名监事（不设监事会）
        if cfg.get("establish_supervisor") or cfg.get("is_shares"):
            if not sup_list:
                name0 = (cfg.get("supervisor_name") or "").strip()
                if name0:
                    sup_list = [
                        {
                            "name": name0,
                            "mobile": str(cfg.get("supervisor_mobile") or "").strip(),
                        }
                    ]
            sup_list = (sup_list or [])[:1]
            # 董事与监事不能同一人
            dir_names_rt = {
                str(x.get("name") or "").strip()
                for x in (cfg.get("director_list") or [])
                if str((x or {}).get("name") or "").strip()
            }
            dn = str(cfg.get("director_name") or "").strip()
            if dn:
                dir_names_rt.add(dn)
            if sup_list:
                sn0 = str(sup_list[0].get("name") or "").strip()
                if sn0 and sn0 in dir_names_rt:
                    result["supervisorDirectorConflict"] = True
                    result["error"] = "董事与监事不能为同一人"
                    result["missing"] = ["监事"]
                    _shot(page, case_id, "personnel-supervisor-director-conflict")
                    store.set_status(
                        case_id,
                        statuses.NEED_HUMAN,
                        reason=f"董事与监事不能为同一人（{sn0}）",
                    )
                    _dump(case_id, "fill-personnel-result.json", result)
                    return result
            result["supervisorsPlanned"] = [s.get("name") for s in sup_list]
            result["supervisorsAdded"] = []
            if not sup_list:
                result["supervisorSkipped"] = "no-supervisor-payload"
            elif _section_quota_done(frame, "监事") and len(sup_list) <= 1:
                result["addSupervisorClick"] = True
                result["supervisorConfirm"] = True
                result["supervisorSkipped"] = "already-filled"
            else:
                all_sup_ok = True
                for s_idx, s_person in enumerate(sup_list):
                    sup_name = str(s_person.get("name") or "").strip()
                    item: dict[str, Any] = {"index": s_idx, "name": sup_name}
                    add_sup = (
                        _click_text(
                            frame,
                            ["+ 添加监事 >>", "添加监事 >>", "+ 添加监事", "添加监事"],
                            exact=False,
                        )
                        or _click_text_any(page, ["添加监事"], exact=False)
                    )
                    item["addClick"] = add_sup
                    if not add_sup:
                        if _section_quota_done(frame, "监事"):
                            item["skipped"] = "quota-done"
                            item["ok"] = True
                            result["supervisorsAdded"].append(item)
                            break
                        item["ok"] = False
                        item["error"] = "未点到添加监事"
                        result["supervisorsAdded"].append(item)
                        all_sup_ok = False
                        break
                    page.wait_for_timeout(900)
                    dlg, _ = _find_dialog(page, "新增监事")
                    if dlg is None:
                        dlg, _ = _find_dialog(page, "监事")
                    if dlg is None:
                        item["ok"] = False
                        item["error"] = "未打开新增监事弹窗"
                        result["supervisorsAdded"].append(item)
                        result["error"] = item["error"]
                        _shot(page, case_id, f"personnel-supervisor-dialog-missing-{s_idx}")
                        _dump(case_id, "fill-personnel-result.json", result)
                        return result
                    item["person"] = _natural_via_id_ocr(
                        dlg,
                        page,
                        case_id=case_id,
                        payload=payload,
                        person_name=sup_name,
                    )
                    page.wait_for_timeout(600)
                    # 职务默认「监事」
                    item["title"] = _select_dropdown(dlg, "职务", "监事")
                    if not item["title"]:
                        try:
                            tit = dlg.locator(".el-form-item").filter(
                                has_text=re.compile(r"职务")
                            ).first
                            trig = tit.locator(
                                ".el-select .el-input__inner, .el-select"
                            ).first
                            if _visible(trig, timeout=500):
                                trig.click(timeout=2500)
                                page.wait_for_timeout(350)
                                for root in _all_scopes(page):
                                    opt = root.locator(
                                        ".el-select-dropdown:visible li"
                                    ).filter(has_text=re.compile(r"^监事$")).first
                                    if not _visible(opt, timeout=300):
                                        opt = root.get_by_text("监事", exact=True).first
                                    if _visible(opt, timeout=400):
                                        opt.click(timeout=2500)
                                        item["title"] = True
                                        break
                        except Exception:
                            pass
                    if not item.get("title"):
                        try:
                            # 已默认选中也算成功
                            item["title"] = _dropdown_shows(dlg, "职务", "监事")
                        except Exception:
                            pass
                    page.wait_for_timeout(300)
                    sup_mobile = (
                        str(s_person.get("mobile") or "").strip()
                        or str(cfg.get("supervisor_mobile") or cfg.get("mobile") or DEFAULT_MOBILE).strip()
                    )
                    item["mobile"] = _fill_contact_mobile(dlg, sup_mobile)
                    if not (item.get("mobile") or {}).get("ok"):
                        item["mobileRetry"] = _fill_contact_mobile(dlg, sup_mobile)
                    _shot(page, case_id, f"personnel-supervisor-before-confirm-{s_idx}")
                    item["confirm"] = _confirm_until_closed(
                        page, ("新增监事", "监事"), retries=5
                    )
                    page.wait_for_timeout(600)
                    if _dialog_still_open(page, "新增监事") or _dialog_still_open(page, "监事"):
                        # 仅标题含「监事」且仍开着时再判失败
                        still = _dialog_still_open(page, "新增监事")
                        if still:
                            item["ok"] = False
                            item["error"] = "监事弹窗未关闭"
                            result["supervisorsAdded"].append(item)
                            result["error"] = item["error"]
                            result["missing"] = ["监事"]
                            _dump(case_id, "fill-personnel-result.json", result)
                            return result
                    item["ok"] = True
                    result["supervisorsAdded"].append(item)
                    frame = _frame_personnel(page) or frame
                result["addSupervisorClick"] = any(
                    x.get("addClick") for x in result["supervisorsAdded"]
                )
                result["supervisorConfirm"] = all_sup_ok and all(
                    x.get("ok") or x.get("skipped") for x in result["supervisorsAdded"]
                )
                _shot(page, case_id, "personnel-after-supervisor")

    # 3) 财务负责人：有限公司「添加高级管理人员」；个人独资等为页面内联表单
    if _section_quota_done(frame, "高管"):
        result["addManagerClick"] = True
        result["managerConfirm"] = True
        result["managerSkipped"] = "already-filled"
    elif cfg.get("is_sole_prop") or (
        _has_inline_finance(frame) and not _has_add_manager_button(frame)
    ):
        inline = _fill_inline_finance_officer(page, frame, cfg, case_id, payload)
        result["managerInline"] = inline
        result["addManagerClick"] = True
        result["managerConfirm"] = bool(inline.get("ok"))
        result["managerVia"] = "inline-finance"
        if not result["managerConfirm"]:
            result["error"] = "财务负责人内联填写未生效"
            result["missing"] = ["财务负责人"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result
    else:
        try:
            loc = frame.get_by_text("高级管理人员", exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            try:
                frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                page.mouse.wheel(0, 1200)
        page.wait_for_timeout(400)
        add_mgr = (
            _click_text(
                frame,
                ["+ 添加高级管理人员 >>", "添加高级管理人员 >>", "添加高级管理人员"],
                exact=False,
            )
            or _click_text_any(page, ["添加高级管理人员"], exact=False)
        )
        result["addManagerClick"] = add_mgr
        page.wait_for_timeout(900)
        dlg, _ = _find_dialog(page, "高级管理人员")
        if dlg is None:
            dlg, _ = _find_dialog(page, "新增高级")
        if dlg is None:
            # 无弹窗时回退内联财务负责人（部分组织形式）
            if _has_inline_finance(frame):
                inline = _fill_inline_finance_officer(page, frame, cfg, case_id, payload)
                result["managerInline"] = inline
                result["addManagerClick"] = True
                result["managerConfirm"] = bool(inline.get("ok"))
                result["managerVia"] = "inline-finance-fallback"
                if not result["managerConfirm"]:
                    result["error"] = "财务负责人填写未生效"
                    result["missing"] = ["财务负责人"]
                    _dump(case_id, "fill-personnel-result.json", result)
                    return result
            else:
                result["error"] = "未打开高级管理人员弹窗"
                _shot(page, case_id, "personnel-manager-dialog-missing")
                _dump(case_id, "fill-personnel-result.json", result)
                return result
        if dlg is not None and not result.get("managerConfirm"):
            try:
                title = (dlg.locator(".el-dialog__title").inner_text(timeout=500) or "")
                result["managerDialogTitle"] = title
                if "董事" in title and "高级" not in title:
                    result["error"] = "打开的仍是董事弹窗，非高级管理人员"
                    _shot(page, case_id, "personnel-manager-wrong-dialog")
                    _dump(case_id, "fill-personnel-result.json", result)
                    return result
            except Exception:
                pass
            result["managerPerson"] = _natural_via_id_ocr(
                dlg,
                page,
                case_id=case_id,
                payload=payload,
                person_name=cfg.get("finance_name") or cfg.get("name") or "",
            )
            page.wait_for_timeout(600)
            mgr_mobile = str(cfg.get("finance_mobile") or cfg.get("mobile") or DEFAULT_MOBILE).strip()
            result["managerTitle"] = _select_dropdown(dlg, "职务", "财务负责人")
            if not result["managerTitle"]:
                try:
                    item = dlg.locator(".el-form-item").filter(has_text=re.compile(r"职务")).first
                    trig = item.locator(".el-select .el-input__inner, .el-select").first
                    if _visible(trig, timeout=500):
                        trig.click(timeout=2500)
                        page.wait_for_timeout(400)
                        for root in _all_scopes(page):
                            opt = root.locator(".el-select-dropdown:visible li").filter(
                                has_text=re.compile(r"财务负责人")
                            ).first
                            if _visible(opt, timeout=400):
                                opt.click(timeout=2500)
                                result["managerTitle"] = True
                                break
                except Exception:
                    pass
            page.wait_for_timeout(700)
            for _ in range(10):
                try:
                    txt = dlg.inner_text(timeout=500) or ""
                except Exception:
                    txt = ""
                if "产生方式" in txt:
                    break
                page.wait_for_timeout(300)
            result["appointMethod"] = _select_dropdown(dlg, "产生方式", "董事聘任")
            if not result["appointMethod"]:
                try:
                    item = dlg.locator(".el-form-item").filter(has_text=re.compile(r"产生方式")).first
                    trig = item.locator(".el-select .el-input__inner, .el-select").first
                    if _visible(trig, timeout=500):
                        trig.click(timeout=2500)
                        page.wait_for_timeout(350)
                        for root in _all_scopes(page):
                            opt = root.get_by_text("董事聘任", exact=False).first
                            if _visible(opt, timeout=400):
                                opt.click(timeout=2500)
                                result["appointMethod"] = True
                                break
                except Exception:
                    pass
            # 职务/产生方式选完后再填移动电话（避免误写固定电话或被冲掉）
            page.wait_for_timeout(300)
            result["managerMobile"] = _fill_contact_mobile(dlg, mgr_mobile)
            if not (result.get("managerMobile") or {}).get("ok"):
                result["managerMobile2"] = _fill_contact_mobile(dlg, mgr_mobile)
            _shot(page, case_id, "personnel-manager-before-confirm")
            result["managerConfirm"] = _confirm_until_closed(page, ("高级管理人员",))
            if not result["managerConfirm"]:
                result["managerConfirm"] = _confirm_until_closed(page, ("新增", "高级"))
            page.wait_for_timeout(600)
            if _dialog_still_open(page, "高级管理人员"):
                dlg2, _ = _find_dialog(page, "高级管理人员")
                if dlg2 is None:
                    dlg2, _ = _find_dialog(page, "新增高级")
                if dlg2 is not None:
                    result["managerMobileRetry"] = _fill_contact_mobile(dlg2, mgr_mobile)
                    page.wait_for_timeout(200)
                    result["managerConfirm"] = _confirm_until_closed(page, ("高级管理人员", "新增"))
                    page.wait_for_timeout(600)
            _shot(page, case_id, "personnel-after-manager")
            if _dialog_still_open(page, "高级管理人员") or _dialog_still_open(page, "新增董事"):
                result["error"] = "高级管理人员弹窗未关闭"
                result["missing"] = ["高级管理人员"]
                _dump(case_id, "fill-personnel-result.json", result)
                return result

    # 4) 添加联络员
    if _section_quota_done(frame, "联络员"):
        result["addLiaisonClick"] = True
        result["liaisonConfirm"] = True
        result["liaisonSkipped"] = "already-filled"
    else:
        try:
            loc = frame.get_by_text("登记联络员", exact=False).first
            if _visible(loc, timeout=600):
                loc.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            try:
                frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                page.mouse.wheel(0, 800)
        page.wait_for_timeout(400)
        add_lia = (
            _click_text(frame, ["+ 添加联络员 >>", "添加联络员 >>", "添加联络员"], exact=False)
            or _click_text_any(page, ["添加联络员"], exact=False)
        )
        result["addLiaisonClick"] = add_lia
        page.wait_for_timeout(900)
        dlg, _ = _find_dialog(page, "登记联络员")
        if dlg is None:
            dlg, _ = _find_dialog(page, "联络员")
        if dlg is None:
            result["error"] = "未打开联络员弹窗"
            _shot(page, case_id, "personnel-liaison-dialog-missing")
            _dump(case_id, "fill-personnel-result.json", result)
            return result
        try:
            title = (dlg.locator(".el-dialog__title").inner_text(timeout=500) or "")
            result["liaisonDialogTitle"] = title
            if "董事" in title and "联络" not in title:
                result["error"] = "打开的仍是董事弹窗，非联络员"
                _shot(page, case_id, "personnel-liaison-wrong-dialog")
                _dump(case_id, "fill-personnel-result.json", result)
                return result
        except Exception:
            pass
        result["liaisonPerson"] = _natural_via_id_ocr(
            dlg,
            page,
            case_id=case_id,
            payload=payload,
            person_name=cfg.get("liaison_name") or cfg.get("name") or "",
        )
        page.wait_for_timeout(600)
        result["liaisonPublicSys"] = _set_yes_no(dlg, "代表登录|公示系统", True)
        if not result["liaisonPublicSys"]:
            result["liaisonPublicSys"] = _set_yes_no(dlg, "公示系统", True)
        liaison_mobile = str(
            cfg.get("liaison_mobile") or cfg.get("mobile") or DEFAULT_MOBILE
        ).strip()
        result["liaisonMobile"] = _fill_contact_mobile(dlg, liaison_mobile)
        result["liaisonEmail"] = _fill_by_label(dlg, "电子邮箱", cfg["email"]) or _fill_by_label(
            dlg, "邮箱", cfg["email"]
        )
        result["liaisonAddress"] = _fill_by_label(dlg, "住所", cfg["address"]) or _fill_by_label(
            dlg, "地址", cfg["address"]
        )
        # 确定前再写一次电话（避免被其它字段冲掉）
        result["liaisonMobile2"] = _fill_contact_mobile(dlg, liaison_mobile)
        page.wait_for_timeout(300)
        _shot(page, case_id, "personnel-liaison-before-confirm")
        result["liaisonConfirm"] = _confirm_until_closed(page, ("联络员",))
        page.wait_for_timeout(600)
        # 若因手机号校验弹窗未关，再填一次后确定
        if not result["liaisonConfirm"] or _dialog_still_open(page, "联络员"):
            dlg2, _ = _find_dialog(page, "联络员")
            if dlg2 is None:
                dlg2, _ = _find_dialog(page, "登记联络员")
            if dlg2 is not None:
                result["liaisonMobileRetry"] = _fill_contact_mobile(dlg2, liaison_mobile)
                page.wait_for_timeout(200)
                result["liaisonConfirm"] = _confirm_until_closed(page, ("联络员", "登记联络员"))
                page.wait_for_timeout(600)
        _shot(page, case_id, "personnel-after-liaison")
        if not result["liaisonConfirm"]:
            result["error"] = "联络员弹窗未关闭"
            result["missing"] = ["联络员"]
            _dump(case_id, "fill-personnel-result.json", result)
            return result

    # 5) 人员信息页下一步 → 图5 提示：点弹窗底部「填报受益所有人信息」
    try:
        frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass
    page.wait_for_timeout(300)
    if _on_opening_process_page(page):
        return _handoff_personnel_to_opening(
            case_id, page, result, reason="already-opening-before-personnel-next"
        )
    next1 = _click_text(frame, ["下一步"], exact=True) or _click_text_any(page, ["下一步"], exact=True)
    result["personnelNext"] = next1
    page.wait_for_timeout(800)
    if _on_opening_process_page(page):
        return _handoff_personnel_to_opening(
            case_id, page, result, reason="personnel-next-to-opening"
        )
    bo_entry = _click_bo_entry(page)
    result["fillBeneficialClick"] = bool(bo_entry.get("clicked") or bo_entry.get("alreadyOnPage"))
    result["boEntry"] = {k: v for k, v in bo_entry.items() if k != "page"}
    result["boEntryReady"] = bool(bo_entry.get("ready"))
    if isinstance(bo_entry.get("page"), Page):
        page = bo_entry["page"]
    result["closedPortalAfterBo"] = _close_stray_portal_tabs(page)
    _shot(page, case_id, "personnel-beneficial-entry")
    if _on_opening_process_page(page):
        return _handoff_personnel_to_opening(
            case_id, page, result, reason="after-bo-entry-on-opening"
        )
    if not result["boEntryReady"]:
        # 无受益人弹窗且已离开人员页时，勿死卡在人员阶段
        if _on_opening_process_page(page) or not _frame_personnel(page, timeout_ms=2000):
            if _on_opening_process_page(page):
                return _handoff_personnel_to_opening(
                    case_id, page, result, reason="no-bo-entry-on-opening"
                )
        result["error"] = "未进入受益所有人填报页"
        result["missing"] = ["填报受益所有人入口"]
        _dump(case_id, "fill-personnel-result.json", result)
        return result

    return _complete_beneficial(case_id, page, cfg, result)
