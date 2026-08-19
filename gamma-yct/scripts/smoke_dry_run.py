"""Quick dry_run smoke test. Run from gamma-yct with API up."""
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"


def main() -> int:
    c = httpx.post(f"{BASE}/api/cases").json()
    cid = c["caseId"]
    print("caseId=", cid)
    jpg = Path("_t.jpg")
    jpg.write_bytes(bytes([0xFF, 0xD8, 0xFF, 0xD9]))
    ids = []
    for _ in range(3):
        r = httpx.post(
            f"{BASE}/api/cases/{cid}/files",
            files={"file": ("t.jpg", jpg.read_bytes(), "image/jpeg")},
        )
        r.raise_for_status()
        ids.append(r.json()["fileId"])
    f1, f2, f3 = ids
    payload = {
        "schemaVersion": "1.0",
        "enterpriseType": "limited_company",
        "preApproval": False,
        "name": {
            "adminDivision": "上海",
            "tradeName": "测测",
            "industry": "科技",
            "orgForm": "有限公司",
            "fullNamePreview": "上海测测科技有限公司",
            "alternatives": [{"tradeName": "测测一", "industry": "科技", "orgForm": "有限公司"}],
        },
        "registration": {
            "district": "浦东新区",
            "addressType": "lease",
            "isCentralizedVenue": False,
            "address": {
                "province": "上海市",
                "city": "上海市",
                "district": "浦东新区",
                "detail": "测试路1号",
                "propertyCertNo": "",
            },
            "authority": "",
            "acceptPoint": "",
            "preferredProcess": "full_electronic",
            "addressProofFileIds": [f3],
        },
        "company": {
            "registeredCapital": 100,
            "capitalCurrency": "CNY",
            "capitalUnit": "万元",
            "businessTermType": "long_term",
            "businessTermYears": None,
            "businessScope": "技术开发、技术咨询",
            "businessScopeCodes": [],
        },
        "investors": [
            {
                "type": "natural_person",
                "name": "张三",
                "idType": "id_card",
                "idNumber": "310101199001011234",
                "mobile": "13800138000",
                "amount": 100,
                "ratio": 100,
                "investmentMethod": "货币",
                "address": "",
            }
        ],
        "personnel": {
            "legalRepresentative": {
                "name": "张三",
                "idType": "id_card",
                "idNumber": "310101199001011234",
                "mobile": "13800138000",
                "fromInvestorIndex": 0,
            },
            "directors": [],
            "supervisors": [],
            "manager": None,
            "financeOfficer": None,
        },
        "seals": {"needPhysical": True, "types": ["公章"], "deliveryInfo": {}},
        "tax": {"needInvoice": True, "invoiceTypes": []},
        "socialInsurance": {"needEmployeeInsurance": False},
        "housingFund": {"need": False},
        "bankAppointment": {"need": False, "bankName": None, "branchName": None},
        "attachments": {
            "idCards": [
                {
                    "personKey": "legalRepresentative",
                    "frontFileId": f1,
                    "backFileId": f2,
                }
            ],
            "addressProof": [f3],
            "other": [],
        },
    }
    httpx.patch(
        f"{BASE}/api/cases/{cid}/payload",
        json={"payload": payload, "wizardStep": 9},
    ).raise_for_status()
    v = httpx.post(f"{BASE}/api/cases/{cid}/validate").json()
    print("validate", v.get("ok"), v.get("errors"))
    if not v.get("ok"):
        return 1
    httpx.post(f"{BASE}/api/cases/{cid}/start").raise_for_status()
    for _ in range(20):
        time.sleep(1)
        g = httpx.get(f"{BASE}/api/cases/{cid}").json()
        print("status", g["status"])
        if g["status"] == "ready_for_user_submit":
            print("qr", g["artifacts"].get("qrImageUrl"))
            print("OK")
            return 0
        if g["status"] in ("failed", "cancelled"):
            print(g)
            return 1
    print("timeout", json.dumps(g, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
