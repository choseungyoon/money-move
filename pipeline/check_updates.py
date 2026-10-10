"""새로 공개된 자료가 있는지 확인하고, 필요한 알림 목록을 만든다 (check-updates 워크플로에서 매일 실행).

인구이동 자료는 두 갈래다.
  KOSIS 월별 시군구 총계 : 약 한 달 늦게 공개. 파이프라인을 돌리면 MDIS 이후 달 OD를 자동 추정한다.
  MDIS 세대관련연간자료  : 연 단위, 1년 넘게 늦게 공개. 로그인이 필요해 사람이 받아 넣어야 한다.

알림
  rebuild : KOSIS 최신 월 > 화면의 추정 포함 마지막 달 → 파이프라인 재실행 필요
  mdis    : KOSIS에 Y년 12월까지 나왔는데 MDIS 실제 OD가 Y년 12월까지 없음 → 연간 파일 반영 필요
  reb     : 실거래 최신 월 > 매입자거주지(R-ONE) 마지막 달 → R-ONE 파일을 사람이 받아 넣어야 함.
            그 사이 매매 흐름은 R-ONE 최신 달 비중으로 대체되고 화면에 표시된다(meta.share_src).
보유 자료: data/coverage.json (실데이터 빌드 때 생성·커밋). 출력: data/update_status.json
"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from sources import kosis  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
COVERAGE = os.path.join(ROOT, "data", "coverage.json")

REBUILD_TITLE = "데이터 갱신 필요: 인구이동 {} 공개"
MDIS_TITLE = "MDIS {}년 인구이동 연간자료 반영 필요"
REB_TITLE = "R-ONE 매입자거주지 {} 반영 필요"


def check(coverage, published):
    cov = coverage or {}
    est_until, actual = cov.get("od_estimated_until"), cov.get("od_latest")
    alerts = []
    if published and (est_until is None or published > est_until):
        alerts.append({"kind": "rebuild", "key": published, "title": REBUILD_TITLE.format(published),
                       "body": (f"KOSIS에 {published} 시군구별 인구이동 총계가 공개되었습니다(화면 자료: {est_until or '없음'}까지).\n\n"
                                "`KOSIS_KEY=... MOLIT_KEY=... python3 pipeline/run.py real` 을 실행해 커밋하면 "
                                "MDIS 이후 달 추정이 갱신되고 이 이슈는 자동으로 닫힙니다.")})
    if published:
        full_year = int(published[:4]) - (0 if published[5:7] == "12" else 1)
        if actual is None or actual < f"{full_year}-12":
            alerts.append({"kind": "mdis", "key": str(full_year), "title": MDIS_TITLE.format(full_year),
                           "body": (f"KOSIS에 {full_year}년 12월까지 공개되었습니다. 화면의 시군구 간 이동은 실제 자료가 {actual or '없음'}까지이고, "
                                    "그 이후는 KOSIS 총계로 추정한 값입니다.\n\n"
                                    f"1. MDIS(mdis.mods.go.kr) > 자료 이용 > 다운로드 서비스 > 인구 > 국내인구이동통계 > 세대관련연간자료에서 {full_year}년이 올라왔는지 확인합니다. "
                                    "아직 없으면 이 이슈를 열어 두세요.\n"
                                    "2. 항목: 전입행정기관코드_시도·시군구, 전입연도, 전입월, 전출행정기관코드_시도·시군구, 전입사유코드, 이동_총인구. CSV(항목명 포함).\n"
                                    "3. 받은 파일을 data/raw/mdis/ 에 넣고 run.py real 을 실행해 커밋하면 이 이슈는 자동으로 닫힙니다.")})
    trade, reb = cov.get("trade_latest"), cov.get("reb_latest")
    if trade and reb and trade > reb:
        alerts.append({"kind": "reb", "key": trade, "title": REB_TITLE.format(trade),
                       "body": (f"아파트 실거래는 {trade} 까지 들어왔는데 매입자거주지(R-ONE)는 {reb} 까지입니다.\n"
                                f"그 사이 {reb} 이후 달의 매매 자금 출발지는 {reb} 비중으로 대체해 그리고 화면에 '대체' 로 표시합니다.\n\n"
                                "R-ONE 자료는 로그인이 필요해 자동으로 받을 수 없습니다. 아래를 따라 주세요.\n\n"
                                "1. 한국부동산원 R-ONE(https://www.reb.or.kr/r-one) > 「(월) 매입자거주지별 아파트거래현황」\n"
                                "2. 기간을 2023-01 ~ 최신월, 지역은 전국(또는 서울·인천·경기)으로 받습니다. 받은 CSV를 그대로 쓰면 됩니다\n"
                                "   (CP949, 기간이 열로 펼쳐진 와이드 형식, 지역 코드 없음 — 로더가 그 형식을 읽습니다).\n"
                                "3. 파일을 data/raw/r-one/ 에 넣고 `python3 pipeline/run.py real` 을 실행합니다.\n"
                                "4. data/reb/buyer_origin_share.csv 와 data/coverage.json 을 커밋하면 이 이슈는 자동으로 닫힙니다.\n\n"
                                "참고: 이 통계는 소유권이전등기 신청 기준이라 국토부 실거래 신고와 모집단이 다릅니다. "
                                "건수는 쓰지 않고 비중만 쓰며, 비중도 최근 12개월 평균으로 씁니다.")})
    return {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kosis_ok": bool(published), "published": published,
            "resolved": {"rebuild": est_until, "mdis": actual, "reb": reb}, "alerts": alerts,
            "message": "; ".join(a["title"] for a in alerts) or ("새로 할 일 없음" if published else "KOSIS 최신 공개 월을 확인하지 못했습니다.")}


def main():
    try:
        coverage = json.loads(Path(COVERAGE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        coverage = None
    published, err = None, None
    key = os.environ.get("KOSIS_KEY")
    if key:
        try:
            published = kosis.latest_month(key)
        except Exception as e:  # 네트워크·키·설정 오류는 실패로 처리하지 않고 상태에 남긴다
            err = str(e)
    status = check(coverage, published)
    if err:
        status["error"] = err
    with open(os.path.join(ROOT, "data", "update_status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    print(status["message"] + (f" ({err})" if err else ""))


if __name__ == "__main__":
    main()
