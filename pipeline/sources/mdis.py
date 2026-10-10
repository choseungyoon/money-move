"""MDIS「국내인구이동통계 > 세대관련연간자료」로더 (mdis.mods.go.kr, 국가데이터처).

시군구→시군구 OD는 MDIS 마이크로데이터에만 있다. 연 단위로 공개되고 공개가 1년 이상 늦다.
연도별 CSV(항목명 포함)를 data/raw/mdis/ 에 둔다.

집계 대상 (KOSIS 총계와 맞추는 조건)
  국내인구이동통계는 읍면동 경계를 넘는 이동만 센다. 전입신고 원자료에는 같은 동 안 이사도 들어 있어
  그대로 세면 시군구 내 이동(대각선)이 1.9배가 된다. 전입·전출의 시도·시군구·읍면동이 모두 같은 행은 뺀다.
  부천 41192/41194/41196 은 ALIAS 로 41190 이 되고 구마다 읍면동 코드가 겹치므로, 합치기 전 코드로 비교한다.

세대관련 자료는 한 행이 이사한 세대 1개다. '이동_총인구'(이사한 세대원 수)가 있으면 사람 수도 구한다.
  persons            주택 사유 사람 수 (흐름 화면의 인구 이동)
  persons_all        전체 사유 사람 수 (KOSIS 월별 총계로 2026년 이후를 추정할 때 기준 패턴)
  households(_all)   세대 수

열 이름은 연도마다 다르다(2021~22 '전입행정구역_시도코드', 2023~25 '전입행정기관코드_시도').
FIELDS의 후보 중 파일 머리글에 있는 것을 쓴다. 주택 사유 코드는 HOUSING_REASON을 코드집으로 확인.
머리글이 없는 파일(고정길이 텍스트, 항목명 없는 CSV)은 2023~25 레이아웃(LAYOUT)으로 읽는다.

추출 조건 점검
  MDIS에서 받을 때 시도 조건(예: 전입·전출 시도 = 서울특별시,경기도)을 걸면 인천과 비수도권 이동이 빠진다.
  그러면 시군구별 총전입·총전출이 KOSIS 총계보다 작아져 이후 달 추정(IPF)도 틀어진다.
  그래서 한 해의 자료에 인천 행이나 비수도권 출발·도착 행이 하나도 없으면 에러를 낸다.

행정구역 코드 체계 (가장 위험한 부분)
  통계청 체계와 행정안전부 체계는 시도부터 다르다: 인천 23/28, 경기 31/41.
  11110·11140·11170·11200·11230은 두 체계에 모두 있지만 가리키는 구가 다르다.
  연도마다 체계가 다를 수 있어 '파일마다' 판별하고, 통계청 코드면 data/sgg_codes.csv로 바꾼다.
  시도 코드 31은 통계청에선 경기, 행정안전부에선 울산이다. 전국 파일에서는 한 체계에만 있는 코드(ONLY)로만 판별한다.
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
    "to_emd": ["전입행정기관코드_읍면동", "전입행정구역_읍면동코드", "전입행정_읍면동", "전입행정_읍면동코드"],
    "fr_emd": ["전출행정기관코드_읍면동", "전출행정구역_읍면동코드", "전출행정_읍면동", "전출행정_읍면동코드"],
    "reason": ["전입사유코드", "전입사유"],
    "persons": ["이동_총인구", "이동_총인구수", "이동인구_계"],  # 없으면 세대 1행 = 1명으로 센다
}
REQUIRED = ("y", "m", "to_sd", "to_sgg", "to_emd", "fr_sd", "fr_sgg", "fr_emd", "reason")
HOUSING_REASON = {"3"}  # 1직업 2가족 3주택 4교육 5주거환경 6자연환경 9기타 (MDIS 코드표 확인, 2023 전국 주택 31%)
NONCAP = "00000"
UNMAPPED_LIMIT = 0.01  # 수도권 행 중 대응표에 없는 비율 상한
SAMPLE_ROWS = 20000    # 체계 판별에 쓰는 최소 행 수(판별 근거가 부족하면 더 읽는다)
DECISIVE = 1000        # 한 체계에만 있는 시도 코드가 이만큼 나오면 판별을 끝낸다
ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}  # 부천 구 재설치(2024), 행정안전부 체계
CODES = os.path.join(os.path.dirname(__file__), "..", "..", "data", "sgg_codes.csv")
SCHEMES = {"mois": {"11", "28", "41"}, "kostat": {"11", "23", "31"}}  # 체계별 수도권 시도 코드
INCHEON = {"mois": "28", "kostat": "23"}
# 판별 근거는 한 체계에만 있는 시도 코드뿐이다. 11·26·29·31·36은 두 체계에 다 있지만 뜻이 다르다
# (31 = 통계청 경기 / 행정안전부 울산, 26 = 부산/울산, 29 = 세종/광주, 36 = 전남/세종).
ONLY = {"kostat": {"21", "22", "23", "24", "25", "32", "33", "34", "35", "37", "38", "39"},
        "mois": {"27", "28", "30", "41", "42", "43", "44", "45", "46", "47", "48", "50", "51", "52"}}
CHECK_MIN_ROWS = 1000  # 이보다 적은 해는 추출 조건 점검을 하지 않는다(테스트·일부 파일)
# 세대관련연간자료 2023~25 레이아웃(Format_PROC…942747.xls): 항목명, 길이. 시군구는 3자리, 읍면동은 5자리.
LAYOUT = [("전입행정기관코드_시도", 2), ("전입행정기관코드_시군구", 3), ("전입행정기관코드_읍면동", 5),
          ("전입연도", 4), ("전입월", 2), ("전입일", 2),
          ("전출행정기관코드_시도", 2), ("전출행정기관코드_시군구", 3), ("전출행정기관코드_읍면동", 5),
          ("전입사유코드", 1), ("세대주관계코드", 1), ("세대주만연령", 3), ("세대주성별코드", 1), ("세대관련코드", 1),
          ("이동_총인구수", 2), ("이동_남자인구수", 2), ("이동_여자인구수", 2)]
WIDTH = sum(n for _, n in LAYOUT)  # 41
# 인구관련연간자료(Format_PROC…942750.xls): 한 행에 전입자 1~10의 나이·성별, 41항목, 고정길이 85자.
# 이 서비스는 세대관련 자료만 쓴다(이동_총인구수로 사람 수도 나온다). 같은 폴더에 있으면 건너뛴다.
PERSON_ITEMS, PERSON_WIDTH = 41, 85
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


def file_kind(path):
    """첫 줄로 자료 종류를 판별: 'household'(세대관련) 또는 'person'(인구관련)."""
    with open(path, encoding=encoding_of(path), errors="replace", newline="") as f:
        first = f.readline().lstrip("\ufeff").rstrip("\r\n")
    if first[:1].isdigit():  # 머리글 없음
        n = len(next(csv.reader([first]))) if "," in first else None
        return "person" if n == PERSON_ITEMS or (n is None and len(first) == PERSON_WIDTH) else "household"
    return "person" if "전입자1_" in first else "household"


def _headerless(f, path):
    """머리글 없는 파일: 쉼표가 있으면 항목명 없는 CSV, 없으면 고정길이 텍스트로 보고 LAYOUT 순서로 읽는다."""
    names = [n for n, _ in LAYOUT]
    name = os.path.basename(path)
    for n, line in enumerate(f, 1):
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        if "," in line:
            vals = next(csv.reader([line]))
            if len(vals) != len(names):
                raise ValueError(f"{name}: {n}행 항목 수 {len(vals)} ≠ {len(names)}. 세대관련연간자료(2023~) 배치와 다릅니다. "
                                 "CSV(항목명 포함)로 다시 받으세요.")
        elif len(line) == WIDTH:
            vals, p = [], 0
            for _, w in LAYOUT:
                vals.append(line[p:p + w]); p += w
        else:
            raise ValueError(f"{name}: {n}행 길이 {len(line)} ≠ {WIDTH}. 세대관련연간자료(2023~) 고정길이 배치와 다릅니다. "
                             "CSV(항목명 포함)로 다시 받으세요.")
        yield dict(zip(names, vals))


def rows_of(path, limit=None):
    with open(path, encoding=encoding_of(path), errors="replace", newline="") as f:
        first = f.readline().lstrip("\ufeff").strip()
        f.seek(0)
        if first[:1].isdigit():
            reader, cols = _headerless(f, path), resolve([n for n, _ in LAYOUT])
        else:
            reader = csv.DictReader(f)
            cols = resolve(reader.fieldnames or [])
        for n, r in enumerate(reader):
            if limit and n >= limit:
                return
            if not (r[cols["y"]] or "").strip() or not (r[cols["m"]] or "").strip():
                continue  # 빈 행(엑셀로 저장한 파일 끝 등)
            yield cols, r


def detect_scheme(path, codes):
    """한 체계에만 있는 시도 코드(ONLY)가 많은 쪽. 그런 코드가 없으면(서울만 등) 대응표에 맞는 행이 많은 쪽.
    반환: (체계, 근거 {체계: 행 수})"""
    marks, hits = Counter(), Counter()
    for n, (c, r) in enumerate(rows_of(path)):
        for sd, sgg in ((r[c["fr_sd"]], r[c["fr_sgg"]]), (r[c["to_sd"]], r[c["to_sgg"]])):
            sd = sd.strip()
            code = _code5(sd, sgg)
            for name in SCHEMES:
                marks[name] += sd in ONLY[name]
                hits[name] += code in codes[name]
        if n >= SAMPLE_ROWS and max(marks.values(), default=0) >= DECISIVE:
            break
    if max(marks.values(), default=0) > 0:
        major, minor = sorted(SCHEMES, key=lambda s: -marks[s])
        if marks[minor] > 0.01 * marks[major]:
            raise ValueError(f"{os.path.basename(path)}: 시도 코드에 통계청 체계({marks['kostat']}건)와 행정안전부 체계"
                             f"({marks['mois']}건)가 섞여 있습니다. 한 파일에는 한 체계만 있어야 합니다.")
        return major, dict(marks)
    if hits["mois"] == hits["kostat"]:
        raise ValueError(f"{os.path.basename(path)}: 코드 체계를 판별할 수 없습니다(양쪽 대응표에 맞는 행 {hits['mois']}건으로 같음). "
                         "한 체계에만 있는 시도 코드(예: 인천 23/28, 경기 31/41)가 있는 파일인지 확인하세요.")
    return max(SCHEMES, key=lambda s: hits[s]), {"hits": dict(hits)}


def load(src, dst, codes_path=CODES):
    """src: glob 문자열 또는 파일 경로 목록."""
    paths = sorted(glob.glob(src) if isinstance(src, str) else src)
    if not paths:
        raise FileNotFoundError(f"MDIS 파일이 없습니다: {src}")
    codes = load_codes(codes_path)
    acc = defaultdict(lambda: [0, 0, 0, 0])  # persons_h, persons_all, households_h, households_all
    years = defaultdict(Counter)  # 추출 조건 점검: 행 수, 인천, 비수도권에서 출발, 비수도권으로 도착
    report = {}
    skipped = [p for p in paths if file_kind(p) == "person"]
    for p in skipped:
        report[os.path.basename(p)] = {"skipped": "인구관련연간자료(쓰지 않음)"}
    paths = [p for p in paths if p not in skipped]
    if not paths:
        raise FileNotFoundError(f"MDIS 세대관련연간자료가 없습니다(인구관련연간자료만 {len(skipped)}개). "
                                "국내인구이동통계 > 세대관련연간자료를 받으세요.")
    for path in paths:
        scheme, evidence = detect_scheme(path, codes)
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

        nrows = 0
        for c, r in rows_of(path):
            nrows += 1
            fr_sd, to_sd = r[c["fr_sd"]].strip(), r[c["to_sd"]].strip()
            yc = years[r[c["y"]].strip()]
            yc["rows"] += 1
            yc["incheon"] += INCHEON[scheme] in (fr_sd, to_sd)
            yc["from_outside"] += fr_sd not in capital
            yc["to_outside"] += to_sd not in capital
            if (fr_sd, r[c["fr_sgg"]].strip(), r[c["fr_emd"]].strip()) == (to_sd, r[c["to_sgg"]].strip(), r[c["to_emd"]].strip()):
                continue  # 같은 읍면동 안 이사: 인구이동통계 집계 대상이 아니다. ALIAS 로 합치기 전 코드로 비교한다
            a, b = to_code(fr_sd, r[c["fr_sgg"]]), to_code(to_sd, r[c["to_sgg"]])
            if a is None or b is None or a == b == NONCAP:
                continue
            people = int(float(r[c["persons"]] or 1)) if c["persons"] else 1
            housing = r[c["reason"]].strip() in HOUSING_REASON
            x = acc[(f"{r[c['y']].strip()}-{int(r[c['m']]):02d}", a, b)]
            x[1] += people; x[3] += 1
            if housing:
                x[0] += people; x[2] += 1
        if cap_rows and sum(unmapped.values()) / cap_rows > UNMAPPED_LIMIT:
            raise ValueError(f"{os.path.basename(path)}: {scheme} 체계(근거 {evidence})로 읽었지만 수도권 코드 {sum(unmapped.values())}/{cap_rows}건이 "
                             f"대응표에 없습니다: {unmapped.most_common(5)}. data/sgg_codes.csv 또는 ALIAS를 확인하세요.")
        report[os.path.basename(path)] = {"scheme": scheme, "evidence": evidence, "rows": nrows, "unmapped": dict(unmapped.most_common(3))}
    for y, yc in sorted(years.items()):
        lack = [label for k, label in (("incheon", "인천"), ("from_outside", "비수도권→수도권"), ("to_outside", "수도권→비수도권"))
                if not yc[k]]
        if yc["rows"] >= CHECK_MIN_ROWS and lack:
            raise ValueError(f"{y}년 MDIS 자료 {yc['rows']:,}행에 {', '.join(lack)} 이동이 하나도 없습니다. "
                             "추출할 때 시도 조건(예: 전입·전출 시도 = 서울특별시,경기도)이 걸린 것으로 보입니다. "
                             "조건 없이 전국으로 다시 받거나 README의 '두 묶음으로 나눠 받기'를 따르세요.")
    with open(dst, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(OUT_COLS)
        for (ym, a, b), v in sorted(acc.items()):
            w.writerow([ym, a, b, *v])
    return report
