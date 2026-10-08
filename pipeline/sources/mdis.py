"""통계청 MDIS「국내인구이동통계」마이크로데이터 로더.

KOSIS 공개표는 시도 간 OD까지만 제공한다. 시군구→시군구 OD는 MDIS(mdis.kostat.go.kr) 마이크로데이터
(무료, 회원가입 후 다운로드)에 있다. 연도별 CSV를 data/raw/mdis/ 에 두고 실행한다.

필요 열(헤더 이름은 COLS에서 매핑):
  신고년, 신고월, 전입행정_시도코드, 전입행정_시군구코드, 전출행정_시도코드, 전출행정_시군구코드, 전입사유코드
- 시도+시군구를 합쳐 5자리 코드로 만든다(예: 11 + 680 → 11680). 비수도권은 00000으로 묶는다.
- HOUSING_ONLY=True면 전입사유 '주택'만 쓴다. 직장·교육 이동은 부동산 자금 흐름과 상관이 약하다.
출력: data/interim/migration_od_month.csv  ym,src,dst,persons
"""
import csv, glob
from collections import Counter

COLS = {"y": "신고년", "m": "신고월", "to_sd": "전입행정_시도코드", "to_sgg": "전입행정_시군구코드",
        "fr_sd": "전출행정_시도코드", "fr_sgg": "전출행정_시군구코드", "reason": "전입사유코드"}
HOUSING_REASON = {"3"}  # 1직업 2가족 3주택 4교육 5주거환경 ... (연도별 코드표 확인 필요)
HOUSING_ONLY = True
CAPITAL = {"11", "28", "41"}
ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}


def _code(sd, sgg):
    if sd not in CAPITAL:
        return "00000"
    c = f"{sd}{sgg[-3:]}"
    return ALIAS.get(c, c)


def load(src_glob, dst):
    cnt = Counter()
    for path in sorted(glob.glob(src_glob)):
        with open(path, encoding="cp949", errors="replace") as f:
            for r in csv.DictReader(f):
                if HOUSING_ONLY and r[COLS["reason"]] not in HOUSING_REASON:
                    continue
                a, b = _code(r[COLS["fr_sd"]], r[COLS["fr_sgg"]]), _code(r[COLS["to_sd"]], r[COLS["to_sgg"]])
                if a == b == "00000":
                    continue
                cnt[(f"{r[COLS['y']]}-{int(r[COLS['m']]):02d}", a, b)] += 1
    with open(dst, "w", newline="") as g:
        w = csv.writer(g); w.writerow(["ym", "src", "dst", "persons"])
        for (ym, a, b), n in sorted(cnt.items()):
            w.writerow([ym, a, b, n])
