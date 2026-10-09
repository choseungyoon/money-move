"""실거래 수집기: 실제 API 응답 형식의 XML로 파싱·필터·병합을 검증한다."""
import csv, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from sources import molit  # noqa: E402


def xml(items, total=None, code="000"):
    body = "".join("<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in it.items()) + "</item>" for it in items)
    return (f'<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>{code}</resultCode>'
            f"<resultMsg>OK</resultMsg></header><body><items>{body}</items><numOfRows>1000</numOfRows>"
            f"<pageNo>1</pageNo><totalCount>{len(items) if total is None else total}</totalCount></body></response>").encode()


class FakeGetter:
    """URL의 API·LAWD_CD로 응답을 고른다. 호출 횟수를 센다."""
    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, url):
        self.urls.append(url)
        for (api, lawd, page), body in self.routes.items():
            if api in url and f"LAWD_CD={lawd}" in url and f"pageNo={page}" in url:
                return body
        return xml([])


class MolitTest(unittest.TestCase):
    def test_parse_error_code_raises(self):
        with self.assertRaises(RuntimeError):
            molit.parse(xml([], code="30"))

    def test_trade_filters_cancelled_and_counts_corp_and_equity(self):
        acc = molit.defaultdict(lambda: [0, 0.0, 0, 0.0, 0.0])
        molit.add_trade(acc, "2025-03", "11680", {"dealAmount": "300,000", "buyerGbn": "개인"})
        molit.add_trade(acc, "2025-03", "11680", {"dealAmount": "100,000", "buyerGbn": "법인"})
        molit.add_trade(acc, "2025-03", "11680", {"dealAmount": "500,000", "cdealType": "O"})  # 해제
        n, value, corp_n, corp_v, eq = acc[("2025-03", "11680")]
        self.assertEqual((n, corp_n), (2, 1))
        self.assertAlmostEqual(value, 40.0)
        self.assertAlmostEqual(corp_v, 10.0)
        self.assertTrue(0 < eq < value)

    def test_rent_separates_new_and_renewal(self):
        acc = molit.defaultdict(lambda: [0, 0, 0, 0, 0.0])
        molit.add_rent(acc, "2025-03", "41135", {"deposit": "60,000", "monthlyRent": "0", "contractType": "신규"})
        molit.add_rent(acc, "2025-03", "41135", {"deposit": "10,000", "monthlyRent": "120", "contractType": "갱신"})
        self.assertEqual(acc[("2025-03", "41135")][:4], [2, 1, 1, 1])

    def test_fetch_merges_bucheon_codes_paginates_and_keeps_history(self):
        routes = {
            ("AptTrade", "41192", 1): xml([{"dealAmount": "50,000"}] * 2, total=1001),
            ("AptTrade", "41192", 2): xml([{"dealAmount": "50,000"}]),
            ("AptTrade", "41194", 1): xml([{"dealAmount": "40,000"}]),
        }
        with tempfile.TemporaryDirectory() as d:
            # 기존 이력 한 줄이 덮어써지지 않아야 한다
            with open(os.path.join(d, "trade_region_month.csv"), "w", newline="") as f:
                w = csv.writer(f); w.writerow(molit.TRADE_COLS); w.writerow(["2024-01", "11110", 9, 90, 0, 0, 60])
            client = molit.Client("KEY", cache_dir=os.path.join(d, "cache"), getter=FakeGetter(routes))
            molit.fetch(client, ["41190"], ["2025-03"], d)
            with open(os.path.join(d, "trade_region_month.csv"), encoding="utf-8") as f:
                rows = {(r["ym"], r["code"]): r for r in csv.DictReader(f)}
        self.assertIn(("2024-01", "11110"), rows)
        self.assertEqual(rows[("2025-03", "41190")]["trades"], "4")  # 41192 2페이지(3건) + 41194(1건)
        self.assertNotIn(("2025-03", "41192"), rows)

    def test_cache_reused_except_forced_months(self):
        with tempfile.TemporaryDirectory() as d:
            g = FakeGetter({("AptTrade", "11110", 1): xml([{"dealAmount": "10,000"}])})
            c = molit.Client("KEY", cache_dir=d, getter=g)
            list(c.items(molit.TRADE, "11110", "2025-01")); list(c.items(molit.TRADE, "11110", "2025-01"))
            self.assertEqual(len(g.urls), 1)
            c2 = molit.Client("KEY", cache_dir=d, force_months={"2025-01"}, getter=g)
            list(c2.items(molit.TRADE, "11110", "2025-01"))
            self.assertEqual(len(g.urls), 2)

    def test_error_response_is_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            c = molit.Client("KEY", cache_dir=d, getter=lambda url: xml([], code="22"))  # 호출 한도 초과 등
            with self.assertRaises(RuntimeError):
                list(c.items(molit.TRADE, "11110", "2025-01"))
            self.assertEqual(sum(len(f) for _, _, f in os.walk(d)), 0)


if __name__ == "__main__":
    unittest.main()
