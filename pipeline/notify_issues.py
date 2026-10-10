"""알림 이슈를 만들고, 해결되면 닫는다 (GitHub REST API, check-updates 워크플로에서 실행).

입력: data/update_status.json (check_updates.py: alerts, resolved, kosis_ok)
  - 필요한 알림이 열려 있지 않으면 만든다(같은 제목 중복 없음).
  - 열려 있는 알림이 더 이상 필요 없으면 닫는다: 해결됐으면 completed, 더 새 알림으로 바뀌었으면 not_planned.
  - KOSIS 확인에 실패한 날은 만들지도 닫지도 않는다(오류 하루로 알림이 사라지지 않게).
  - 예전 형식 '인구이동 YYYY-MM 자료 공개됨'은 MDIS가 월별이라는 틀린 전제였으므로 설명을 달고 닫는다.
판단(plan)은 순수 함수, GitHub 호출(apply)은 분리.
"""
import json, os, re, sys, urllib.request
from pathlib import Path

API = "https://api.github.com"
KINDS = {
    "rebuild": re.compile(r"^데이터 갱신 필요: 인구이동 (\d{4}-\d{2}) 공개$"),
    "mdis": re.compile(r"^MDIS (\d{4})년 인구이동 연간자료 반영 필요$"),
    "reb": re.compile(r"^R-ONE 매입자거주지 (\d{4}-\d{2}) 반영 필요$"),
}
LEGACY = re.compile(r"^인구이동 (\d{4}-\d{2}) 자료 공개됨$")
LEGACY_NOTE = ("이 안내는 틀린 전제로 만들어졌습니다. MDIS의 시군구 간 이동 자료는 월별이 아니라 연 단위(현재 2025년까지)라 "
               "월별 자료를 받을 수 없습니다. 월별은 KOSIS 총계로 자동 추정하고, 연간 자료 반영은 {mdis} 에서 안내합니다.")


def resolved(kind, key, res):
    have = (res or {}).get(kind)
    if not have:
        return False
    if kind == "mdis":
        return have >= f"{key}-12"
    return have >= key   # rebuild: 추정 포함 마지막 달, reb: 매입자거주지 마지막 달


def plan(open_issues, status):
    actions = []
    for it in open_issues:
        if LEGACY.match(it["title"]):
            actions.append(("close", it["number"], LEGACY_NOTE, "not_planned"))
    if not status.get("kosis_ok"):
        return actions
    want = {(a["kind"], a["key"]): a for a in status.get("alerts", [])}
    have = {}
    for it in open_issues:
        for kind, pat in KINDS.items():
            m = pat.match(it["title"])
            if m:
                have[(kind, m.group(1))] = it["number"]
    for k, a in want.items():
        if k not in have:
            actions.append(("create", a["kind"], a["title"], a["body"]))
    for (kind, key), num in sorted(have.items(), key=lambda x: x[1]):
        if (kind, key) in want:
            continue
        if resolved(kind, key, status.get("resolved")):
            actions.append(("close", num, "반영 완료: 화면 자료가 갱신되었습니다.", "completed"))
        else:
            actions.append(("close", num, "더 새 알림 {%s} 로 통합합니다." % kind, "not_planned"))
    return actions


def request(method, path, body=None, token=None):
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def apply(actions, repo, call, open_issues=()):
    refs = {"rebuild": "새 알림", "mdis": "새 알림"}
    for it in open_issues:  # 이미 열린 알림도 참조 대상으로
        for kind, pat in KINDS.items():
            if pat.match(it["title"]):
                refs[kind] = f"#{it['number']}"
    for a in actions:
        if a[0] == "create":
            issue = call("POST", f"/repos/{repo}/issues", {"title": a[2], "body": a[3]})
            refs[a[1]] = f"#{issue['number']}"
            print(f"이슈 생성 {refs[a[1]]}: {a[2]}")
    for a in actions:
        if a[0] == "close":
            _, num, comment, reason = a
            call("POST", f"/repos/{repo}/issues/{num}/comments", {"body": comment.format(**refs)})
            call("PATCH", f"/repos/{repo}/issues/{num}", {"state": "closed", "state_reason": reason})
            print(f"이슈 #{num} 닫음 ({reason})")
    if not actions:
        print("할 일 없음")


def main():
    root = os.path.join(os.path.dirname(__file__), "..")
    status = json.loads(Path(os.path.join(root, "data", "update_status.json")).read_text(encoding="utf-8"))
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        sys.exit("GITHUB_TOKEN, GITHUB_REPOSITORY 가 필요합니다(GitHub Actions에서 실행).")
    call = lambda m, p, b=None: request(m, p, b, token)
    issues = [i for i in call("GET", f"/repos/{repo}/issues?state=open&per_page=100") if "pull_request" not in i]
    apply(plan(issues, status), repo, call, issues)


if __name__ == "__main__":
    main()
