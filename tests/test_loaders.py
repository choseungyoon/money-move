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
                w.writerow(["전입연도", "전입월", "전입행정기관코드_시도", "전입행정기관코드_시군구", "전입행정기관코드_읍면동",
                            "전출행정기관코드_시도", "전출행정기관코드_시군구", "전출행정기관코드_읍면동", "전입사유코드"])
                y, m, e = "2025", "3", ["00001", "00002"]
                w.writerow([y, m, "41", "135", e[0], "11", "680", e[1], "3"])   # 강남 → 분당, 주택
                w.writerow([y, m, "41", "135", e[0], "11", "680", e[1], "3"])
                w.writerow([y, m, "41", "135", e[0], "11", "680", e[1], "1"])   # 직업 사유: 제외
                w.writerow([y, m, "11", "680", e[0], "26", "350", e[1], "3"])   # 부산 → 강남: 비수도권 00000
                w.writerow([y, m, "41", "192", e[0], "11", "500", e[1], "3"])   # 부천 원미구 → 41190
                w.writerow([y, m, "26", "350", e[0], "48", "121", e[1], "3"])   # 지방 → 지방: 제외
            dst = os.path.join(d, "od.csv")
            mdis.load(os.path.join(d, "*.csv"), dst)
            with open(dst, encoding="utf-8") as f:
                rows = {(r["src"], r["dst"]): int(r["persons"]) for r in csv.DictReader(f)}
        self.assertEqual(rows, {("11680", "41135"): 2, ("00000", "11680"): 1, ("11500", "41190"): 1})


# 2021~22년 설명서 기준 머리글(행정구역 = 통계청 체계일 수 있음)
HEADER_2022 = ["전입연도", "전입월", "전입행정구역_시도코드", "전입행정구역_시군구코드", "전출행정구역_시도코드", "전출행정구역_시군구코드", "전입사유코드",
               "전입행정구역_읍면동코드", "전출행정구역_읍면동코드"]


class MdisSchemeTest(unittest.TestCase):
    """통계청 코드(인천 23, 경기 31)와 행정안전부 코드(28, 41)를 판별해 같은 결과로 만든다."""
    def write(self, d, rows):
        with open(os.path.join(d, "x.csv"), "w", encoding="cp949", newline="") as f:
            w = csv.writer(f); w.writerow(HEADER_2022); w.writerows([r + ["00001", "00002"] for r in rows])
        dst = os.path.join(d, "od.csv")
        info = mdis.load(os.path.join(d, "*.csv"), dst)["x.csv"]
        with open(dst, encoding="utf-8") as f:
            return info, {(r["src"], r["dst"]): int(r["persons"]) for r in csv.DictReader(f)}

    def test_kostat_codes_mapped_not_dropped_as_noncapital(self):
        with tempfile.TemporaryDirectory() as d:
            # 통계청: 강남 11230 → 분당 31023, 인천 중구 23010 → 수원 장안 31011
            info, rows = self.write(d, [["2026", "5", "31", "023", "11", "230", "3"],
                                        ["2026", "5", "31", "011", "23", "010", "3"]])
        self.assertEqual(info["scheme"], "kostat")
        self.assertEqual(rows, {("11680", "41135"): 1, ("28110", "41111"): 1})

    def test_ambiguous_seoul_only_codes(self):
        # 11230 은 통계청에선 강남구, 행정안전부에선 동대문구. 서울만 있으면 대응표에 맞는 행이 많은 쪽으로 판별
        with tempfile.TemporaryDirectory() as d:
            info, rows = self.write(d, [["2026", "5", "11", "230", "11", "010", "3"],   # 종로(11010) → 강남(11230)
                                        ["2026", "5", "11", "250", "11", "020", "3"]])  # 중구(11020) → 강동(11250)
        self.assertEqual(info["scheme"], "kostat")
        self.assertEqual(rows, {("11110", "11680"): 1, ("11140", "11740"): 1})

    def test_unknown_capital_codes_fail_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                # 인천 23010(통계청에만 있는 시도 23)으로 체계는 분명하고, 경기 31999는 대응표에 없다
                self.write(d, [["2026", "5", "31", "999", "11", "230", "3"]] * 5 + [["2026", "5", "23", "010", "11", "230", "3"]])

    def test_undecidable_scheme_fails(self):
        # 11230은 두 체계에 다 있다(강남/동대문). 31은 경기/울산. 근거가 없으면 추측하지 않는다
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "판별할 수 없"):
                self.write(d, [["2026", "5", "31", "999", "11", "230", "3"]] * 5)


class MdisFileTest(unittest.TestCase):
    def test_per_file_scheme_household_persons_and_encoding(self):
        """2022(통계청 체계, CP949)와 2025(행정안전부 체계, UTF-8, 이동_총인구 포함)를 한 번에 읽는다."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2022.csv"), "w", encoding="cp949", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2022)
                w.writerow(["2022", "3", "31", "023", "11", "230", "3", "00001", "00002"])  # 통계청: 강남 → 분당
            with open(os.path.join(d, "2025.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["전입행정기관코드_시도", "전입행정기관코드_시군구", "전입행정기관코드_읍면동", "전입연도", "전입월",
                            "전출행정기관코드_시도", "전출행정기관코드_시군구", "전출행정기관코드_읍면동", "전입사유코드", "이동_총인구"])
                w.writerow(["41", "135", "00001", "2025", "3", "11", "680", "00002", "3", "4"])  # 행정안전부: 강남 → 분당, 4인 세대
                w.writerow(["41", "135", "00001", "2025", "3", "11", "680", "00002", "1", "1"])  # 직업 사유
            dst = os.path.join(d, "od.csv")
            rep = mdis.load(os.path.join(d, "*.csv"), dst)
            with open(dst, encoding="utf-8") as f:
                rows = {r["ym"]: r for r in csv.DictReader(f)}
        self.assertEqual((rep["2022.csv"]["scheme"], rep["2025.csv"]["scheme"]), ("kostat", "mois"))
        self.assertEqual((rows["2022-03"]["src"], rows["2022-03"]["dst"]), ("11680", "41135"))
        r = rows["2025-03"]
        self.assertEqual([r[k] for k in ("persons", "persons_all", "households", "households_all")], ["4", "5", "1", "2"])

    def test_missing_required_column_is_explained(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.csv"), "w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(["전입연도", "전입월"])
            with self.assertRaises(ValueError):
                mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))


# 2023~25 세대관련연간자료 실제 머리글(업로드된 Format 샘플과 같은 순서)
HEADER_2025 = ["전입행정기관코드_시도", "전입행정기관코드_시군구", "전입행정기관코드_읍면동", "전입연도", "전입월", "전입일",
               "전출행정기관코드_시도", "전출행정기관코드_시군구", "전출행정기관코드_읍면동", "전입사유코드", "세대주관계코드",
               "세대주만연령", "세대주성별코드", "세대관련코드", "이동_총인구수", "이동_남자인구수", "이동_여자인구수"]


def hh(to_sgg, fr_sgg, reason="3", n=1, ym="2025-09", emd=None):
    """세대 1행. 시군구는 샘플처럼 5자리 행정안전부 코드. emd=(전입, 전출) 읍면동 코드."""
    y, m = ym.split("-")
    to_emd, fr_emd = emd or (to_sgg + "00000", fr_sgg + "00000")
    return [to_sgg[:2], to_sgg, to_emd, y, m, "15", fr_sgg[:2], fr_sgg, fr_emd, reason, "1", "040", "1", "2", str(n), "0", str(n)]


class MdisSampleFormatTest(unittest.TestCase):
    def od(self, d, dst="od.csv"):
        with open(os.path.join(d, dst), encoding="utf-8") as f:
            return {(r["ym"], r["src"], r["dst"]): (int(r["persons"]), int(r["persons_all"]), int(r["households"])) for r in csv.DictReader(f)}

    def test_header_five_digit_codes_padded_month_and_blank_row(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025.csv"), "w", encoding="cp949", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025)
                w.writerow(hh("41150", "41150", "3", 1))   # 의정부 시군구 내, 같은 동(읍면동까지 같다): 집계 제외
                w.writerow(hh("41150", "41150", "3", 2, emd=("41150" + "00000", "41150" + "00200")))  # 의정부 동 간 이동
                w.writerow(hh("41135", "11680", "3", 4))   # 강남 → 분당 4인
                w.writerow(hh("41135", "11680", "2", 2))   # 가족 사유
                w.writerow([""] * len(HEADER_2025))        # 엑셀 저장 파일 끝의 빈 행
            rep = mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))
            od = self.od(d)
        self.assertEqual(rep["2025.csv"]["rows"], 4)
        self.assertEqual(od, {("2025-09", "41150", "41150"): (2, 2, 1), ("2025-09", "11680", "41135"): (4, 6, 1)})

    def test_fixed_width_text_same_as_csv(self):
        rows = [hh("41135", "11680", "3", 4), hh("28110", "26350", "3", 2)]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025); w.writerows(rows)
            # 고정길이: 시군구 3자리, 읍면동 5자리 (레이아웃 xls 기준)
            fixed = lambda r: "".join([r[0], r[1][2:], r[2][5:], *r[3:9][:3], r[6], r[7][2:], r[8][5:], r[9], r[10], r[11], r[12], r[13],
                                       r[14].zfill(2), r[15].zfill(2), r[16].zfill(2)])
            with open(os.path.join(d, "b.txt"), "w", encoding="ascii") as f:
                f.write("\n".join(fixed(r) for r in rows) + "\n")
            mdis.load([os.path.join(d, "a.csv")], os.path.join(d, "od.csv")); a = self.od(d)
            mdis.load([os.path.join(d, "b.txt")], os.path.join(d, "od.csv")); b = self.od(d)
        self.assertEqual(a, b)
        self.assertEqual(a[("2025-09", "00000", "28110")], (2, 2, 1))

    def test_bucheon_cross_gu_kept_even_when_emd_code_repeats(self):
        """부천 41192/41194 는 ALIAS 로 둘 다 41190 이 된다. 구별로 읍면동 코드가 겹쳐도 같은 동 이사로 보면 안 된다."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025)
                w.writerow(hh("41192", "41194", "3", 3, emd=("00100", "00100")))  # 구가 다르다: 남는다
                w.writerow(hh("41192", "41192", "3", 9, emd=("00100", "00100")))  # 같은 구·같은 동: 빠진다
                w.writerow(hh("28110", "26350"))                                   # 체계 판별·추출 조건용
                w.writerow(hh("26350", "28110"))
            mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))
            od = self.od(d)
        self.assertEqual(od[("2025-09", "41190", "41190")], (3, 3, 1))

    def test_filtered_extract_fails_loudly(self):
        """전입·전출 시도 = 서울·경기 조건으로 받은 파일: 인천·비수도권이 없으니 조용히 쓰지 않고 에러."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025)
                w.writerows([hh("41135", "11680")] * mdis.CHECK_MIN_ROWS)
            with self.assertRaisesRegex(ValueError, "인천.*비수도권"):
                mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))

    def test_two_disjoint_extracts_pass_check_without_double_count(self):
        """두 묶음: A 전입 시도 = 서울·인천·경기(전출 조건 없음), B 전출 = 서울·인천·경기 & 전입 = 그 밖의 14개 시도."""
        k = mdis.CHECK_MIN_ROWS
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025_in.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025)
                w.writerows([hh("41135", "11680")] * k + [hh("28110", "26350")])
            with open(os.path.join(d, "2025_out.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025); w.writerow(hh("26350", "28110"))
            mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))
            od = self.od(d)
        self.assertEqual(od[("2025-09", "11680", "41135")][0], k)
        self.assertEqual((od[("2025-09", "00000", "28110")][0], od[("2025-09", "28110", "00000")][0]), (1, 1))


class MdisNationwideSchemeTest(unittest.TestCase):
    """전국 파일: 시도 31은 행정안전부에선 울산, 통계청에선 경기. 31만 보고 통계청으로 판별하면 서울 코드가 전부 안 맞는다."""
    def load(self, d, rows):
        with open(os.path.join(d, "2023.csv"), "w", encoding="cp949", newline="") as f:
            w = csv.writer(f); w.writerow(HEADER_2025); w.writerows(rows)
        rep = mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))["2023.csv"]
        with open(os.path.join(d, "od.csv"), encoding="utf-8") as f:
            return rep, {(r["src"], r["dst"]): int(r["households_all"]) for r in csv.DictReader(f)}

    def test_mois_with_ulsan_rows(self):
        rows = [hh("31110", "11620")] * 50 + [hh("11710", "11620")] * 30 + [hh("28177", "41135")] * 5 + [hh("46110", "11710")]
        with tempfile.TemporaryDirectory() as d:
            rep, od = self.load(d, rows)
        self.assertEqual(rep["scheme"], "mois")
        self.assertEqual(od, {("11620", "00000"): 50, ("11620", "11710"): 30, ("41135", "28177"): 5, ("11710", "00000"): 1})

    def test_kostat_with_ulsan_26_and_busan_21(self):
        # 통계청: 26 울산, 21 부산, 31 경기(31023 분당), 11230 강남
        rows = [hh("26310", "11230")] * 20 + [hh("31023", "11230")] * 10 + [hh("11230", "21010")] * 3
        with tempfile.TemporaryDirectory() as d:
            rep, od = self.load(d, rows)
        self.assertEqual(rep["scheme"], "kostat")
        self.assertEqual(od, {("11680", "00000"): 20, ("11680", "41135"): 10, ("00000", "11680"): 3})

    def test_mixed_schemes_fail(self):
        rows = [hh("41135", "11680")] * 50 + [hh("23010", "11680")] * 50
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "섞여"):
                self.load(d, rows)


class MdisPersonFileTest(unittest.TestCase):
    """같은 폴더에 인구관련연간자료(41항목)가 섞여 있어도 세대관련만 읽는다."""
    def person_row(self):
        r = hh("41135", "11680")[:10]  # 앞 10항목은 세대관련과 같다
        return r + ["1", "040", "1"] + [""] * 27 + ["000001"]

    def test_person_files_skipped_in_any_form(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025_세대.csv"), "w", encoding="cp949", newline="") as f:
                csv.writer(f).writerow(hh("41135", "11680", "3", 3))            # 머리글 없는 세대 CSV
            with open(os.path.join(d, "2025_인구_a.csv"), "w", encoding="cp949", newline="") as f:
                csv.writer(f).writerow(self.person_row())                       # 머리글 없는 인구 CSV
            with open(os.path.join(d, "2025_인구_b.csv"), "w", encoding="cp949", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025[:10] + ["전입자1_세대주관계코드"] + ["x"] * 30)
                w.writerow(self.person_row())                                   # 머리글 있는 인구 CSV
            with open(os.path.join(d, "2025_인구_c.txt"), "w", encoding="ascii") as f:
                f.write("4113500000202509151168000000" + "3" + "10401" + " " * 45 + "000001\n")  # 고정길이 85자
            rep = mdis.load(os.path.join(d, "*.*"), os.path.join(d, "od.csv"))
            with open(os.path.join(d, "od.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(sorted(k for k, v in rep.items() if "skipped" in v), ["2025_인구_a.csv", "2025_인구_b.csv", "2025_인구_c.txt"])
        self.assertEqual([(r["src"], r["dst"], r["persons"]) for r in rows], [("11680", "41135", "3")])

    def test_only_person_files_fail(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "2025_인구.csv"), "w", encoding="cp949", newline="") as f:
                csv.writer(f).writerow(self.person_row())
            with self.assertRaisesRegex(FileNotFoundError, "세대관련"):
                mdis.load(os.path.join(d, "*.csv"), os.path.join(d, "od.csv"))


class MdisAggregateTest(unittest.TestCase):
    def test_gzip_copy_is_byte_stable(self):
        import gzip
        import run
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "2025.csv")
            with open(src, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f); w.writerow(HEADER_2025); w.writerow(hh("41135", "11680"))
            agg, run.MDIS_AGG = run.MDIS_AGG, os.path.join(d, "mdis", "od.csv.gz")
            try:
                run.mdis_aggregate([src], d)
                with open(run.MDIS_AGG, "rb") as f:
                    first = f.read()
                run.mdis_aggregate([src], d)
                with open(run.MDIS_AGG, "rb") as f:
                    second = f.read()
                text = gzip.decompress(first).decode()
            finally:
                run.MDIS_AGG = agg
        self.assertEqual(first, second)
        self.assertIn("2025-09,11680,41135,1,1,1,1", text)


if __name__ == "__main__":
    unittest.main()
