"""인구이동 추정(IPF)과 KOSIS 월별 총계 수집."""
import json, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import estimate_od as eo  # noqa: E402
from sources import kosis  # noqa: E402

A, B, C, NC = "11680", "41135", "28185", "00000"


def od(ym, cells):
    return [{"ym": ym, "src": s, "dst": d, "persons": str(h), "persons_all": str(a)} for (s, d), (h, a) in cells.items()]


class EstimateTest(unittest.TestCase):
    def setUp(self):
        base = {(A, B): (30, 100), (B, A): (10, 50), (A, C): (5, 20), (C, B): (8, 40), (NC, A): (6, 30), (B, NC): (4, 20),
                (A, A): (90, 200), (B, B): (60, 150), (C, C): (20, 60)}
        self.od = od("2025-11", base) + od("2025-12", base)
        # 2026-01 '실제' 행렬: 강남→분당이 늘고 전체가 조금 늘었다. 총계는 이 행렬에서 계산해 서로 모순이 없다.
        truth = {k: a * 1.1 for k, (h, a) in base.items()}
        truth[(A, B)] *= 1.5; truth[(A, A)] = 220; truth[(B, B)] = 160; truth[(C, C)] = 65
        self.truth = truth
        mg = {}
        for (s, d), v in truth.items():
            if d != NC:
                mg.setdefault(d, [0, 0, 0])[0] += v
            if s != NC:
                mg.setdefault(s, [0, 0, 0])[1] += v
            if s == d:
                mg[s][2] += v
        self.margins = [{"ym": "2026-01", "code": c, "in_total": str(round(v[0])), "out_total": str(round(v[1])), "intra": str(round(v[2]))}
                        for c, v in mg.items()] + [{"ym": "2025-12", "code": A, "in_total": "1", "out_total": "1", "intra": "1"}]

    def test_only_months_after_mdis_and_margins_respected(self):
        rows, last, months = eo.estimate(self.od, self.margins)
        self.assertEqual((last, months), ("2025-12", ["2026-01"]))
        m = {(r[1], r[2]): r[4] for r in rows}
        self.assertEqual(m[(A, A)], 220)                                      # 대각선은 KOSIS 시군구 내 이동으로 고정
        out_A = sum(v for (s, d), v in m.items() if s == A and d != A)
        in_B = sum(v for (s, d), v in m.items() if d == B and s != B)
        exp_out_A = sum(v for (s, d), v in self.truth.items() if s == A and d != A)
        exp_in_B = sum(v for (s, d), v in self.truth.items() if d == B and s != B)
        self.assertAlmostEqual(out_A, exp_out_A, delta=2)                    # 총전출 - 시군구 내
        self.assertAlmostEqual(in_B, exp_in_B, delta=2)                      # 총전입 - 시군구 내
        self.assertGreater(m[(A, B)], 100 * 1.1 * 1.2)                        # 늘어난 강남→분당을 따라간다

    def test_housing_uses_cell_ratio(self):
        rows, _, _ = eo.estimate(self.od, self.margins)
        r = next(r for r in rows if (r[1], r[2]) == (A, B))
        self.assertAlmostEqual(r[3] / r[4], 0.3, delta=0.02)                  # 기준 기간 주택 사유 비율 30/100

    def test_requires_mdis_base(self):
        with self.assertRaises(ValueError):
            eo.estimate([], self.margins)


class KosisMarginsTest(unittest.TestCase):
    def test_margins_filter_items_codes_and_chunk(self):
        urls = []
        def getter(url):
            urls.append(url)
            rows = [{"ITM_ID": i, "C1": c, "PRD_DE": "202601", "DT": dt} for i, c, dt in
                    (("T10", "11680", "250"), ("T20", "11680", "400"), ("T30", "11680", "220"), ("T25", "11680", "-150"),
                     ("T10", "41192", "10"), ("T10", "41194", "5"), ("T10", "26110", "999"))]
            return json.dumps(rows).encode()
        rows, missing = kosis.margins("K", "2026-01", "2026-08", ["11680", "41190", "41135"], getter=getter, chunk=6)
        self.assertEqual(len(urls), 2)                                        # 8개월 → 6개월씩 2번 (같은 달이 겹쳐 와도 두 배 안 됨)
        self.assertIn(["2026-01", "11680", 250, 400, 220], rows)
        self.assertIn(["2026-01", "41190", 15, 0, 0], rows)                   # 부천 구 코드 합산
        self.assertEqual(missing, ["41135"])


if __name__ == "__main__":
    unittest.main()
