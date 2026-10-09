"""MDIS「국내인구이동통계 > 세대관련연간자료」로더 (mdis.mods.go.kr, 국가데이터처).

시군구→시군구 OD는 MDIS 마이크로데이터에만 있다. 연 단위로 공개되고 공개가 1년 이상 늦다.
연도별 CSV(항목명 포함)를 data/raw/mdis/ 에 둔다.

세대관련 자료는 한 행이 이사한 세대 1개다. '이동_총인구'(이사한 세대원 수)가 있으면 사람 수도 구한다.
  persons            주택 사유 사람 수 (흐름 화면의 인구 이동)
  persons_all        전체 사유 사람 수 (KOSIS 월별 총계로 2026년 이후를 추정할 때 기준 패턴)
  households(_all)   세대 수

열 이름은 연도마다 다르다(2021~22 '전입행정구역_시도코드', 2023~25 '전입행정기관코드_시도').
FIELDS의 후보 중 파일 머리글에 있는 것을 쓴다. 주택 사유 코드는 HOUSING_REASON을 코드집으로 확인.

행정구역 코드 체계 (가장 위험한 부분)
  통계청 체계와 행정안전부 체계는 시도부터 다르다: 인천 23/28, 경기 31/41.
  11110·11140·11170·11200·11230은 두 체계에 모두 있지만 가리키는 구가 다르다.
  연도마다 체계가 다를 수 있어 '파일마다' 판별하고, 통계청 코드면 data/sgg_codes.csv로 바꾼다.
  대응표에 없는 수도권 코드가 UNMAPPED_LIMIT를 넘으면 조용히 버리지 않고 에러를 낸다.

출력: data/interim/migration_od_month.csv  ym,src,dst,persons,persons_all,households,households_all
"""
import csv, glob, os
from collections import Counter, defaultdict

FIELDS = {
    "y": ["전입연도", "전입년", "신고년"],
    "m": ["전입월", "신고월"],
    "to_sd": ["전입행정기관코드_시도", "전입행정구역_시도코드", "전입행정_시도", "전입행정_시도코드"],
    "to_sgg": ["전입행정기관코드_시군구", "전입행정구역_시군구코드", "전입행정_시군구", "전입행정_시군구코드"],
    "fr_sd": ["전출행정기관코드_시도", "전출행정구역_시도코드", "전출행정_시도", "전출행정_시도코드"],
    "fr_sgg": ["전출행정기관코드_시군구", "전출행정구역_시군구코드", "전출행정_시군구", "전출행정_시군구코드"],
    "reason": ["전입사유코드", "전입사유"],
    "persons": ["이동_총인구", "이동_총인구수", "이동인구_계"],  # 없으면 세대 1행 = 1명으로 센다
}
REQUIRED = ("y", "m", "to_sd", "to_sgg", "fr_sd", "fr_sgg", "reason")
HOUSING_REASON = {"3"}  # 1직업 2가족 3주택 4교육 5주거환경 ... (연도별 코드집 확인 필요)
NONCAP = "00000"
UNMAPPED_LIMIT = 0.01  # 수도권 행 중 대응표에 없는 비율 상한
SAMPLE_ROWS = 20000    # 체계 판별에 쓰는 앞부분 행 수
ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}  # 부천 구 재설치(2024), 행정안전부 체계
CODES = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sgg_codes.csv")
SCHEMES = {"mois": {"11", "28", "41"}, "kostat": {"11", "23", "31"}}  # 체계별 수도권 시도 코드
OUT_COLS = ["ym", "src", "dst", "persons", "persons_all", "households", "households_all"]


def load_codes(path=CODES):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {"mois": {r["code"]: r["code"] for r in rows} | dict(ALIAS),
            "kostat": {r["kostat"]: r["code"] for r in rows}}


def encoding_of(path):
    """MDIS CSV는 CP949인 경우가 많지만 UTF-8일 수도 있다. 앞부분으로 판별."""
    with open(path, "rb") as f:
        head = f.read(1 << 16)
    try:
        head.decode("utf-8")
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp949"


def resolve(header):
    cols = {}
    for k, names in FIELDS.items():
        hit = next((n for n in names if n in header), None)
        if hit is None and k in REQUIRED:
            raise ValueError(f"MDIS 파일 머리글에서 '{k}' 열을 찾지 못했습니다(후보 {names}). 머리글: {header}")
        cols[k] = hit
    return cols


def _code5(sd, sgg):
    sgg = (sgg or "").strip()
    return sgg if len(sgg) == 5 else f"{sd.strip()}{sgg[-3:].zfill(3)}"


def rows_of(path, limit=None):
    with open(path, encoding=encoding_of(path), errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        cols = resolve(reader.fieldnames or [])
        for n, r in enumerate(reader):
            if limit and n >= limit:
                return
            yield cols, r


def detect_scheme(path, codes):
    """시도 코드 23/31이 있으면 통계청, 28/41이면 행정안전부. 서울만 있으면 대응표에 맞는 행이 많은 쪽."""
    sidos, hits = Counter(), Counter()
    for c, r in rows_of(path, SAMPLE_ROWS):
        for sd, sgg in ((r[c["fr_sd"]], r[c["fr_sgg"]]), (r[c["to_sd"]], r[c["to_sgg"]])):
            sd = sd.strip(); sidos[sd] += 1
            code = _code5(sd, sgg)
            for name in SCHEMES:
                hits[name] += code in codes[name]
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
    acc = defaultdict(lambda: [0, 0, 0, 0])  # persons_h, persons_all, households_h, households_all
    report = {}
    for path in paths:
        scheme = detect_scheme(path, codes)
        table, capital = codes[scheme], SCHEMES[scheme]
        cap_rows, unmapped = 0, Counter()

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

        for c, r in rows_of(path):
            a, b = to_code(r[c["fr_sd"]], r[c["fr_sgg"]]), to_code(r[c["to_sd"]], r[c["to_sgg"]])
            if a is None or b is None or a == b == NONCAP:
                continue
            people = int(float(r[c["persons"]] or 1)) if c["persons"] else 1
            housing = r[c["reason"]].strip() in HOUSING_REASON
            x = acc[(f"{r[c['y']].strip()}-{int(r[c['m']]):02d}", a, b)]
            x[1] += people; x[3] += 1
            if housing:
                x[0] += people; x[2] += 1
        if cap_rows and sum(unmapped.values()) / cap_rows > UNMAPPED_LIMIT:
            raise ValueError(f"{os.path.basename(path)}: {scheme} 체계로 읽었지만 수도권 코드 {sum(unmapped.values())}/{cap_rows}건이 "
                             f"대응표에 없습니다: {unmapped.most_common(5)}. data/sgg_codes.csv 또는 ALIAS를 확인하세요.")
        report[os.path.basename(path)] = {"scheme": scheme, "unmapped": dict(unmapped.most_common(3))}
    with open(dst, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(OUT_COLS)
        for (ym, a, b), v in sorted(acc.items()):
            w.writerow([ym, a, b, *v])
    return report
