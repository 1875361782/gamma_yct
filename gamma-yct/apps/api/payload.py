"""Payload models aligned with 总体方案 §8."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class NameAlternative(BaseModel):
    tradeName: str = ""
    industry: str = ""
    orgForm: str = "有限公司"


class NameBlock(BaseModel):
    adminDivision: str = "上海市"
    tradeName: str = ""
    industry: str = ""
    orgForm: str = "有限公司"
    fullNamePreview: str = ""
    alternatives: list[NameAlternative] = Field(default_factory=list)


class AddressBlock(BaseModel):
    province: str = "上海市"
    city: str = "上海市"
    district: str = ""
    detail: str = ""
    propertyCertNo: str = ""
    postalCode: str = ""


class RegistrationBlock(BaseModel):
    district: str = ""
    addressType: Literal["centralized", "lease", "self_owned", "other"] = "lease"
    isCentralizedVenue: bool = False
    address: AddressBlock = Field(default_factory=AddressBlock)
    authority: str = ""
    acceptPoint: str = ""
    preferredProcess: str = "full_electronic"
    addressProofFileIds: list[str] = Field(default_factory=list)


class CompanyBlock(BaseModel):
    registeredCapital: float = 0
    capitalCurrency: str = "CNY"
    capitalUnit: str = "万元"
    businessTermType: Literal["long_term", "fixed"] = "long_term"
    businessTermYears: Optional[int] = None
    businessScope: str = ""
    businessScopeCodes: list[str] = Field(default_factory=list)
    # 用户勾选的经营范围条目名称（填报时按名称在政务网搜索添加）
    businessScopeNames: list[str] = Field(default_factory=list)


class Investor(BaseModel):
    type: Literal["natural_person", "enterprise"] = "natural_person"
    name: str = ""
    idType: str = "id_card"
    idNumber: str = ""
    mobile: str = ""
    amount: float = 0
    ratio: float = 0
    investmentMethod: str = "货币"
    address: str = ""
    ethnicity: str = ""
    # 个人独资企业自然人扩展字段
    postalCode: str = ""
    politicalStatus: str = ""
    education: str = ""
    priorOccupation: str = ""
    # 合伙企业合伙人扩展字段
    isExecutivePartner: bool = False
    isLicenseHolder: bool = False
    # 有限合伙：有限责任 / 无限责任
    liabilityType: str = ""


class PersonBrief(BaseModel):
    name: str = ""
    idType: str = "id_card"
    idNumber: str = ""
    mobile: str = ""
    fromInvestorIndex: Optional[int] = None


class LiaisonOfficer(BaseModel):
    name: str = ""
    idType: str = "id_card"
    idNumber: str = ""
    mobile: str = ""
    email: str = ""
    address: str = ""


class BeneficialOwner(BaseModel):
    name: str = ""
    mobile: str = ""
    idValidUntil: str = ""
    ownershipRatio: float = 0
    address: str = ""


class PersonnelBlock(BaseModel):
    legalRepresentative: PersonBrief = Field(default_factory=PersonBrief)
    directors: list[PersonBrief] = Field(default_factory=list)
    supervisors: list[PersonBrief] = Field(default_factory=list)
    establishSupervisor: bool = False
    manager: Optional[PersonBrief] = None
    financeOfficer: Optional[PersonBrief] = None
    liaisonOfficer: Optional[LiaisonOfficer] = None
    # 合伙企业填报默认：执行事务合伙人 / 电子营业执照持照人姓名列表
    executivePartnerNames: list[str] = Field(default_factory=list)
    licenseHolderNames: list[str] = Field(default_factory=list)


class SealsBlock(BaseModel):
    needPhysical: bool = True
    types: list[str] = Field(default_factory=lambda: ["公章", "财务章", "法人章"])
    deliveryInfo: dict[str, Any] = Field(default_factory=dict)


class TaxBlock(BaseModel):
    needInvoice: bool = True
    invoiceTypes: list[str] = Field(default_factory=list)


class SocialInsuranceBlock(BaseModel):
    needEmployeeInsurance: bool = False


class HousingFundBlock(BaseModel):
    need: bool = False


class BankAppointmentBlock(BaseModel):
    need: bool = False
    bankName: Optional[str] = None
    branchName: Optional[str] = None


class IdCardAttachment(BaseModel):
    personKey: str = ""
    personName: str = ""
    roles: list[str] = Field(default_factory=list)
    frontFileId: str = ""
    backFileId: str = ""


class AttachmentsBlock(BaseModel):
    idCards: list[IdCardAttachment] = Field(default_factory=list)
    addressProof: list[str] = Field(default_factory=list)
    other: list[str] = Field(default_factory=list)


class CasePayload(BaseModel):
    schemaVersion: str = "1.0"
    enterpriseType: str = "limited_company"
    preApproval: bool = False
    name: NameBlock = Field(default_factory=NameBlock)
    registration: RegistrationBlock = Field(default_factory=RegistrationBlock)
    company: CompanyBlock = Field(default_factory=CompanyBlock)
    investors: list[Investor] = Field(default_factory=list)
    personnel: PersonnelBlock = Field(default_factory=PersonnelBlock)
    beneficialOwner: BeneficialOwner = Field(default_factory=BeneficialOwner)
    seals: SealsBlock = Field(default_factory=SealsBlock)
    tax: TaxBlock = Field(default_factory=TaxBlock)
    socialInsurance: SocialInsuranceBlock = Field(default_factory=SocialInsuranceBlock)
    housingFund: HousingFundBlock = Field(default_factory=HousingFundBlock)
    bankAppointment: BankAppointmentBlock = Field(default_factory=BankAppointmentBlock)
    attachments: AttachmentsBlock = Field(default_factory=AttachmentsBlock)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def validate_payload(data: dict[str, Any]) -> list[str]:
    """Return list of error messages; empty means OK."""
    errors: list[str] = []
    try:
        p = CasePayload.model_validate(data)
    except Exception as e:
        return [f"payload 结构无效: {e}"]

    if p.enterpriseType != "limited_company":
        errors.append("一期仅支持内资有限责任公司（enterpriseType=limited_company）")
    if p.preApproval:
        errors.append("一期仅支持不涉及前置审批（preApproval 须为 false）")

    n = p.name
    if not n.tradeName.strip():
        errors.append("企业名称字号不能为空")
    if not n.industry.strip():
        errors.append("行业特点不能为空")
    if not n.orgForm.strip():
        errors.append("组织形式不能为空")

    r = p.registration
    if not r.district.strip() and not r.address.district.strip():
        errors.append("住所所在区不能为空")
    if not r.address.detail.strip():
        errors.append("住所详细地址不能为空")
    # 与 yct「住所填报」一致：非集中登记地不强制上传扫描件；
    # 官方弹窗必填为完整地址 + 邮编；产权证收件编号选填。
    if not r.isCentralizedVenue and r.addressType != "centralized":
        if not (r.address.postalCode or "").strip():
            errors.append("非集中登记地请填写邮政编码（address.postalCode）")

    if p.company.registeredCapital <= 0:
        errors.append("注册资本须大于 0")
    if not p.company.businessScope.strip():
        errors.append("经营范围不能为空")

    if not p.investors:
        errors.append("至少一名股东")
    else:
        ratio_sum = sum(i.ratio for i in p.investors)
        if abs(ratio_sum - 100) > 0.01:
            errors.append(f"股东出资比例合计须为 100%，当前为 {ratio_sum}")
        for i, inv in enumerate(p.investors):
            if not inv.name.strip():
                errors.append(f"股东[{i}]姓名不能为空")
            if not inv.idNumber.strip():
                errors.append(f"股东[{i}]证件号不能为空")
            if inv.mobile and len(inv.mobile) != 11:
                errors.append(f"股东[{i}]手机号格式不正确")

    lr = p.personnel.legalRepresentative
    if not lr.name.strip():
        errors.append("法定代表人姓名不能为空")
    if not lr.idNumber.strip():
        errors.append("法定代表人证件号不能为空")

    id_cards = p.attachments.idCards
    if not id_cards:
        errors.append("请上传法定代表人等身份证件")
    else:
        for card in id_cards:
            if not card.frontFileId or not card.backFileId:
                errors.append(f"证件附件不完整（{card.personKey or 'unknown'}）")

    return errors


def validate_name_declaration(data: dict[str, Any]) -> list[str]:
    """Validate only the fields needed by the name-declaration flow."""
    errors: list[str] = []
    if data.get("enterpriseType") != "limited_company":
        errors.append("名称申报一期仅支持有限责任公司")
    if data.get("preApproval") is not False:
        errors.append("名称申报一期仅支持不涉及前置审批事项")

    name = data.get("name") or {}
    if not str(name.get("adminDivision") or "").strip():
        errors.append("名称区划不能为空")
    if str(name.get("adminDivision") or "").strip() not in {"上海", "上海市"}:
        errors.append("名称区划一期仅支持上海市")
    if str(name.get("tradeName") or "").strip() != "盛表玛":
        errors.append("名称字号必须为盛表玛")
    if str(name.get("industry") or "").strip() != "科技":
        errors.append("行业表述必须为科技")
    if str(name.get("orgForm") or "").strip() != "有限公司":
        errors.append("组织形式必须为有限公司")
    return errors
