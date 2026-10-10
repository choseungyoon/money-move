"""KOSIS(국가통계포털) 공유서비스 API로 '최신 공개 월'을 확인한다.

시군구 간 이동 OD는 MDIS 마이크로데이터(로그인·이용 신청 필요)라 자동으로 받을 수 없다.
대신 같은 조사의 공개 통계표가 몇 월까지 올라왔는지를 KOSIS에서 확인해, 새 달이 나오면 알린다.

  통계자료 API: /openapi/Param/statisticsParameterData.do?method=getList
  newEstPrdCnt=1 로 '최근 수록 시점 1개'만 요청하고 응답의 PRD_DE(YYYYMM)를 읽는다.

TABLE 값(통계표 ID, 항목·분류 코드)은 KOSIS 통계표 화면의 'OpenAPI' 버튼에서 확인해 맞춘다.
실제 키로 확인하지 못한 값이다. 키는 KOSIS 공유서비스에서 무료로 발급(KOSIS_KEY 환경변수).
"""
import json, urllib.parse, urllib.request

BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
TABLE = {"orgId": "101", "tblId": "DT_1B26001_A01", "itmId": "ALL", "objL1": "ALL"}  # 시군구별 이동자수 (확인 필요)


def http_get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


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
                                     "41271", "41273", "41281", "41285", "41287", "41461", "41463", "41465")}
# 인천 개편(2026-07-01): 28110 중구·28140 동구·28260 서구는 이후 달에 0으로 나오고
# 28125 제물포구·28155 영종구·28275 서해구·28290 검단구가 새로 나온다(kosis-probe 2026-10 확인).
# 새 구는 기존 지역으로 깔끔하게 합칠 수 없어 별칭을 두지 않는다.
#   - 제물포구 = 옛 중구 내륙 + 옛 동구. 이 표에는 동 단위가 없어 나눌 수 없다.
#   - 서해구 + 검단구 = 옛 서구지만, 둘 사이 이동이 전입·전출로 잡혀 더하면 이중 계산되고 시군구 내 이동은 빠진다
#     (2026-06 서구 시군구내 3,044 → 2026-07 서해+검단 2,314).
# 대신 총전입·총전출이 모두 0인 지역(폐지 코드)이 나오면 에러를 낸다. 0을 그대로 쓰면 IPF가 그 지역 이동을 0으로 맞춘다.
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
    codes: 우리 77개 시군구 코드. 결과는 KOSIS 코드(일반구는 시 코드 SI_OF_GU)로 준다.
    응답에 없는 우리 코드는 missing 으로 돌려준다."""
    wanted = {SI_OF_GU.get(c, c) for c in codes}
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
    dead = sorted(k for k, v in acc.items() if v["in_total"] == v["out_total"] == 0)
    if dead:
        raise RuntimeError(f"KOSIS 총전입·총전출이 0인 시군구(폐지된 코드): {dead[:6]}{' 외' if len(dead) > 6 else ''}. "
                           "행정구역 개편으로 새 코드로 바뀐 지역입니다. kosis.py의 인천 개편 주석을 보고 처리 방법을 정하세요.")
    rows = [[ym, c, v["in_total"], v["out_total"], v["intra"]] for (ym, c), v in sorted(acc.items())]
    got = {c for _, c in acc}
    missing = sorted(c for c in codes if SI_OF_GU.get(c, c) not in got)
    return rows, missing
