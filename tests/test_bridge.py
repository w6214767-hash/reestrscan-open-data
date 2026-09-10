import unittest
from datetime import datetime, timezone

import bridge


class BridgeTests(unittest.TestCase):
    def fixture(self):
        row = {
            "regNum": "21000000010000000001",
            "href": "https://torgi.gov.ru/new/opendata/detail.json",
            "publishDate": "2026-09-10T10:00:00Z",
            "subjectEstateCode": "50",
        }
        detail = {
            "exportObject": {
                "structuredObject": {
                    "notice": {
                        "commonInfo": {"publishDate": "2026-09-10T10:00:00Z"},
                        "bidderOrg": {"orgInfo": {"name": "Комитет имущества"}},
                        "lots": [{
                            "lotNumber": 1,
                            "lotName": "Земельный участок",
                            "lotDescription": "Продажа участка",
                            "lotStatus": "PUBLISHED",
                            "priceMin": "1000000.50",
                            "deposit": {"value": "100000"},
                            "biddConditions": {"biddEndTime": "2026-09-20T12:00:00Z"},
                            "biddingObjectInfo": {
                                "subjectRF": {"code": "50", "name": "Московская область"},
                                "estateAddress": "Московская область, Домодедово, 50:28:0000000:123",
                                "estateArea": "1200",
                                "estateAddressFIAS": {
                                    "hierarchyObjects": [{
                                        "level": {"code": "3"},
                                        "name": "Домодедово",
                                    }]
                                },
                            },
                        }],
                    }
                }
            }
        }
        return row, detail

    def test_mapper_builds_contract_feed(self):
        row, detail = self.fixture()
        feed, counts = bridge.build_feed(
            [row],
            {row["href"]: detail},
            current=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )
        self.assertEqual(feed["source_id"], "gis-torgi")
        self.assertEqual(counts["lots"], 1)
        lot = feed["lots"][0]
        self.assertEqual(lot["region"], "moskovskaya-oblast")
        self.assertEqual(lot["municipality"], "domodedovo")
        self.assertEqual(lot["category"], "zemlya")
        self.assertEqual(lot["price_minor"], 100000050)
        self.assertEqual(lot["assets"][0]["cadastral_number"], "50:28:0000000:123")

    def test_discovery_uses_recent_data_only(self):
        meta = {"data": [
            {"source": "data-20260908.json", "created": "2026-09-08"},
            {"source": "data-20260910.json", "created": "2026-09-10"},
            {"source": "structure-20240401.json", "created": "2026-09-11"},
            {"source": "data-20260909.json", "created": "2026-09-09"},
        ]}
        self.assertEqual(
            bridge.discover_sources(meta, 2),
            [
                "https://torgi.gov.ru/new/opendata/7710568760-notice/data-20260910.json",
                "https://torgi.gov.ru/new/opendata/7710568760-notice/data-20260909.json",
            ],
        )

    def test_row_selection_is_region_limited_and_deduplicated(self):
        rows = [
            {"regNum": "A", "href": "https://torgi.gov.ru/a", "subjectEstateCode": 50, "publishDate": "2026-09-01"},
            {"regNum": "A", "href": "https://torgi.gov.ru/b", "subjectEstateCode": 50, "publishDate": "2026-09-02"},
            {"regNum": "B", "href": "https://torgi.gov.ru/c", "subjectEstateCode": 77, "publishDate": "2026-09-03"},
            {"regNum": "C", "href": "https://torgi.gov.ru/d", "subjectEstateCode": 78, "publishDate": "2026-09-04"},
        ]
        selected, counts = bridge.select_rows([{"listObjects": rows}], 10)
        self.assertEqual([row["regNum"] for row in selected], ["B", "A"])
        self.assertEqual(counts["unique_notices"], 2)

    def test_off_origin_is_rejected(self):
        with self.assertRaises(bridge.BridgeError):
            bridge._approved_url("https://example.com/data.json")


if __name__ == "__main__":
    unittest.main()
