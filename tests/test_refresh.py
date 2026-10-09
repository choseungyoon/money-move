"""캐시 재사용 정책, 묶음 단위 교체, 변경 기록, 해제 시차 분석."""
import csv, os, sys, tempfile, time, unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from sources import molit  # noqa: E402
import cancel_lag  # noqa: E402
from test_molit import FakeGetter, xml  # noqa: E402

NOW = time.mktime((2026, 10, 9, 12, 0, 0, 0, 0, -1))
DAY = 86400


def deal(**kw):
    it = {"aptNm": "A", "umdNm": "대치동", "jibun": "1", "floor": "5", "excluUseAr": "84",
          "dealYear": "2026", "dealMonth": "3", "dealDay": "10", "dealAmount": "100,000"}
    it.update(kw)
    return it


class PolicyTest(unittest.TestCase):
    def test_tiers_by_contract_month_age(self):
        p = molit.RefreshPolicy(now=NOW)
        self.assertEqual(p.age_months("2026-10"), 0)
        self.assertEqual(p.ttl_days("2026-07"), 1)    # 3개월 전: 뜨거운 구간
        self.assertEqual(p.ttl_days("2026-06"), 30)   # 4개월 전: 따뜻한 구간
        self.assertEqual(p.ttl_days("2025-10"), 30)   # 12개월 전
        self.assertEqual(p.ttl_days("2025-09"), 180)  # 13개월 전: 차가운 구간
        self.assertTrue(p.is_fresh("2025-01", NOW - 100 * DAY))
        self.assertFalse(p.is_fresh("2026-08", NOW - 2 * DAY))


class ClientRefreshTest(unittest.TestCase):
    def run_fetch(self, d, routes, ym, cached_days_ago=None):
        g = FakeGetter(routes)
        c = molit.Client("K", cache_dir=d, policy=molit.RefreshPolicy(now=NOW), getter=g,
                         change_log=os.path.join(d, "changes.csv"))
        if cached_days_ago is not None:  # 캐시 파일의 받은 시각을 과거로 돌린다
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(".xml"):
                        os.utime(os.path.join(root, f), (NOW - cached_days_ago * DAY,) * 2)
        items = list(c.items(molit.TRADE, "11680", ym))
        return items, len(g.urls)

    def test_old_month_uses_cache_recent_month_refetches(self):
        with tempfile.TemporaryDirectory() as d:
            r = {("AptTrade", "11680", 1): xml([deal()])}
            self.run_fetch(d, r, "2025-03")
            _, calls = self.run_fetch(d, r, "2025-03", cached_days_ago=60)   # 19개월 전 계약, 60일 전 캐시
            self.assertEqual(calls, 0)
            self.run_fetch(d, r, "2026-08")
            _, calls = self.run_fetch(d, r, "2026-08", cached_days_ago=2)    # 2개월 전 계약, 2일 전 캐시
            self.assertEqual(calls, 1)
            _, calls = self.run_fetch(d, r, "2026-08", cached_days_ago=0.1)  # 같은 날 재실행
            self.assertEqual(calls, 0)

    def test_refetch_replaces_all_pages_and_logs_changes(self):
        with tempfile.TemporaryDirectory() as d:
            many = [deal(dealDay=str(k % 28 + 1), floor=str(k)) for k in range(1001)]
            first = {("AptTrade", "11680", 1): xml(many[:1000], total=1001), ("AptTrade", "11680", 2): xml(many[1000:], total=1001)}
            self.run_fetch(d, first, "2026-08")
            # 다시 받았더니: 1건 해제 표시, 1건 가격 정정, 2건 사라져 999건 → 1페이지로 줄어듦
            again = [dict(x) for x in many[:999]]
            again[0]["cdealType"], again[0]["cdealDay"] = "O", "26.09.01"
            again[1]["dealAmount"] = "99,000"
            second = {("AptTrade", "11680", 1): xml(again, total=999)}
            items, calls = self.run_fetch(d, second, "2026-08", cached_days_ago=3)
            self.assertEqual((len(items), calls), (999, 1))
            self.assertFalse(os.path.exists(os.path.join(d, "RTMSDataSvcAptTrade", "11680_202608_2.xml")))  # 옛 2페이지 삭제
            with open(os.path.join(d, "changes.csv"), encoding="utf-8") as f:
                log = list(csv.DictReader(f))
            self.assertEqual({k: log[-1][k] for k in ("age_months", "old_n", "new_n", "removed", "cancelled", "price_changed")},
                             {"age_months": "2", "old_n": "1001", "new_n": "999", "removed": "2", "cancelled": "1", "price_changed": "1"})

    def test_failed_refetch_keeps_old_cache(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_fetch(d, {("AptTrade", "11680", 1): xml([deal()])}, "2026-08")
            c = molit.Client("K", cache_dir=d, policy=molit.RefreshPolicy(now=NOW + 3 * DAY), getter=lambda u: xml([], code="22"))
            with self.assertRaises(RuntimeError):
                list(c.items(molit.TRADE, "11680", "2026-08"))
            with open(os.path.join(d, "RTMSDataSvcAptTrade", "11680_202608_1.xml"), "rb") as f:
                self.assertEqual(len(molit.parse(f.read())[0]), 1)


class CancelLagTest(unittest.TestCase):
    def test_parse_day_formats(self):
        for s in ("26.09.01", "20260901", "2026-09-01"):
            self.assertEqual(cancel_lag.parse_day(s), date(2026, 9, 1))
        self.assertIsNone(cancel_lag.parse_day(""))

    def test_lags_and_recommendation(self):
        with tempfile.TemporaryDirectory() as d:
            items = [deal(cdealType="O", cdealDay=f"26.{m:02d}.10") for m in (4, 4, 5, 9)] + [deal()]
            os.makedirs(os.path.join(d, "RTMSDataSvcAptTrade"))
            with open(os.path.join(d, "RTMSDataSvcAptTrade", "11680_202603_1.xml"), "wb") as f:
                f.write(xml(items))
            lags = cancel_lag.cancel_lags(d)
        self.assertEqual(lags, [31, 31, 61, 184])
        self.assertEqual(cancel_lag.recommend(lags, 0.99), 8)  # (184 + 30일) / 30.44 → 8개월


if __name__ == "__main__":
    unittest.main()
