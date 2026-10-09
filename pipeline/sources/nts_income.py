"""국세청 국세통계포털(TASIS)「시군구별 근로소득 연말정산 신고현황(주소지)」로더.

TASIS > 세목별 > 원천세 > 근로소득 연말정산 신고현황 > 시군구별(주소지) 통계표를 CSV로 받아
data/raw/nts_income.csv 로 둔다. 연 단위이고, 'N년 귀속' 자료가 보통 N+1년 12월에 공개된다.

열 이름은 COLS에서 맞춘다(통계표 개편 때 바뀔 수 있다). 총급여 단위는 UNIT_TO_EOK로 억 원 환산.
일반구(분당구 등)가 따로 없고 '성남시'만 있으면 같은 값을 소속 구에 복사하고 level=city로 표시한다.
분당과 중원의 소득이 같을 리 없으므로, 화면은 이 표시를 보고 '시 단위 값'임을 밝혀야 한다.

출력: data/interim/income_year.csv  year,code,earners,total_pay_eok,avg_pay_manwon,level
"""
import csv, json, os

COLS = {"year": "귀속연도", "sido": "시도", "sgg": "시군구", "earners": "인원", "total_pay": "총급여"}
UNIT_TO_EOK = 1 / 100  # 총급여가 백만원 단위라고 가정 (억 = 백만원 / 100)
SIDO_ALIAS = {"서울": "서울", "서울특별시": "서울", "인천": "인천", "인천광역시": "인천", "경기": "경기", "경기도": "경기"}
CITY_GU = ("수원시", "성남시", "고양시", "용인시", "안산시", "안양시", "부천시")


def _num(s):
    return float(str(s).replace(",", "") or 0)


def load(src, dst, geo_path):
    regions = json.load(open(geo_path, encoding="utf-8"))["regions"]
    by_name = {(r["sido"], r["name"].replace(" ", "")): r["code"] for r in regions}
    out = []
    with open(src, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sido = SIDO_ALIAS.get(row[COLS["sido"]].strip())
            if not sido:
                continue
            name = row[COLS["sgg"]].replace(" ", "")
            earners, total = _num(row[COLS["earners"]]), _num(row[COLS["total_pay"]]) * UNIT_TO_EOK
            if not earners:
                continue
            avg = round(total * 1e4 / earners)  # 억 → 만원 / 인
            code = by_name.get((sido, name))
            if code:
                out.append([row[COLS["year"]], code, int(earners), round(total, 1), avg, "sgg"])
            elif name in CITY_GU:  # 시 단위만 있으면 소속 일반구에 복사
                for (s, n), c in by_name.items():
                    if s == sido and n.startswith(name) and n != name:
                        out.append([row[COLS["year"]], c, int(earners), round(total, 1), avg, "city"])
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["year", "code", "earners", "total_pay_eok", "avg_pay_manwon", "level"])
        w.writerows(sorted(out))
    return len(out)
