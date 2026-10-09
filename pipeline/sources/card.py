"""시군구별 카드 지출 로더. 시도마다 공개 자료의 '기준'이 달라 basis 열로 구분한다.

  서울  resident        서울 열린데이터광장 OA-23094 (서울시·신한카드), 거주지 행정동 기준, 월
  경기  merchant        경기데이터드림「카드 소비 데이터」, 시군구 (가맹점 소재지 기준으로 추정, 명세 확인 필요)
  인천  local_currency  인천e음(지역사랑상품권) 군구별 결제금액, 월. 신용카드 전체가 아니라 일부다.

기준이 다른 숫자는 서로 비교하면 안 된다. 화면은 basis를 항상 함께 보여주고 순위는 같은 기준끼리만 매긴다.
행정동 코드는 행정안전부 10자리여야 한다(앞 5자리 = 시군구). 통계청 8자리 코드는 앞 5자리가
다른 체계라 그대로 자르면 엉뚱한 구로 들어가므로 에러를 낸다.

출력: data/interim/card_month.csv  ym,code,amount_eok,count,basis
"""
import csv, json, os
from pathlib import Path
from collections import defaultdict

SEOUL_COLS = {"ym": "기준연월", "adm": "행정동코드", "amount": "카드이용금액", "count": "카드이용건수"}
GG_COLS = {"ym": "기준연월", "sgg": "시군구코드", "amount": "매출금액", "count": "매출건수"}
IC_COLS = {"ym": "기준연월", "sgg": "군구", "amount": "결제금액"}
AMOUNT_TO_EOK = 1e-8  # 원 → 억


def _ym(s):
    s = str(s).replace("-", "").replace(".", "")
    return f"{s[:4]}-{s[4:6]}"


def _num(s):
    return float(str(s).replace(",", "") or 0)


def seoul(src):
    acc = defaultdict(lambda: [0.0, 0])
    with open(src, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            adm = r[SEOUL_COLS["adm"]].strip()
            if len(adm) != 10:
                raise ValueError(f"행정동 코드 '{adm}'는 행안부 10자리가 아닙니다. 통계청 코드라면 매핑표가 필요합니다.")
            a = acc[(_ym(r[SEOUL_COLS["ym"]]), adm[:5])]
            a[0] += _num(r[SEOUL_COLS["amount"]]) * AMOUNT_TO_EOK
            a[1] += int(_num(r.get(SEOUL_COLS["count"], 0)))
    return [[ym, c, round(v, 1), n, "resident"] for (ym, c), (v, n) in acc.items()]


def gyeonggi(src):
    acc = defaultdict(lambda: [0.0, 0])
    with open(src, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            a = acc[(_ym(r[GG_COLS["ym"]]), r[GG_COLS["sgg"]].strip()[:5])]
            a[0] += _num(r[GG_COLS["amount"]]) * AMOUNT_TO_EOK
            a[1] += int(_num(r.get(GG_COLS["count"], 0)))
    return [[ym, c, round(v, 1), n, "merchant"] for (ym, c), (v, n) in acc.items()]


def incheon(src, geo_path):
    regions = json.loads(Path(geo_path).read_text(encoding="utf-8"))["regions"]
    by_name = {r["name"]: r["code"] for r in regions if r["sido"] == "인천"}
    acc = defaultdict(float)
    with open(src, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = by_name.get(r[IC_COLS["sgg"]].strip())
            if code:
                acc[(_ym(r[IC_COLS["ym"]]), code)] += _num(r[IC_COLS["amount"]]) * AMOUNT_TO_EOK
    return [[ym, c, round(v, 1), 0, "local_currency"] for (ym, c), v in acc.items()]


def write(rows, dst):
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ym", "code", "amount_eok", "count", "basis"])
        w.writerows(sorted(rows))
