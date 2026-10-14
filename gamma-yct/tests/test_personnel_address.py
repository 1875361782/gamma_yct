import unittest

from apps.worker.playbooks.personnel import (
    _addr_option_variants,
    _id_card_region_parts,
    _parse_cn_region_address,
    _resolve_bo_user_address,
)


class PersonnelAddressTests(unittest.TestCase):
    def test_parse_shanghai_address_without_province_or_city(self):
        result = _parse_cn_region_address("浦东新区南汇新城镇沪城环路999号")

        self.assertEqual(result["province"], "上海市")
        self.assertEqual(result["city"], "上海市")
        self.assertEqual(result["district"], "浦东新区")
        self.assertEqual(result["detail"], "南汇新城镇沪城环路999号")

    def test_parse_full_shanghai_address(self):
        result = _parse_cn_region_address("上海市浦东新区金桥镇金桥路1480号")

        self.assertEqual(result["province"], "上海市")
        self.assertEqual(result["city"], "上海市")
        self.assertEqual(result["district"], "浦东新区")
        self.assertEqual(result["detail"], "金桥镇金桥路1480号")

    def test_shanghai_city_cascade_uses_city_area_option(self):
        city = "上海市"
        city_options = _addr_option_variants(city)
        city_options = ("市辖区",) + tuple(
            option for option in city_options if option != "市辖区"
        )

        self.assertEqual(city_options[0], "市辖区")

    def test_parse_anhui_wuwei_address(self):
        result = _parse_cn_region_address(
            "安徽省无为市昆山镇车门行政村叶洼自然村42-1号"
        )
        self.assertEqual(result["province"], "安徽省")
        self.assertEqual(result["city"], "无为市")
        self.assertIn("昆山镇", result["detail"])

    def test_id_card_anhui_wuwei(self):
        parts = _id_card_region_parts("342623199508086318")
        self.assertEqual(parts["province"], "安徽省")
        self.assertEqual(parts["city"], "芜湖市")
        self.assertEqual(parts["district"], "无为市")

    def test_resolve_prefers_frontend_hukou(self):
        payload = {
            "investors": [
                {
                    "name": "吴伟康",
                    "idNumber": "342623199508086318",
                    "idAddress": "安徽省无为市昆山镇车门行政村叶洼自然村42-1号",
                }
            ]
        }
        bo = {
            "name": "吴伟康",
            "hukouProvince": "安徽省",
            "hukouCity": "芜湖市",
            "hukouDistrict": "无为市",
        }
        addr, parts = _resolve_bo_user_address(payload, bo)
        self.assertEqual(parts["country"], "中国")
        self.assertEqual(parts["province"], "安徽省")
        self.assertEqual(parts["city"], "芜湖市")
        self.assertEqual(parts["district"], "无为市")
        self.assertTrue(parts.get("_hukou_locked"))
        self.assertIn("无为市", addr)


if __name__ == "__main__":
    unittest.main()
