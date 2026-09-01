"""伽玛注册 API（api.gamaz.cn）同源代理客户端。

透传 name_check / biz_scope，统一返回上游 {code,msg,data,error_code}；
失败时返回 code=0，便于前端降级。
name_check 上游不可用时提供本地规则降级，保证字号添加可用。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

GAMAZ_API_BASE = "https://api.gamaz.cn"
DEFAULT_TIMEOUT = 12.0
CHECK_TIMEOUT = 25.0
# 直连上游，避免 Windows「系统代理」指向已关闭的 127.0.0.1:xxxx 导致 WinError 10061
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_ZIHAO_RE = re.compile(r"^[\u4e00-\u9fff]{2,6}$")
_ORG_FORMS = [
    {"value": "有限公司", "label": "有限公司"},
    {"value": "股份有限公司", "label": "股份有限公司"},
    {"value": "个人独资企业", "label": "个人独资企业"},
    {"value": "普通合伙企业", "label": "普通合伙企业"},
    {"value": "有限合伙企业", "label": "有限合伙企业"},
    {"value": "个体工商户", "label": "个体工商户"},
]
_INDUSTRY_SEED = [
    "科技", "信息科技", "网络科技", "技术", "信息技术", "商贸", "贸易",
    "咨询", "管理咨询", "文化", "教育", "医疗", "健康", "设计", "传媒",
]
_HIGH_CHARS = list("启创云智数澜芮骁璟翊宥桓昭屿澄沐泊川星辰皓宇安泰合源通达")


def _fail(msg: str, *, error_code: int = -1) -> dict[str, Any]:
    return {"code": 0, "msg": msg, "data": None, "error_code": error_code}


def _ok(data: Any, msg: str = "success") -> dict[str, Any]:
    return {"code": 1, "msg": msg, "data": data, "error_code": 0}


def local_zihao_generate(
    count: int = 10,
    *,
    industry: str = "",
    district: str = "上海",
) -> dict[str, Any]:
    """本地高通过率二字字号（无大模型时的降级）。"""
    import random

    n = max(1, min(int(count or 10), 20))
    seen: set[str] = set()
    out: list[str] = []
    # 固定种子池打乱，保证每次略有变化且可凑满
    pool = _HIGH_CHARS[:]
    random.shuffle(pool)
    for a in pool:
        for b in pool:
            if a == b:
                continue
            z = a + b
            if z in seen:
                continue
            seen.add(z)
            out.append(z)
            if len(out) >= n:
                break
        if len(out) >= n:
            break
    while len(out) < n:
        z = random.choice(_HIGH_CHARS) + random.choice(_HIGH_CHARS)
        if z not in seen:
            seen.add(z)
            out.append(z)
    return _ok(
        {
            "list": [{"zihao": z} for z in out[:n]],
            "source": "local",
            "industry": industry or "",
            "district": district or "上海",
        },
        msg="local fallback",
    )


def _extract_zihao_list(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    raw = data.get("list") or data.get("items") or data.get("zihao_list") or data.get("candidates")
    out: list[str] = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, str):
                z = it.strip()
            elif isinstance(it, dict):
                z = str(it.get("zihao") or it.get("name") or it.get("tradeName") or "").strip()
            else:
                z = ""
            if _ZIHAO_RE.match(z) and z not in out:
                out.append(z)
    return out


def _llm_zihao_generate_sync(
    *,
    count: int,
    industry: str,
    district: str,
    org_form: str,
) -> Optional[dict[str, Any]]:
    """OpenAI 兼容 Chat Completions；需环境变量 GAMAZ_LLM_API_KEY（或 OPENAI_API_KEY）。"""
    import os

    api_key = (os.environ.get("GAMAZ_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    base = (
        os.environ.get("GAMAZ_LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = (os.environ.get("GAMAZ_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
    prompt = (
        f"请生成{count}个适合中国大陆公司核名的二字汉字字号候选。"
        f"地区={district or '上海'}，行业表述={industry or '科技'}，组织形式={org_form or '有限公司'}。"
        "要求：仅常用汉字、易读易记、避免生僻字/多音难写字/敏感词；不要带地区或组织形式后缀。"
        '只返回 JSON：{"list":["字号1","字号2",...]}'
    )
    body = {
        "model": model,
        "temperature": 0.8,
        "messages": [
            {"role": "system", "content": "你是企业核名字号助手，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    url = base + "/chat/completions"
    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw_body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "gamma-rpa-demo/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            parsed = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = (
            (((parsed.get("choices") or [{}])[0]).get("message") or {}).get("content")
            or ""
        )
        if isinstance(content, list):
            content = "".join(
                (p.get("text") if isinstance(p, dict) else str(p)) for p in content
            )
        obj = json.loads(content) if isinstance(content, str) else {}
        zis = _extract_zihao_list(obj if isinstance(obj, dict) else {})
        if not zis and isinstance(obj, dict):
            # 有的模型直接返回 list
            maybe = obj.get("list")
            if isinstance(maybe, list):
                zis = [str(x).strip() for x in maybe if _ZIHAO_RE.match(str(x).strip())]
        if not zis:
            return None
        return _ok(
            {
                "list": [{"zihao": z} for z in zis[:count]],
                "source": "llm",
                "model": model,
                "industry": industry or "",
                "district": district or "上海",
            },
            msg="llm",
        )
    except Exception:
        return None


async def generate_zihao_candidates(body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """优先上游 → 大模型 → 本地字库，保证返回 count 个字号。"""
    body = body or {}
    try:
        count = int(body.get("count") or 10)
    except (TypeError, ValueError):
        count = 10
    count = max(1, min(count, 20))
    industry = str(body.get("industry") or "").strip()
    district = str(body.get("district") or "上海").strip() or "上海"
    org_form = str(body.get("orgForm") or body.get("org_form") or "有限公司").strip()

    payload = {
        "district": district,
        "industry": industry,
        "orgForm": org_form,
        "count": count,
        "limit": count,
    }
    for path in (
        "/api/name_check/zihao_generate",
        "/api/name_check/generate",
        "/api/name_check/suggest",
    ):
        upstream = await proxy_post(path, payload)
        if upstream.get("code") == 1:
            zis = _extract_zihao_list(upstream.get("data") or {})
            if zis:
                return _ok(
                    {
                        "list": [{"zihao": z} for z in zis[:count]],
                        "source": "upstream",
                        "path": path,
                        "industry": industry,
                        "district": district,
                    }
                )

    llm = await asyncio.to_thread(
        _llm_zihao_generate_sync,
        count=count,
        industry=industry,
        district=district,
        org_form=org_form,
    )
    if llm and llm.get("code") == 1:
        zis = _extract_zihao_list(llm.get("data") or {})
        if len(zis) >= count:
            return llm
        # 不足则本地补齐
        local = local_zihao_generate(count, industry=industry, district=district)
        for it in (local.get("data") or {}).get("list") or []:
            z = it.get("zihao") if isinstance(it, dict) else ""
            if z and z not in zis:
                zis.append(z)
            if len(zis) >= count:
                break
        return _ok(
            {
                "list": [{"zihao": z} for z in zis[:count]],
                "source": "llm+local",
                "industry": industry,
                "district": district,
            }
        )

    return local_zihao_generate(count, industry=industry, district=district)


def local_name_meta() -> dict[str, Any]:
    return _ok(
        {
            "region": "上海",
            "region_code": "310000",
            "org_forms": list(_ORG_FORMS),
            "disclaimer": "查重结果仅供参考，最终以登记机关申报系统的计算机比对及人工审核为准。",
            "fallback": True,
        },
        msg="local fallback",
    )


def local_industry_suggest(kw: str = "", limit: int = 20) -> dict[str, Any]:
    q = (kw or "").strip()
    items = [n for n in _INDUSTRY_SEED if (not q or q in n)]
    return _ok({"list": [{"name": n, "code": ""} for n in items[: max(1, min(limit, 50))]]}, msg="local fallback")


def local_name_validate(body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body = body or {}
    zihao = str(body.get("zihao") or "").strip()
    if not zihao:
        level, msg = "error", "请输入字号"
    elif not _ZIHAO_RE.match(zihao):
        level, msg = "error", "字号须为 2–6 个汉字"
    else:
        level, msg = "pass", "字号格式校验通过（本地规则）"
    return _ok(
        {"validation": {"zihao": {"level": level, "msg": msg}}, "fallback": True},
        msg="local fallback",
    )


def local_name_check(body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    body = body or {}
    district = str(body.get("district") or "上海").strip() or "上海"
    zihao = str(body.get("zihao") or "").strip()
    industry = str(body.get("industry") or "").strip()
    org_form = str(body.get("orgForm") or body.get("org_form") or "有限公司").strip()
    # 政务网名称后缀与「企业类型」文案不完全一致
    suffix_map = {
        "普通合伙企业": "合伙企业（普通合伙）",
        "有限合伙企业": "合伙企业（有限合伙）",
        "个人独资企业": "经营部（个人独资）",
        "股份公司": "股份有限公司",
    }
    name_suffix = suffix_map.get(org_form, org_form)
    name = f"{district}{zihao}{industry}{name_suffix}"
    return _ok(
        {
            "name": name,
            "conclusion": {
                "status": "caution",
                "label": "待线上查重",
                "detail": "核名智能接口暂不可用，已用本地规则通过格式校验",
            },
            "fallback": True,
        },
        msg="local fallback",
    )


async def proxy_get_with_fallback(
    path: str,
    query: Optional[dict[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    fallback: Optional[Any] = None,
) -> dict[str, Any]:
    data = await proxy_get(path, query, timeout=timeout)
    if data.get("code") == 1:
        return data
    if callable(fallback):
        return fallback()
    return data


async def proxy_post_with_fallback(
    path: str,
    body: Optional[dict[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    fallback: Optional[Any] = None,
) -> dict[str, Any]:
    data = await proxy_post(path, body, timeout=timeout)
    if data.get("code") == 1:
        return data
    if callable(fallback):
        return fallback(body or {})
    return data


def _request(
    method: str,
    path: str,
    *,
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    url = GAMAZ_API_BASE.rstrip("/") + "/" + path.lstrip("/")
    if query:
        filtered = {k: v for k, v in query.items() if v is not None and v != ""}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered, doseq=True)

    data = None
    headers = {"Accept": "application/json", "User-Agent": "gamma-rpa-demo/1.0"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    return _fail("上游返回非 JSON")
                if isinstance(parsed, dict):
                    return parsed
                return _fail("上游返回格式异常")
        except urllib.error.HTTPError as e:
            try:
                raw = e.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            return _fail(f"上游 HTTP {e.code}")
        except urllib.error.URLError as e:
            last_err = e
            # 连接被拒 / 临时 DNS：短暂重试
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            return _fail(f"无法连接伽玛 API：{e.reason}")
        except TimeoutError as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
                continue
            return _fail("伽玛 API 请求超时")
        except Exception as e:
            return _fail(str(e) or "伽玛 API 请求失败")
    if last_err is not None:
        return _fail(f"无法连接伽玛 API：{getattr(last_err, 'reason', last_err)}")
    return _fail("伽玛 API 请求失败")


async def proxy_get(
    path: str,
    query: Optional[dict[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    return await asyncio.to_thread(_request, "GET", path, query=query, body=None, timeout=timeout)


async def proxy_post(
    path: str,
    body: Optional[dict[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    return await asyncio.to_thread(_request, "POST", path, query=None, body=body or {}, timeout=timeout)
