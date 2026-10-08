# 외부 스킬 출처

| 스킬 | 원본 | 커밋 | 라이선스 |
|---|---|---|---|
| design-taste-frontend | https://github.com/Leonxlnx/taste-skill (`skills/taste-skill`) | b482f7a | MIT |
| web-design-guidelines | https://github.com/vercel-labs/agent-skills (`skills/web-design-guidelines`) | 063bee9 | MIT (README 표기) |
| agent-browser | https://github.com/vercel-labs/agent-browser (`skills/agent-browser`) | 0207911 | Apache-2.0 |

## 이 프로젝트에서의 적용 범위

- **design-taste-frontend**: 스스로 "대시보드·데이터 UI는 범위 밖"이라고 명시한다(13장). 기본 스택(React/Tailwind)도
  이 프로젝트(바닐라 JS + Canvas)와 다르다. 9장 'AI 티 나는 패턴'과 14장 점검표 중 해당 항목만 적용한다.
- **web-design-guidelines**: 실행할 때마다 vercel-labs/web-interface-guidelines 의 command.md를 받아 점검한다.
- **agent-browser**: CLI가 필요하다. `npm i -g agent-browser`.
  클라우드 세션에서는 Chrome 다운로드 대신 `AGENT_BROWSER_EXECUTABLE_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome` 사용.

요청된 'Awesome design'(VoltAgent/awesome-claude-design)은 SKILL.md가 없는 DESIGN.md 링크 모음이라 설치하지 않았다.
