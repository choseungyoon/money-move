"""한국부동산원 R-ONE「매입자거주지별 아파트매매거래 현황」(월, 시군구) 로더.

R-ONE 통계표에서 시군구별·월별 CSV를 내려받아 data/raw/reb_buyer_residence.csv 로 저장한 뒤 사용한다.
(R-ONE Open API `SttsApiTblData.do`로도 받을 수 있으나 통계표 ID(STATBL_ID)가 개편 때 바뀌므로
 자동화 시 R-ONE 'Open API > 통계표 목록'에서 ID를 확인해 REB_STATBL_ID 환경변수로 넣는다.)

입력 CSV 열(헤더 이름은 COLS에서 매핑):
  기간(YYYY-MM 또는 YYYYMM), 지역코드(5자리), 관할시군구내, 관할시도내, 관할시도외_서울, 관할시도외_기타
출력: data/interim/buyer_origin_share.csv  ym,code,same_sgg,same_sido,seoul,other
"""
import csv

COLS = {"ym": "기간", "code": "지역코드", "same_sgg": "관할시군구내", "same_sido": "관할시도내",
        "seoul": "관할시도외_서울", "other": "관할시도외_기타"}


def load(src, dst):
    with open(src, encoding="utf-8-sig") as f, open(dst, "w", newline="") as g:
        w = csv.writer(g); w.writerow(["ym", "code", "same_sgg", "same_sido", "seoul", "other"])
        for row in csv.DictReader(f):
            ym = row[COLS["ym"]].replace(".", "-")
            ym = ym if "-" in ym else f"{ym[:4]}-{ym[4:6]}"
            vals = [float(row.get(COLS[k]) or 0) for k in ("same_sgg", "same_sido", "seoul", "other")]
            s = sum(vals) or 1
            w.writerow([ym, row[COLS["code"]][:5], *[round(v / s, 4) for v in vals]])
