"""MDIS「국내인구이동통계」마이크로데이터 로더 (mdis.mods.go.kr, 국가데이터처).

KOSIS 공개표는 시도 간 OD까지만 있다. 시군구→시군구 OD는 MDIS 마이크로데이터에만 있다.
받은 CSV를 data/raw/mdis/ 에 둔다. 열 이름은 COLS에서, 주택 사유 코드는 HOUSING_REASON에서
MDIS 코드집을 보고 맞춘다(실제 파일로 확인하지 못한 값이다).

행정구역 코드 체계 (가장 위험한 부분)
  통계청 체계와 행정안전부 체계는 시도부터 다르다: 인천 23/28, 경기 31/41.
  11110·11140·11170·11200·11230은 두 체계에 모두 있지만 가리키는 구가 다르다.
  → 파일 앞부분으로 체계를 판별하고(data/sgg_codes.csv 대응표), 통계청 코드면 행정안전부 코드로 바꾼다.
  → 대응표에 없는 수도권 코드가 UNMAPPED_LIMIT를 넘으면 조용히 버리지 않고 에러를 낸다.

출력: data/interim/migration_od_month.csv  ym,src,dst,persons
"""
import csv, glob, os
from collections import Counter

COLS = {"y": "신고년", "m": "신고월", "to_sd": "전입행정_시도코드", "to_sgg": "전입행정_시군구코드",
        "fr_sd": "전출행정_시도코드", "fr_sgg": "전출행정_시군구코드", "reason": "전입사유코드"}
HOUSING_REASON = {"3"}  # 1직업 2가족 3주택 4교육 5주거환경 ... (연도별 코드집 확인 필요)
HOUSING_ONLY = True
ENCODING = "cp949"
NONCAP = "00000"
UNMAPPED_LIMIT = 0.01  # 수도권 행 중 대응표에 없는 비율 상한
SAMPLE_ROWS = 20000    # 체계 판별에 쓰는 앞부분 행 수
ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}  # 부천 구 재설치(2024), 행정안전부 체계
CODES = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sgg_codes.csv")
SCHEMES = {"mois": {"11", "28", "41"}, "kostat": {"11", "23", "31"}}  # 체계별 수도권 시도 코드


def load_codes(path=CODES):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {"mois": {r["code"]: r["code"] for r in rows} | {a: c for a, c in ALIAS.items()},
            "kostat": {r["kostat"]: r["code"] for r in rows}}


def _code5(sd, sgg):
    sgg = sgg.strip()
    return sgg if len(sgg) == 5 else f"{sd.strip()}{sgg[-3:]}"


def _rows(paths, limit=None):
    n = 0
    for path in paths:
        with open(path, encoding=ENCODING, errors="replace") as f:
            for r in csv.DictReader(f):
                yield r
                n += 1
                if limit and n >= limit:
                    return


def detect_scheme(paths, codes):
    """시도 코드 23/31이 있으면 통계청, 28/41이면 행정안전부. 서울만 있으면 대응표에 맞는 행이 많은 쪽."""
    sidos, hits = Counter(), Counter()
    for r in _rows(paths, SAMPLE_ROWS):
        for sd, sgg in ((r[COLS["fr_sd"]], r[COLS["fr_sgg"]]), (r[COLS["to_sd"]], r[COLS["to_sgg"]])):
            sd = sd.strip(); sidos[sd] += 1
            c = _code5(sd, sgg)
            for name in SCHEMES:
                hits[name] += c in codes[name]
    if sidos["23"] or sidos["31"]:
        return "kostat"
    if sidos["28"] or sidos["41"]:
        return "mois"
    return max(SCHEMES, key=lambda s: hits[s])


def load(src_glob, dst, codes_path=CODES):
    paths = sorted(glob.glob(src_glob))
    if not paths:
        raise FileNotFoundError(f"MDIS 파일이 없습니다: {src_glob}")
    codes = load_codes(codes_path)
    scheme = detect_scheme(paths, codes)
    table, capital = codes[scheme], SCHEMES[scheme]
    cnt, cap_rows, unmapped = Counter(), 0, Counter()

    def to_code(sd, sgg):
        nonlocal cap_rows
        sd = sd.strip()
        if sd not in capital:
            return NONCAP
        cap_rows += 1
        c = _code5(sd, sgg)
        if c not in table:
            unmapped[c] += 1
            return None
        return table[c]

    for r in _rows(paths):
        if HOUSING_ONLY and r[COLS["reason"]].strip() not in HOUSING_REASON:
            continue
        a = to_code(r[COLS["fr_sd"]], r[COLS["fr_sgg"]])
        b = to_code(r[COLS["to_sd"]], r[COLS["to_sgg"]])
        if a is None or b is None or a == b == NONCAP:
            continue
        cnt[(f"{r[COLS['y']].strip()}-{int(r[COLS['m']]):02d}", a, b)] += 1
    if cap_rows and sum(unmapped.values()) / cap_rows > UNMAPPED_LIMIT:
        raise ValueError(f"{scheme} 체계로 읽었지만 수도권 코드 {sum(unmapped.values())}/{cap_rows}건이 대응표에 없습니다: "
                         f"{unmapped.most_common(5)}. data/sgg_codes.csv 또는 ALIAS를 확인하세요.")
    with open(dst, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(["ym", "src", "dst", "persons"])
        for (ym, a, b), n in sorted(cnt.items()):
            w.writerow([ym, a, b, n])
    return {"scheme": scheme, "rows": sum(cnt.values()), "unmapped": dict(unmapped.most_common(5))}
