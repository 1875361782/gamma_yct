"""按组织形式分支的填报配置（basic_info / capital / personnel 共用）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def org_form_of(payload: dict[str, Any]) -> str:
    return ((payload.get("name") or {}).get("orgForm") or "").strip()


def profile_for(payload: dict[str, Any]) -> dict[str, Any]:
    org = org_form_of(payload)
    # 默认：有限公司
    p: dict[str, Any] = {
        "orgForm": org,
        "agent_types": ["法定代表人"],
        # 无区县时的兜底；有区县时 basic_info 优先选「{区}市场监督管理局」
        "authority_options": [
            "上海市市场监督管理局",
            "上海市市场监督局",
            "市市场监督管理局",
        ],
        "skip_company_type": False,
        "company_types": [
            "有限责任公司（自然人投资或控股）",
            "有限责任公司(自然人投资或控股)",
        ],
        "resolution_labels": ["章程决议日期"],
        "investor_type": "自然人股东",
        "investor_type_labels": ["投资人类型", "合伙人类型"],
        "economic_parts": ["内资企业", "有限责任公司", "其他有限责任公司"],
        "investment_method": "货币",
        # subscription_end: plus_years | today | yesterday
        "subscription_end": "plus_years",
        "subscription_years": 4,
        "is_sole_prop": False,
        "is_partnership": False,
        "is_limited_partnership": False,
        "is_general_partnership": False,
        "is_shares": False,
        "is_individual": False,
        "skip_board": False,
    }

    if org in ("有限公司", "有限责任公司", ""):
        return p

    if org in ("股份公司", "股份有限公司"):
        p.update(
            {
                "is_shares": True,
                "company_types": [
                    "股份有限公司（上市、自然人投资或控股）",
                    "股份有限公司(上市、自然人投资或控股)",
                    "股份有限公司（上市，自然人投资或控股）",
                    "股份有限公司(上市，自然人投资或控股)",
                ],
                "economic_parts": ["内资企业", "股份有限公司", "非国有控股非上市企业"],
                "subscription_end": "yesterday",
            }
        )
        return p

    if org == "个体工商户":
        p.update(
            {
                "is_individual": True,
                "company_types": [
                    "有限责任公司（自然人独资）",
                    "有限责任公司(自然人独资)",
                ],
                "economic_parts": ["个体经营", "个体工商户", "内资个体"],
            }
        )
        return p

    if org == "个人独资企业":
        p.update(
            {
                "is_sole_prop": True,
                "skip_board": True,
                "agent_types": ["投资人"],
                "skip_company_type": True,
                "company_types": [],
                "economic_parts": ["个体经营", "个人", "内资个人"],
                "investment_method": "个人财产出资",
                "subscription_end": "today",
            }
        )
        return p

    if org == "有限合伙企业":
        p.update(
            {
                "is_partnership": True,
                "is_limited_partnership": True,
                "skip_board": True,
                "agent_types": ["执行事务合伙人或委派代表", "执行事务合伙人"],
                "skip_company_type": True,
                "company_types": [],
                "resolution_labels": ["合伙协议决议日期", "合伙协议日期", "章程决议日期"],
                "economic_parts": ["个体经营", "个人合伙", "内资合伙"],
            }
        )
        return p

    if org == "普通合伙企业":
        p.update(
            {
                "is_partnership": True,
                "is_general_partnership": True,
                "skip_board": True,
                "agent_types": ["执行事务合伙人或委派代表", "执行事务合伙人"],
                "skip_company_type": True,
                "company_types": [],
                "resolution_labels": ["合伙协议决议日期", "合伙协议日期", "章程决议日期"],
                "economic_parts": ["个体经营", "个人合伙", "内资合伙"],
            }
        )
        return p

    return p


def subscription_dates(profile: dict[str, Any]) -> tuple[date, date]:
    """返回 (开始日, 认缴出资时间/结束日)。"""
    today = date.today()
    mode = profile.get("subscription_end") or "plus_years"
    if mode == "yesterday":
        end = today - timedelta(days=1)
        return today, end
    if mode == "today":
        return today, today
    years = int(profile.get("subscription_years") or 4)
    try:
        end = today.replace(year=today.year + years)
    except ValueError:
        end = today.replace(year=today.year + years, day=28)
    return today, end
