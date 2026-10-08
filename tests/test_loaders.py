"""부동산원·MDIS 로더: 실제 배포 형식(CP949, 한글 헤더)으로 검증한다."""
import csv, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from sources import mdis, reb  # noqa: E402


class RebTest(unittest.TestCase):
    def test_counts_become_shares_and_period_normalized(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "in.csv"), os.path.join(d, "out.csv")
            with open(src, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["기간", "지역코드", "관할시군구내", "관할시도내", "관할시도외_서울", "관할시도외_기타"])
                w.writerow(["202503", "4113500000", 50, 20, 25, 5])
                w.writerow(["2025.04", "11680", 40, 40, 0, 20])
            reb.load(src, dst)
            with open(dst, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual((rows[0]["ym"], rows[0]["code"], rows[0]["seoul"]), ("2025-03", "41135", "0.25"))
        self.assertEqual(rows[1]["ym"], "2025-04")


class MdisTest(unittest.TestCase):
    def test_housing_only_noncapital_collapsed_and_bucheon_merged(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025.csv"), "w", encoding="cp949", newline="") as f:
                w = csv.writer(f)
                w.writerow(list(mdis.COLS.values()))
                y, m = "2025", "3"
                w.writerow([y, m, "41", "135", "11", "680", "3"])   # 강남 → 분당, 주택
                w.writerow([y, m, "41", "135", "11", "680", "3"])
                w.writerow([y, m, "41", "135", "11", "680", "1"])   # 직업 사유: 제외
                w.writerow([y, m, "11", "680", "26", "350", "3"])   # 부산 → 강남: 비수도권 00000
                w.writerow([y, m, "41", "192", "11", "500", "3"])   # 부천 원미구 → 41190
                w.writerow([y, m, "26", "350", "48", "121", "3"])   # 지방 → 지방: 제외
            dst = os.path.join(d, "od.csv")
            mdis.load(os.path.join(d, "*.csv"), dst)
            with open(dst, encoding="utf-8") as f:
                rows = {(r["src"], r["dst"]): int(r["persons"]) for r in csv.DictReader(f)}
        self.assertEqual(rows, {("11680", "41135"): 2, ("00000", "11680"): 1, ("11500", "41190"): 1})


if __name__ == "__main__":
    unittest.main()
