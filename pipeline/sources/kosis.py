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
CODE_ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}
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
    codes: 우리 77개 시군구 코드. 응답에 없는 코드는 결과에서 빠지고 missing 으로 돌려준다."""
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
            if not field or CODE_ALIAS.get(raw, raw) not in codes:
                continue
            p = str(r["PRD_DE"])
            seen[(f"{p[:4]}-{p[4:6]}", raw, field)] = int(float(r.get("DT") or 0))
    acc = {}
    for (ym, raw, field), v in seen.items():  # 부천 구 코드(2024 재설치)만 41190으로 합친다
        row = acc.setdefault((ym, CODE_ALIAS.get(raw, raw)), {"in_total": 0, "out_total": 0, "intra": 0})
        row[field] += v
    rows = [[ym, c, v["in_total"], v["out_total"], v["intra"]] for (ym, c), v in sorted(acc.items())]
    missing = sorted(set(codes) - {c for _, c in acc})
    return rows, missing
