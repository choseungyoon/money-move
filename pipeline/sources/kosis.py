"""KOSIS(국가통계포털) 공유서비스 API로 '최신 공개 월'을 확인한다.

시군구 간 이동 OD는 MDIS 마이크로데이터(로그인·이용 신청 필요)라 자동으로 받을 수 없다.
대신 같은 조사의 공개 통계표가 몇 월까지 올라왔는지를 KOSIS에서 확인해, 새 달이 나오면 알린다.

  통계자료 API: /openapi/Param/statisticsParameterData.do?method=getList
  newEstPrdCnt=1 로 '최근 수록 시점 1개'만 요청하고 응답의 PRD_DE(YYYYMM)를 읽는다.

TABLE 값(통계표 ID, 항목·분류 코드)은 KOSIS 통계표 화면의 'OpenAPI' 버튼에서 확인해 맞춘다.
실제 키로 확인하지 못한 값이다. 키는 KOSIS 공유서비스에서 무료로 발급(KOSIS_KEY 환경변수).
"""
import json, time, urllib.parse, urllib.request

BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
TABLE = {"orgId": "101", "tblId": "DT_1B26001_A01", "itmId": "ALL", "objL1": "ALL"}  # 시군구별 이동자수 (확인 필요)


def http_get(url, retries=4):
    """KOSIS 도 공공 API라 간헐적으로 연결이 끊긴다(ConnectionResetError Errno 54를 겪었다).
    margins 는 6개월씩 여러 번 호출하는데, 한 번 실패하면 그때까지 받은 것이 전부 날아간다. molit 과 같이 다시 시도한다."""
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception:
            if k == retries - 1:
                raise
            time.sleep(2 ** (k + 1))


def latest_month(api_key, table=TABLE, getter=http_get):
    """최신 공개 월 'YYYY-MM'. 오류 응답이면 예외."""
    q = {"method": "getList", "apiKey": api_key, "format": "json", "jsonVD": "Y",
         "prdSe": "M", "newEstPrdCnt": "1", **table}
    body = json.loads(getter(f"{BASE}?{urllib.parse.urlencode(q)}"))
    if isinstance(body, dict):  # 오류는 {"err": "..", "errMsg": ".."} 형태
        raise RuntimeError(f"KOSIS 오류 {body.get('err')}: {body.get('errMsg')}")
    periods = [str(r.get("PRD_DE", "")) for r in body if r.get("PRD_DE")]
    if not periods:
        raise RuntimeError("KOSIS 응답에 PRD_DE(수록 시점)가 없습니다. TABLE 설정을 확인하세요.")
    p = max(periods)
    return f"{p[:4]}-{p[4:6]}"


# 시군구별 이동자수(DT_1B26001_A01) 항목. GitHub Actions에서 실제 응답으로 확인함(2026-10).
ITEMS = {"T10": "in_total", "T20": "out_total", "T30": "intra"}  # 총전입, 총전출, 시군구 내 이동
# 이 표는 경기 일반구 없이 시 단위로만 준다(kosis-probe 2026-10: 41110 수원시는 있고 41111 장안구는 없음).
# 시 총계의 시군구 내(T30)에는 그 시의 구 사이 이동도 들어 있다(MDIS를 구→시로 묶으면 정수까지 일치).
# 그래서 일반구 총계는 구별로 나누지 않고 시 묶음으로 쓴다(estimate_od.py). 부천(41190)은 KOSIS에도 시 하나로 나온다.
SI_OF_GU = {c: c[:4] + "0" for c in ("41111", "41113", "41115", "41117", "41131", "41133", "41135", "41171", "41173",
                                     "41271", "41273", "41281", "41285", "41287", "41461", "41463", "41465",
                                     "41591", "41593", "41595", "41597")}   # 화성 구(2026-02 신설)도 시(41590)로만 나온다
# 인천 개편(2026-07-01): 28110 중구·28140 동구·28260 서구는 이후 달에 0으로 나오고
# 28125 제물포구·28155 영종구·28275 서해구·28290 검단구가 새로 나온다(kosis-probe 2026-10 확인).
# 새 구는 기존 지역으로 깔끔하게 합칠 수 없어 별칭을 두지 않는다.
#   - 제물포구 = 옛 중구 내륙 + 옛 동구. 이 표에는 동 단위가 없어 나눌 수 없다.
#   - 서해구 + 검단구 = 옛 서구지만, 둘 사이 이동이 전입·전출로 잡혀 더하면 이중 계산되고 시군구 내 이동은 빠진다
#     (2026-06 서구 시군구내 3,044 → 2026-07 서해+검단 2,314).
# 대신 총전입·총전출이 모두 0인 지역(폐지 코드)이 나오면 에러를 낸다. 0을 그대로 쓰면 IPF가 그 지역 이동을 0으로 맞춘다.
#
# 2026-07 이후를 쓰려면 (아직 못 한다)
#   SI_OF_GU 와 같은 묶음 제약으로 되돌리는 것이 맞다. 묶음은 두 개다.
#     28260 서구          ← 서해구(28275) + 검단구(28290)
#     28110 중구 + 28140 동구 ← 제물포구(28125) + 영종구(28155)   (제물포구가 두 구에 걸쳐 하나로 묶인다)
#   새 구 n개를 묶음 M 으로 합칠 때, M 안에서 새 구끼리 오간 이동 X 를 빼고 더해야 한다.
#     in_total(M)  = Σ in_total  - X      out_total(M) = Σ out_total - X
#     intra(M)     = Σ intra     + X
#   KOSIS 는 X 를 주지 않는다(시군구 간 OD 가 없는 표다). 개편 직전·직후 실측으로 크기만 가늠하면
#     서구:      intra 2026-06 3,044 vs 2026-07 서해 1,169 + 검단 1,145 = 2,314  → X ≈ 730
#     중구+동구: intra 2026-06 684 + 78 = 762 vs 2026-07 제물포 191 + 영종 470 = 661 → X ≈ 101
#   X 를 0 으로 두면 서구의 묶음 간 이동이 22% 과대, 시군구 내 이동이 24% 과소가 된다. 그래서 추정이 필요하다.
#   제대로 추정하려면 MDIS 원자료(읍면동 코드가 있다)에서 옛 구 내부 이동을 새 구로 나눠 비율을 구해야 하고,
#   그러려면 새 구별 법정동 '코드' 목록이 필요하다. molit.py 의 _JUNGGU/_DONGGU 는 제물포구 분할용 '이름'
#   목록이어서 MDIS(코드)에는 쓸 수 없고, 서해·검단·영종 목록도 아직 없다.
#   X 를 MDIS 2023~25 행정동 쌍 집계로 추정해 복원한다(REFORM). 복원하지 못한 달은 통째로 버린다(dropped).
#   버린 달은 매매·전월세에 영향이 없고(국토부 자료) 인구이동만 화면에 '직전 달 대체'로 표시된다.
# 2026-07 인천 개편 복원 ---------------------------------------------------------------------
# KOSIS 는 이 달부터 새 구만 주고 옛 코드는 0으로 준다. 새 구를 우리 옛 체계 묶음으로 되돌린다.
#   묶음 대표 ← [새 구들], x_ratio
#   x_ratio = (묶음 안에서 새 구끼리 오간 이동) / (새 구 각각의 내부 이동 합)
#   in_total(묶음) = Σin  - X,  out_total(묶음) = Σout - X,  intra(묶음) = Σintra + X,  X = Σintra × x_ratio
# x_ratio 는 MDIS 2023~25(36개월) 행정동 쌍 집계로 구했다. 행정동 → 새 구 대응은 행정안전부
# '인천 행정구역 변경 상세내역'(2026-07-01)의 행정동 코드로 확인했다.
#   옛 중구(28110) → 제물포구: 53000 신포·52000 연안·54000 신흥·56000 도원·57000 율목·58500 동인천·61500 개항
#                  → 영종구  : 나머지 5종(62800·62000·62200·62300·63000). MDIS 실제 코드와 합계가 맞는다.
#   옛 동구(28140) → 제물포구: 전부(11종)
#   옛 서구(28260) → 검단구  : 68000 검단·69000 불로대곡·70000 원당·71000 당하·72000 오류왕길·73000 마전
#                             ·74000 아라(2025-10 에 아라1 75000·아라2 76000 으로 나뉨, 월별 코드 교체로 확인)·75000·76000
#                  → 서해구  : 51500 검암경서·53000 연희·53600/53700/53900 청라1~3·54200/54300/54400 가정1~3
#                             ·57500 신현원창·55000/56000/56100 석남1~3·58000/59000/60000/61000 가좌1~4
#     옛 서구 25종이 두 표로 빠짐없이 갈린다.
# 검증: KOSIS 개편 직전·직후 한 달 역산(서구 X ≈ 730, 중구+동구 ≈ 101)과 비교하면 MDIS 월평균은
#   서구 598명, 중구+동구 34명으로 둘 다 작다. 한 달 역산은 월 변동이 섞이고, 검단 신도시 입주로 2026년의
#   서해↔검단 이동이 2023~25 평균보다 클 수 있다. 36개월 평균인 MDIS 쪽을 쓰고 이 차이를 한계로 둔다.
#   (74000 을 처음엔 이 역산에 맞춰 서해로 뒀다가 월별 코드 교체로 검단임을 확인했다. 한 달 역산으로 분류하지 말 것.)
#   행정동 경계와 법정동 경계가 어긋나 일부 법정동만 넘어간 곳(검암경서동의 시천동·오류동)은 표현하지 못한다.
REFORM_FROM = "2026-07"
REFORM = {"28110": (["28125", "28155"], 0.045),   # 중구+동구 묶음 ← 제물포구 + 영종구
          "28260": (["28275", "28290"], 0.286)}   # 서구 ← 서해구 + 검단구
REFORM_ABSORBED = {"28140": "28110"}              # 묶음 대표에 흡수되는 우리 코드 (estimate_od 가 달별 묶음으로 쓴다)
MARGIN_COLS = ["ym", "code", "in_total", "out_total", "intra"]


def _months(start, end):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y}-{m:02d}" <= end:
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def margins(api_key, start, end, codes, getter=http_get, chunk=6):
    """start~end('YYYY-MM') 시군구별 총전입·총전출·시군구내 이동. 한 번에 받을 수 있는 양에 한도가 있어 chunk개월씩.
    codes: 우리 80개 시군구 코드. 결과는 KOSIS 코드(일반구는 시 코드 SI_OF_GU)로 준다.
    반환: (rows, missing, dropped)
      missing  응답에 없는 우리 코드
      dropped  폐지된 코드가 0으로 온 달 중 복원하지 못한 달. 그 달은 rows 에서 통째로 뺀다
               (0을 그대로 쓰면 IPF 가 그 지역 이동을 0으로 맞춘다)."""
    wanted = {SI_OF_GU.get(c, c) for c in codes} | {n for ns, _ in REFORM.values() for n in ns}
    ms = _months(start, end)
    seen = {}  # (ym, 원래 코드, 항목) → 값. 겹친 응답이 와도 두 번 더하지 않도록 덮어쓴다.
    for k in range(0, len(ms), chunk):
        part = ms[k:k + chunk]
        q = {"method": "getList", "apiKey": api_key, "format": "json", "jsonVD": "Y", "prdSe": "M",
             "startPrdDe": part[0].replace("-", ""), "endPrdDe": part[-1].replace("-", ""),
             "orgId": TABLE["orgId"], "tblId": TABLE["tblId"], "itmId": "ALL", "objL1": "ALL"}
        body = json.loads(getter(f"{BASE}?{urllib.parse.urlencode(q)}"))
        if isinstance(body, dict):
            raise RuntimeError(f"KOSIS 오류 {body.get('err')}: {body.get('errMsg')}")
        for r in body:
            field, raw = ITEMS.get(r.get("ITM_ID")), str(r.get("C1"))
            if not field or raw not in wanted:
                continue
            p = str(r["PRD_DE"])
            seen[(f"{p[:4]}-{p[4:6]}", raw, field)] = int(float(r.get("DT") or 0))
    acc = {}
    for (ym, raw, field), v in seen.items():
        acc.setdefault((ym, raw), {"in_total": 0, "out_total": 0, "intra": 0})[field] = v
    for ym in sorted({k[0] for k in acc if k[0] >= REFORM_FROM}):
        for old_code, (news, ratio) in REFORM.items():
            vals = [acc.pop((ym, n)) for n in news if (ym, n) in acc]
            if not vals:
                continue
            tot = {f: sum(v[f] for v in vals) for f in ITEMS.values()}
            x = round(tot["intra"] * ratio)
            acc[(ym, old_code)] = {"in_total": tot["in_total"] - x, "out_total": tot["out_total"] - x,
                                   "intra": tot["intra"] + x}
            for absorbed, rep in REFORM_ABSORBED.items():
                if rep == old_code:
                    acc.pop((ym, absorbed), None)   # 묶음 대표에 들어갔다
    dead = sorted({k[0] for k, v in acc.items() if v["in_total"] == v["out_total"] == 0})
    if dead:
        acc = {k: v for k, v in acc.items() if k[0] not in dead}
    rows = [[ym, c, v["in_total"], v["out_total"], v["intra"]] for (ym, c), v in sorted(acc.items())]
    got = {c for _, c in acc}
    missing = sorted(c for c in codes if SI_OF_GU.get(c, c) not in got)
    return rows, missing, dead
