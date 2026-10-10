# 수도권 머니 플라이트 — 작업 메모

수도권 77개 시군구 사이 매매 자금·전월세 보증금·인구 이동을 항공 관제판처럼 보여주는 정적 웹 서비스.
월 배치(Python) → `data/flows.json` + `data/detail/*.json` → `dist/index.html`(Canvas 2D, 외부 JS 없음).
설명·커밋 메시지·코드 주석·오류 메시지는 한국어.

## 명령

```bash
bash scripts/setup.sh                      # 처음 한 번: 점검 + 데모 빌드
bash scripts/check.sh                      # push 전: CI와 같은 점검(테스트, 데모 빌드, 페이지 문법)
python3 pipeline/run.py demo               # 데모 데이터 → dist/
python3 pipeline/run.py mdis               # MDIS 원자료(data/raw/mdis) → data/mdis/migration_od_month.csv.gz
python3 pipeline/run.py real --from 2024-01 --to 2026-08   # .env의 MOLIT_KEY, KOSIS_KEY 사용
python3 -m http.server -d dist 8000        # 미리보기 (file://로 열면 상세 파일 요청이 막힌다)
```

## 지켜야 할 것

- 파이프라인은 표준 라이브러리만 쓴다(예외: `build_geo.py`의 shapely, 결과물 `data/regions_geo.json`은 커밋돼 있음).
- 테스트는 `unittest`. CI는 `-W error::ResourceWarning`이다. 파일은 `with` 또는 `Path.read_text()`로 연다.
- 커밋 금지: `data/raw/`(특히 MDIS 원자료는 재배포 금지), `data/interim/`, `dist/`, `data/flows.json`, `data/detail/`, `.env`.
  MDIS는 `run.py mdis`가 만든 시군구 집계 `data/mdis/*.csv.gz`만 커밋한다.
- 추정·대체한 값은 화면에 반드시 표시한다(`meta.od_estimated`, `meta.move_src` → UI의 '추정' 라벨).
- 조용히 버리지 말고 크게 실패한다: 대응표에 없는 코드, 필수 열 누락, 시도 조건이 걸린 MDIS 추출은 에러.

## 데이터 함정 (이미 한 번씩 밟은 것)

- **행정구역 코드 두 체계**: 통계청(인천 23, 경기 31) vs 행정안전부(인천 28, 경기 41).
  11110·11140·11170·11200·11230은 두 체계에 다 있지만 다른 구다. `data/sgg_codes.csv`로 변환, MDIS는 파일마다 판별.
  시도 코드 11·26·29·31·36도 두 체계에 다 있고 뜻이 다르다(31 = 통계청 경기 / 행정안전부 울산).
  판별은 한 체계에만 있는 시도 코드(`mdis.ONLY`)로만 하고, 근거가 없거나 섞이면 에러.
- **부천**: 41192/41194/41196(2024 구 재설치) → 41190으로 합친다.
- **실거래 해제**: `cdealType == 'O'`는 제외. 해제 신고는 '해제 확정일부터 30일 내'라 계약일 기준 상한이 없다.
  그래서 캐시는 계약월 나이별로 다시 받는다(`molit.RefreshPolicy`), 병합은 (월, 시군구) 묶음 단위로 통째 교체.
- **MDIS**: 연간 자료(현재 2025년까지). 세대관련연간자료만 쓴다(인구관련은 불필요).
  시도 입력조건을 걸면 인천·비수도권 이동이 빠져 IPF 추정이 틀어진다 → 로더가 거부한다.
  전입사유 코드: 1 직업, 2 가족, 3 주택, 4 교육, 5 주거환경, 6 자연환경, 9 기타 (MDIS 코드표 확인). 화면은 3만 쓴다.
- **KOSIS** `DT_1B26001_A01`: 행정안전부 코드, T10 총전입 / T20 총전출 / T30 시군구 내. 응답 기간이 겹치면 더하지 말고 덮어쓴다.
- **2026년 이후 인구이동**: `estimate_od.py`가 MDIS 최근 12개월 패턴을 KOSIS 월별 총계에 맞춰 IPF.
  데모 채점 WAPE 10.5%(최신월 대체 17.7%). KOSIS도 없는 달은 직전 달 대체 표시.

## 흐름 모형 요약

- 매매 자금 출발지 = 부동산원 매입자거주지 버킷 비중 × 최근 12개월 인구이동 OD 가중.
- 자기자본 = 가격 − 0.65 × min(LTV × 가격, 한도). 한도는 6·27, 10·15 대책 반영(`leverage.py`).
- 전월세는 신규 계약만. 선 굵기·색은 전체 기간 공통 5분위.

## 자동화

- `ci.yml`: push마다 테스트·데모 빌드·문법 검사.
- `check-updates.yml`: 매일 00:13 UTC. KOSIS 새 달·MDIS 새 연도가 나오면 이슈를 열고, 반영되면(`data/coverage.json` 커밋) 닫는다.
- 인증키는 GitHub Secrets(`MOLIT_KEY`, `KOSIS_KEY`), 로컬은 `.env`.

## 진행 상황 (2026-10 기준)

- 완료: 데모 전 구간, UI(지도·관제판·상세 분석·핀치 줌), 캐시·해제 처리, KOSIS 연동, 추정, 알림.
- 기다리는 자료: MDIS 세대관련연간자료 집계(`run.py mdis` 결과 .gz),
  R-ONE 매입자거주지별 아파트매매거래 파일, (선택) TASIS 소득·카드 파일.
- 다음: 위 자료로 첫 실데이터 실행 → `coverage.json` 커밋 → 열린 알림 이슈 자동 종료 확인.
  `sources/reb.py`, `nts_income.py`, `card.py`의 열 이름(`COLS`)은 실제 파일로 아직 확인하지 못했다.
