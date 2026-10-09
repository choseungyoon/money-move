"""단지별 집계·소득·카드·지역 상세 파일."""
import csv, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from sources import molit, nts_income, card  # noqa: E402
import build_detail as bd  # noqa: E402
from test_molit import FakeGetter, xml  # noqa: E402

GEO = os.path.join(os.path.dirname(__file__), "..", "data", "regions_geo.json")


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


class ComplexTest(unittest.TestCase):
    def test_complex_id_prefers_aptseq_else_composite(self):
        self.assertEqual(molit.complex_id("11680", {"aptSeq": "11680-123", "aptNm": "A"}), "11680-123")
        a = molit.complex_id("11680", {"umdNm": "대치동", "jibun": "1", "aptNm": "래미안 대치"})
        b = molit.complex_id("11680", {"umdNm": "대치동", "jibun": "1", "aptNm": "래미안대치"})
        c = molit.complex_id("11680", {"umdNm": "도곡동", "jibun": "1", "aptNm": "래미안대치"})
        self.assertEqual(a, b)      # 띄어쓰기 차이는 같은 단지
        self.assertNotEqual(b, c)   # 다른 동의 동명 단지는 다른 단지

    def test_complex_totals_equal_region_and_refetch_drops_cancelled(self):
        deal = lambda name, amt, **kw: {"aptNm": name, "umdNm": "대치동", "jibun": "1", "dealAmount": amt, "excluUseAr": "84", **kw}
        first = {("AptTrade", "11680", 1): xml([deal("A", "100,000"), deal("A", "120,000"), deal("B", "90,000")])}
        # 다시 받았을 때 B 거래가 해제되어 빠졌다
        second = {("AptTrade", "11680", 1): xml([deal("A", "100,000"), deal("A", "120,000"), deal("B", "90,000", cdealType="O")])}
        with tempfile.TemporaryDirectory() as d:
            molit.fetch(molit.Client("K", getter=FakeGetter(first)), ["11680"], ["2025-03"], d)
            ct = read(os.path.join(d, "complex_trade_month.csv")); rt = read(os.path.join(d, "trade_region_month.csv"))
            self.assertEqual(sum(int(r["trades"]) for r in ct), int(rt[0]["trades"]))
            self.assertAlmostEqual(sum(float(r["value_eok"]) for r in ct), float(rt[0]["value_eok"]), places=2)
            molit.fetch(molit.Client("K", getter=FakeGetter(second)), ["11680"], ["2025-03"], d)
            names = {r["cid"].split("|")[-1] for r in read(os.path.join(d, "complex_trade_month.csv"))}
            self.assertEqual(names, {"A"})  # 행 단위 덮어쓰기였다면 B가 남았을 것
            self.assertEqual(len(read(os.path.join(d, "complexes.csv"))), 2)  # 단지 정보는 누적


class IncomeCardTest(unittest.TestCase):
    def test_income_matches_names_and_marks_city_level(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.csv"), os.path.join(d, "out.csv")
            with open(src, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f); w.writerow(list(nts_income.COLS.values()))
                w.writerow(["2024", "서울특별시", "강남구", "250,000", "20,000,000"])   # 총급여 백만원
                w.writerow(["2024", "경기도", "성남시", "400,000", "24,000,000"])       # 구별 없이 시 단위
                w.writerow(["2024", "부산광역시", "해운대구", "1", "1"])                 # 수도권 아님
            nts_income.load(src, dst, GEO)
            rows = {r["code"]: r for r in read(dst)}
        self.assertEqual(rows["11680"]["avg_pay_manwon"], "8000")
        self.assertEqual({c for c in rows if c.startswith("4113")}, {"41131", "41133", "41135"})
        self.assertEqual(rows["41135"]["level"], "city")

    def test_card_rejects_statistics_korea_codes(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "c.csv")
            with open(src, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f); w.writerow(list(card.SEOUL_COLS.values()))
                w.writerow(["202506", "1168064000", "1,000,000,000", "10"])
                w.writerow(["202506", "1168065000", "500,000,000", "5"])
            rows = card.seoul(src)
            self.assertEqual(rows, [["2025-06", "11680", 15.0, 15, "resident"]])
            with open(src, "a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(["202506", "11230510", "1", "1"])  # 통계청 8자리
            with self.assertRaises(ValueError):
                card.seoul(src)


class DetailFileTest(unittest.TestCase):
    def test_detail_files_keep_totals_and_unknown_complexes(self):
        with tempfile.TemporaryDirectory() as d:
            inter, out = os.path.join(d, "interim"), os.path.join(d, "detail"); os.makedirs(inter)
            def w(name, header, rows):
                with open(os.path.join(inter, name), "w", newline="", encoding="utf-8") as f:
                    x = csv.writer(f); x.writerow(header); x.writerows(rows)
            w("complexes.csv", molit.COMPLEX_COLS, [["11680", "c1", "A단지", "대치동", "2001"]])
            w("complex_trade_month.csv", molit.CTRADE_COLS, [["2025-03", "11680", "c1", 2, 30.0, 168], ["2025-03", "11680", "c9", 1, 10.0, 84]])
            old = (bd.INTERIM, bd.OUT); bd.INTERIM, bd.OUT = inter, out
            try:
                bd.build(["2025-03"], [{"code": "11680", "name": "강남구"}])
            finally:
                bd.INTERIM, bd.OUT = old
            doc = json.load(open(os.path.join(out, "11680.json"), encoding="utf-8"))
        self.assertEqual(sum(r[2] for r in doc["trade"]), 3)       # 단지 정보에 없는 c9도 버리지 않는다
        self.assertEqual(len(doc["complexes"]), 2)
        self.assertIsNone(doc["income"]); self.assertIsNone(doc["card"])


if __name__ == "__main__":
    unittest.main()
