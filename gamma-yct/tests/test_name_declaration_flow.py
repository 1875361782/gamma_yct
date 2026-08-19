import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.api import statuses, store
from apps.api.payload import validate_name_declaration


class NameDeclarationFlowTests(unittest.TestCase):
    def test_name_payload_accepts_fixed_mvp_values(self):
        payload = {
            "enterpriseType": "limited_company",
            "preApproval": False,
            "name": {
                "adminDivision": "上海市",
                "tradeName": "盛表玛",
                "industry": "科技",
                "orgForm": "有限公司",
            },
        }
        self.assertEqual(validate_name_declaration(payload), [])

    def test_name_payload_rejects_wrong_values(self):
        payload = {
            "enterpriseType": "limited_company",
            "preApproval": False,
            "name": {
                "adminDivision": "北京市",
                "tradeName": "其他字号",
                "industry": "科技",
                "orgForm": "有限公司",
            },
        }
        errors = validate_name_declaration(payload)
        self.assertIn("名称区划一期仅支持上海市", errors)
        self.assertIn("名称字号必须为盛表玛", errors)

    def test_name_case_is_seeded_without_full_registration_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(store, "DATA_DIR", Path(tmp)):
                case = store.create_case(flow="name_declaration_v1")
        self.assertEqual(case["flow"], "name_declaration_v1")
        self.assertEqual(case["payload"]["name"]["tradeName"], "盛表玛")
        self.assertEqual(case["payload"]["name"]["industry"], "科技")
        self.assertEqual(case["payload"]["name"]["orgForm"], "有限公司")
        self.assertEqual(case["payload"]["registration"]["address"]["detail"], "")

    def test_name_next_reached_is_terminal(self):
        self.assertIn(statuses.NAME_NEXT_REACHED, statuses.TERMINAL)


if __name__ == "__main__":
    unittest.main()
