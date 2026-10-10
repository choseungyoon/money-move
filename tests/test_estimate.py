"""인구이동 추정(IPF)과 KOSIS 월별 총계 수집."""
import json, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import estimate_od as eo  # noqa: E402
from sources import kosis  # noqa: E402

A, B, C, NC = "11680", "41135", "28185", "00000"
D = "41131"  # 성남 수정구: 분당(B)과 같은 시


def od(ym, cells):
    return [{"ym": ym, "src": s, "dst": d, "persons": str(h), "persons_all": str(a)} for (s, d), (h, a) in cells.items()]


def kosis_margins(ym, truth):
    """실제 KOSIS처럼 일반구는 시로 묶어 총전입·총전출·시군구 내를 센다."""
    g, mg = lambda c: kosis.SI_OF_GU.get(c, c), {}
    for (s, d), v in truth.items():
        if d != NC:
            mg.setdefault(g(d), [0, 0, 0])[0] += v
        if s != NC:
            mg.setdefault(g(s), [0, 0, 0])[1] += v
        if g(s) == g(d):
            mg[g(s)][2] += v
    return [{"ym": ym, "code": c, "in_total": str(round(v[0])), "out_total": str(round(v[1])), "intra": str(round(v[2]))}
            for c, v in mg.items()]


class EstimateTest(unittest.TestCase):
    def setUp(self):
        base = {(A, B): (30, 100), (B, A): (10, 50), (A, C): (5, 20), (C, B): (8, 40), (NC, A): (6, 30), (B, NC): (4, 20),
                (A, A): (90, 200), (B, B): (60, 150), (C, C): (20, 60)}
        self.od = od("2025-11", base) + od("2025-12", base)
        # 2026-01 '실제' 행렬: 강남→분당이 늘고 전체가 조금 늘었다. 총계는 이 행렬에서 계산해 서로 모순이 없다.
        truth = {k: a * 1.1 for k, (h, a) in base.items()}
        truth[(A, B)] *= 1.5; truth[(A, A)] = 220; truth[(B, B)] = 160; truth[(C, C)] = 65
        self.truth = truth
        self.margins = kosis_margins("2026-01", truth) + [{"ym": "2025-12", "code": A, "in_total": "1", "out_total": "1", "intra": "1"}]

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

    def test_general_gu_constrained_as_city_group(self):
        """KOSIS에 없는 일반구(분당·수정)는 성남시(41130) 총계로 묶어 맞춘다. 구 안 이동도 빠뜨리지 않는다."""
        base = {(A, B): (30, 100), (A, D): (10, 40), (B, A): (10, 50), (D, A): (5, 30), (B, D): (6, 25), (D, B): (4, 20),
                (NC, B): (3, 15), (A, A): (90, 200), (B, B): (60, 150), (D, D): (30, 70)}
        truth = {k: a * 1.2 for k, (h, a) in base.items()}
        truth[(A, B)] *= 1.5; truth[(B, D)] = 40
        rows, _, _ = eo.estimate(od("2025-12", base), kosis_margins("2026-01", truth))
        m = {(r[1], r[2]): r[4] for r in rows}
        sn = {B, D}
        out_sn = sum(v for (s, d), v in m.items() if s in sn and d not in sn)
        in_sn = sum(v for (s, d), v in m.items() if d in sn and s not in sn)
        intra_sn = sum(v for (s, d), v in m.items() if s in sn and d in sn)
        self.assertAlmostEqual(out_sn, sum(v for (s, d), v in truth.items() if s in sn and d not in sn), delta=2)
        self.assertAlmostEqual(in_sn, sum(v for (s, d), v in truth.items() if d in sn and s not in sn), delta=2)
        self.assertAlmostEqual(intra_sn, sum(v for (s, d), v in truth.items() if s in sn and d in sn), delta=3)
        self.assertTrue(all(m.get(k, 0) > 0 for k in ((B, B), (D, D), (B, D), (D, B))))   # 구 대각선·구 사이 칸
        self.assertNotIn(("41130", "41130"), m)                                           # 시 코드는 화면 노드가 아니다

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
                     ("T10", "41130", "15"), ("T10", "41110", "7"), ("T10", "26110", "999"))]
            return json.dumps(rows).encode()
        rows, missing, dropped = kosis.margins("K", "2026-01", "2026-08", ["11680", "41135", "41131", "41111"], getter=getter, chunk=6)
        self.assertEqual(len(urls), 2)                                        # 8개월 → 6개월씩 2번 (같은 달이 겹쳐 와도 두 배 안 됨)
        self.assertIn(["2026-01", "11680", 250, 400, 220], rows)
        self.assertIn(["2026-01", "41130", 15, 0, 0], rows)                   # 분당·수정 → 성남시 한 줄(두 번 더하지 않음)
        self.assertIn(["2026-01", "41110", 7, 0, 0], rows)                    # 장안 → 수원시
        self.assertEqual(len(rows), 3)                                        # 부산(26110)은 빠진다
        self.assertEqual((missing, dropped), ([], []))
        _, missing, _ = kosis.margins("K", "2026-01", "2026-01", ["41285"], getter=getter)
        self.assertEqual(missing, ["41285"])                                  # 고양시(41280)가 응답에 없으면 우리 코드로 알린다

    def test_reform_new_gu_restored_to_old_groups(self):
        """2026-07 인천 개편: 옛 코드는 0으로, 새 구는 값으로 온다. 새 구를 옛 체계 묶음으로 되돌린다.
        묶음 안에서 새 구끼리 오간 이동 X = Σintra × x_ratio 를 전입·전출에서 빼고 시군구 내에 더한다."""
        def getter(url):
            rows = [{"ITM_ID": i, "C1": c, "PRD_DE": p, "DT": dt} for p, c, i, dt in
                    (("202606", "28260", "T10", "8716"), ("202606", "28260", "T20", "6520"), ("202606", "28260", "T30", "3044"),
                     ("202607", "28110", "T10", "0"), ("202607", "28110", "T20", "0"), ("202607", "28110", "T30", "0"),
                     ("202607", "28140", "T10", "0"), ("202607", "28140", "T20", "0"), ("202607", "28140", "T30", "0"),
                     ("202607", "28260", "T10", "0"), ("202607", "28260", "T20", "0"), ("202607", "28260", "T30", "0"),
                     ("202607", "28125", "T10", "862"), ("202607", "28125", "T20", "852"), ("202607", "28125", "T30", "191"),
                     ("202607", "28155", "T10", "1816"), ("202607", "28155", "T20", "1411"), ("202607", "28155", "T30", "470"),
                     ("202607", "28275", "T10", "3942"), ("202607", "28275", "T20", "3711"), ("202607", "28275", "T30", "1169"),
                     ("202607", "28290", "T10", "5186"), ("202607", "28290", "T20", "2665"), ("202607", "28290", "T30", "1145"))]
            return json.dumps(rows).encode()
        rows, missing, dropped = kosis.margins("K", "2026-06", "2026-07", ["28110", "28140", "28260"], getter=getter)
        got = {(r[0], r[1]): r[2:] for r in rows}
        self.assertEqual(dropped, [])
        self.assertEqual(got[("2026-06", "28260")], [8716, 6520, 3044])           # 개편 전 달은 그대로
        x1 = round((191 + 470) * kosis.REFORM["28110"][1])
        self.assertEqual(got[("2026-07", "28110")], [862 + 1816 - x1, 852 + 1411 - x1, 191 + 470 + x1])
        x2 = round((1169 + 1145) * kosis.REFORM["28260"][1])
        self.assertEqual(got[("2026-07", "28260")], [3942 + 5186 - x2, 3711 + 2665 - x2, 1169 + 1145 + x2])
        self.assertNotIn(("2026-07", "28140"), got)                               # 동구는 중구 묶음에 들어갔다
        self.assertFalse({c for _, c in got} & {"28125", "28155", "28275", "28290"})  # 새 구 코드는 남지 않는다

    def test_abolished_code_without_successor_drops_month(self):
        """복원할 새 구가 응답에 없는데 옛 코드가 0이면 그 달을 버린다(0을 쓰면 IPF 가 0으로 맞춘다)."""
        def getter(url):
            rows = [{"ITM_ID": i, "C1": "28260", "PRD_DE": p, "DT": dt} for p, i, dt in
                    (("202606", "T10", "8716"), ("202606", "T20", "6520"), ("202606", "T30", "3044"),
                     ("202607", "T10", "0"), ("202607", "T20", "0"), ("202607", "T30", "0"))]
            return json.dumps(rows).encode()
        rows, _, dropped = kosis.margins("K", "2026-06", "2026-07", ["28260"], getter=getter)
        self.assertEqual(dropped, ["2026-07"])
        self.assertEqual(rows, [["2026-06", "28260", 8716, 6520, 3044]])

class ReformGroupTest(unittest.TestCase):
    """2026-07 인천 개편 이후 달에는 중구(28110)·동구(28140)를 한 묶음으로 제약한다(KOSIS 가 제물포구로 합쳐 준다)."""
    JUNG, DONG, OUT = "28110", "28140", "11680"

    def test_jung_dong_constrained_together_only_after_reform(self):
        J, Dg, O = self.JUNG, self.DONG, self.OUT
        base = {(J, J): (50, 50), (Dg, Dg): (10, 10), (J, Dg): (5, 5), (Dg, J): (5, 5),
                (O, J): (20, 20), (O, Dg): (10, 10), (J, O): (15, 15), (Dg, O): (15, 15)}
        # 개편 전 달: 따로 온다 / 개편 후 달: 중구 묶음 하나로 온다(동구는 없다)
        margins = [
            {"ym": "2026-06", "code": J, "in_total": "80", "out_total": "75", "intra": "50"},
            {"ym": "2026-06", "code": Dg, "in_total": "25", "out_total": "30", "intra": "10"},
            {"ym": "2026-07", "code": J, "in_total": "100", "out_total": "100", "intra": "70"},
        ]
        rows, _, months = eo.estimate(od("2025-12", base), margins)
        self.assertEqual(months, ["2026-06", "2026-07"])
        got = {(r[0], r[1], r[2]): r[4] for r in rows}
        # 개편 전: 시군구 내는 각각 KOSIS 값
        self.assertEqual(got[("2026-06", J, J)], 50)
        self.assertEqual(got[("2026-06", Dg, Dg)], 10)
        # 개편 후: 묶음 안 칸(중구 내·동구 내·중구↔동구) 합이 묶음 시군구 내(70)이고, MDIS 비율(50:10:5:5)로 나뉜다
        inner = sum(v for (ym, a, b), v in got.items() if ym == "2026-07" and a in (J, Dg) and b in (J, Dg))
        self.assertAlmostEqual(inner, 70, delta=1)
        self.assertAlmostEqual(got[("2026-07", J, J)], 70 * 50 / 70, delta=1)
        self.assertIn(("2026-07", J, Dg), got)       # 중구↔동구는 묶음 안 칸으로 남는다
        # 묶음에서 밖으로 나간 양 = out_total - intra = 30
        out = sum(v for (ym, a, b), v in got.items() if ym == "2026-07" and a in (J, Dg) and b not in (J, Dg))
        self.assertAlmostEqual(out, 30, delta=1)


if __name__ == "__main__":
    unittest.main()
