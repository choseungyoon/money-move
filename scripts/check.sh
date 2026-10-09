#!/usr/bin/env bash
# CI(.github/workflows/ci.yml)와 같은 점검을 로컬에서 실행한다. push 전에 돌린다.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}

"$PY" -W error::ResourceWarning -m unittest discover -s tests
"$PY" pipeline/run.py demo
if command -v node >/dev/null; then
  node -e "
    const s = require('fs').readFileSync('dist/index.html', 'utf8');
    new Function(s.match(/<script>([\s\S]*)<\/script>/)[1]);
    console.log('페이지 스크립트 문법 ok', (s.length / 1e6).toFixed(2) + ' MB');
  "
else
  echo "node가 없어 페이지 스크립트 문법 검사는 건너뜀 (CI에서는 실행됨)"
fi
