"""새 자료 알림 이슈를 만들고, 반영되면 닫는다 (GitHub REST API, check-updates 워크플로에서 실행).

입력: data/update_status.json (check_updates.py 결과: od_have, od_published)
규칙
  - 보유 자료가 알림 월 이상이면: '반영 완료' 댓글 후 완료로 닫는다.
  - 더 새 달이 공개됐는데 옛 달 알림이 열려 있으면: 새 알림을 만들고 옛 알림은 새 이슈로 통합하며 닫는다.
  - 같은 달 알림이 이미 열려 있으면 새로 만들지 않는다.
판단(plan)은 순수 함수로 두고, GitHub 호출(apply)은 분리해 테스트한다.
"""
import json, os, re, sys, urllib.request

TITLE = "인구이동 {} 자료 공개됨"
PATTERN = re.compile(r"^인구이동 (\d{4}-\d{2}) 자료 공개됨$")
API = "https://api.github.com"


def plan(open_issues, have, published, message=""):
    """open_issues: [{'number', 'title'}] → 할 일 목록 [('create'|'close', ...)]"""
    alerts = {}
    for it in open_issues:
        m = PATTERN.match(it["title"])
        if m:
            alerts[it["number"]] = m.group(1)
    actions = []
    want_new = bool(published and (have is None or published > have))
    if want_new and published not in alerts.values():
        actions.append(("create", TITLE.format(published), message))
    for num, month in sorted(alerts.items()):
        if have and month <= have:
            actions.append(("close", num, f"반영 완료: 화면 자료가 {have}까지 갱신되었습니다.", "completed"))
        elif want_new and month < published:
            actions.append(("close", num, f"더 새 자료({published})가 공개되어 {{new}} 로 통합합니다.", "not_planned"))
    return actions


def request(method, path, body=None, token=None):
    req = urllib.request.Request(f"{API}{path}", method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def apply(actions, repo, call):
    new_ref = "새 알림"
    for a in actions:
        if a[0] == "create":
            issue = call("POST", f"/repos/{repo}/issues", {"title": a[1], "body": a[2]})
            new_ref = f"#{issue['number']}"
            print(f"이슈 생성 {new_ref}: {a[1]}")
    for a in actions:
        if a[0] == "close":
            _, num, comment, reason = a
            call("POST", f"/repos/{repo}/issues/{num}/comments", {"body": comment.replace("{new}", new_ref)})
            call("PATCH", f"/repos/{repo}/issues/{num}", {"state": "closed", "state_reason": reason})
            print(f"이슈 #{num} 닫음 ({reason})")
    if not actions:
        print("할 일 없음")


def main():
    root = os.path.join(os.path.dirname(__file__), "..")
    status = json.load(open(os.path.join(root, "data", "update_status.json"), encoding="utf-8"))
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        sys.exit("GITHUB_TOKEN, GITHUB_REPOSITORY 가 필요합니다(GitHub Actions에서 실행).")
    call = lambda m, p, b=None: request(m, p, b, token)
    issues = [i for i in call("GET", f"/repos/{repo}/issues?state=open&per_page=100") if "pull_request" not in i]
    apply(plan(issues, status.get("od_have"), status.get("od_published"), status.get("message", "")), repo, call)


if __name__ == "__main__":
    main()
