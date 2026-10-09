#!/usr/bin/env bash
# 로컬 개발 환경 준비 (macOS/Linux). 여러 번 실행해도 안전하다.
#   bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}

# 1) Python 3.10 이상 (CI는 3.12). 파이프라인은 표준 라이브러리만 쓴다.
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Python 3.10 이상이 필요합니다. 현재: $("$PY" --version 2>&1)"
  echo "  macOS: brew install python@3.12  →  PYTHON=python3.12 bash scripts/setup.sh"
  exit 1
fi
echo "✓ $("$PY" --version)"

# 2) 커밋 작성자: 비어 있으면 알려만 준다(개인 정보라 스크립트에 넣지 않는다)
if [ -z "$(git config user.email || true)" ]; then
  echo "! git 작성자가 없습니다: git config user.name \"이름\" && git config user.email \"메일\""
else
  echo "✓ git 작성자: $(git config user.name) <$(git config user.email)>"
fi

# 3) 수동 다운로드 자료를 둘 곳 (data/raw/는 .gitignore 대상, MDIS 원자료는 절대 커밋 금지)
mkdir -p data/raw/mdis
echo "✓ data/raw/mdis/ (MDIS 원자료), data/raw/ (부동산원·소득·카드 파일)"

# 4) 인증키 파일
if [ ! -f .env ]; then
  cp .env.example .env && chmod 600 .env
  echo "✓ .env 생성: MOLIT_KEY, KOSIS_KEY 값을 채우세요(실데이터 모드에서만 필요)"
else
  echo "✓ .env 있음"
fi

# 5) 선택 도구
command -v node >/dev/null && echo "✓ node $(node --version) (페이지 스크립트 문법 검사)" \
  || echo "- node 없음: 페이지 문법 검사는 CI에서만 실행 (brew install node)"
command -v gh >/dev/null && echo "✓ gh (이슈·워크플로 확인)" || echo "- gh 없음: brew install gh && gh auth login"
command -v claude >/dev/null && echo "✓ Claude Code (CLAUDE.md, .claude/skills 자동 로드)" \
  || echo "- Claude Code 없음: https://code.claude.com/docs 참고"

# 6) CI와 같은 점검 + 데모 빌드
echo; echo "== 테스트와 데모 빌드 =="
PYTHON="$PY" bash scripts/check.sh
echo
echo "완료. 미리보기: $PY -m http.server -d dist 8000  →  http://localhost:8000"
