"""새로 공개된 자료가 있는지 확인한다. GitHub Actions에서 매일 돌린다(.github/workflows/check-updates.yml).

비교 대상
  보유 자료: data/coverage.json — 실데이터 빌드(run.py real) 때 생성·커밋하는 작은 상태 파일.
            중간 데이터는 git에 없으므로 '화면이 몇 월 자료까지 가졌는지'는 이 파일로만 알 수 있다.
  공개 자료: KOSIS 최신 공개 월 (KOSIS_KEY 없으면 확인 생략)

출력: data/update_status.json, 그리고 GitHub Actions 출력(od_new, od_published, od_have)
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(__file__))
from sources import kosis  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
COVERAGE = os.path.join(ROOT, "data", "coverage.json")


def check(coverage, published):
    have = (coverage or {}).get("od_latest")
    status = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "od_have": have, "od_published": published,
              "od_new": bool(published and (have is None or published > have))}
    if not published:
        status["message"] = "KOSIS 최신 공개 월을 확인하지 못했습니다(KOSIS_KEY 없음 또는 오류)."
    elif status["od_new"]:
        status["message"] = (f"인구이동 {published} 자료가 공개되었습니다(보유: {have or '없음'}). "
                             "MDIS에서 국내인구이동 마이크로데이터를 받아 data/raw/mdis/ 에 넣고 "
                             "`python3 pipeline/run.py real`을 실행하세요. 화면의 대체 표시는 자동으로 사라집니다.")
    else:
        status["message"] = f"새 자료 없음 (보유 {have}, 공개 {published})."
    return status


def main():
    try:
        coverage = json.load(open(COVERAGE, encoding="utf-8"))
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
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"od_new={'true' if status['od_new'] else 'false'}\n")
            f.write(f"od_published={published or ''}\nod_have={status['od_have'] or ''}\n")
            f.write(f"message={status['message']}\n")


if __name__ == "__main__":
    main()
