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
