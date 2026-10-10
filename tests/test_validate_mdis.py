"""MDIS 집계 ↔ KOSIS 총계 대조: 같은 모집단이면 통과, 지역이 빠진 추출은 실패."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import validate_mdis as v  # noqa: E402


def od(rows):
    return [{"ym": ym, "src": a, "dst": b, "persons_all": str(n)} for ym, a, b, n in rows]


TRUTH = od([("2025-01", "11680", "41135", 100), ("2025-01", "41135", "11680", 80), ("2025-01", "11680", "11680", 300),
            ("2025-01", "28110", "11680", 40), ("2025-01", "00000", "41135", 60), ("2025-01", "41135", "00000", 50),
            ("2025-01", "28110", "28110", 120), ("2025-01", "41135", "41135", 200)])


def kosis_from(rows):
    return [{"ym": ym, "code": c, **{f: str(x) for f, x in m.items()}} for (ym, c), m in v.mdis_margins(rows).items()]


class ValidateMdisTest(unittest.TestCase):
    def test_margins_include_intra_and_noncapital(self):
        m = v.mdis_margins(TRUTH)[("2025-01", "11680")]
        self.assertEqual(m, {"in_total": 80 + 300 + 40, "out_total": 100 + 300, "intra": 300})

    def test_same_population_passes(self):
        res = v.compare(TRUTH, kosis_from(TRUTH))
        self.assertTrue(res["ok"])
        self.assertEqual(res["years"]["2025"]["in_total"], 1.0)

    def test_extract_missing_incheon_fails(self):
        filtered = [r for r in TRUTH if "28110" not in (r["src"], r["dst"])]
        res = v.compare(filtered, kosis_from(TRUTH))
        self.assertFalse(res["ok"])
        self.assertEqual(res["cells"][0][2], "28110")  # 가장 크게 어긋난 칸이 인천

    def test_months_without_kosis_are_reported_not_compared(self):
        extra = TRUTH + od([("2026-01", "11680", "41135", 10)])
        res = v.compare(extra, kosis_from(TRUTH))
        self.assertTrue(res["ok"])
        self.assertEqual(res["mdis_only"], ["2026-01"])


if __name__ == "__main__":
    unittest.main()
