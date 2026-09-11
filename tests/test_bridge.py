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

    def test_notice_without_price_is_not_a_free_auction(self):
        row, detail = self.fixture()
        del detail['exportObject']['structuredObject']['notice']['lots'][0]['priceMin']
        feed, counts = bridge.build_feed([row], {row['href']: detail})
        self.assertEqual(feed['lots'], [])
        self.assertEqual(counts['price_missing'], 1)

    def test_deadline_does_not_change_source_version(self):
        row, detail = self.fixture()
        before, _ = bridge.build_feed([row], {row['href']: detail}, current=datetime(2026, 9, 11, tzinfo=timezone.utc))
        after, _ = bridge.build_feed([row], {row['href']: detail}, current=datetime(2026, 10, 11, tzinfo=timezone.utc))
        self.assertEqual(before["lots"], after["lots"])
        self.assertNotEqual(before["generated_at"], after["generated_at"])

    def test_modified_date_takes_precedence_over_publication(self):
        row, detail = self.fixture()
        row['lastUpdateDate'] = '2026-09-11T11:00:00Z'
        feed, _ = bridge.build_feed([row], {row['href']: detail})
        self.assertEqual(feed['lots'][0]['source_updated_at'], '2026-09-11T11:00:00Z')


if __name__ == "__main__":
    unittest.main()

class PropertyTests(unittest.TestCase):
    def test_structured_property_categories_override_incidental_words(self):
        self.assertEqual(bridge._category({'lotName':'Машино-место для автомобиля'}, {}), 'parking')
        self.assertEqual(bridge._category({'lotName':'Право водопользования участком акватории'}, {}), 'other')
        self.assertEqual(bridge._category({'lotDescription':'Здание на земельном участке'}, {'category':{'name':'Нежилое помещение'}}), 'commercial')

    def test_mixed_land_procedure_is_not_evidence_of_lease_price(self):
        notice={'commonInfo':{'biddType':{'code':'ZK','name':'Аренда и продажа земельных участков'}}}
        self.assertEqual(bridge._transaction(notice, {'lotName':'Продажа земельного участка'})[0], 'sale')
        self.assertEqual(bridge._transaction(notice, {'lotName':'Земельный участок','additionalDetails':[{'value':{'name':'Договор аренды'}}]})[0], 'rent_unspecified')
        self.assertEqual(bridge._transaction(notice, {'lotDescription':'Ежегодная арендная плата'})[0], 'annual_rent')
        self.assertEqual(bridge._procedure({'commonInfo':{'biddType':{'code':'229FZ'}}}, {}), 'seized')

    def test_only_document_ids_bound_to_official_attachments_are_exposed(self):
        payload={'exportObject':{'attachments':[{'contentId':'a','URL':'https://torgi.gov.ru/new/file-store/v1/a'}, {'contentId':'b','URL':'https://evil.example/x'}]}}
        lot={'docs':[{'id':'a','name':'Извещение.pdf'},{'id':'b','name':'Bad'},{'id':'missing','name':'Missing'}]}
        self.assertEqual(bridge._documents(payload, {}, lot), [{'title':'Извещение.pdf','url':'https://torgi.gov.ru/new/file-store/v1/a'}])

    def test_notice_and_cancellation_are_both_retained(self):
        rows=[{'regNum':'N','href':'https://torgi.gov.ru/n','subjectEstateCode':50,'documentType':'notice','publishDate':'2026-09-01'}, {'regNum':'N','href':'https://torgi.gov.ru/c','subjectEstateCode':50,'documentType':'noticeCancel','publishDate':'2026-09-02'}]
        selected, _=bridge.select_rows([rows],10)
        self.assertEqual(len(selected),2)
        events=bridge.cancellation_events(selected, {'https://torgi.gov.ru/c':{'exportObject':{'structuredObject':{'noticeCancel':{'commonInfo':{'noticeNumber':'N','publishDate':'2026-09-02T10:00:00Z'}}}}}})
        self.assertEqual(events,[{'procedure_id':'N','external_id':None,'source_updated_at':'2026-09-02T10:00:00Z'}])
