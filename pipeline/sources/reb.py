"""한국부동산원 R-ONE「(월) 매입자거주지별 아파트거래현황」로더.

R-ONE 통계표를 내려받은 CSV를 그대로 읽는다(2026-10 실제 파일로 확인). 형식:
  인코딩 CP949, 기간이 열로 펼쳐진 와이드 형식.
  열 0~7 : No, 시도, 시군구, 시군구 또는 일반구, 매입자거주지, 항목, 단위, 통계자료
  열 8~  : '2023년 1월' … 기간마다 한 열
  행     : 지역 × 매입자거주지(합계/관할시군구내/관할시도내/관할시도외_서울/관할시도외_기타) × 항목(동(호)수/면적)
  값에 쉼표가 들어가고(예: "4,529"), 자료 없는 달은 '-'다.
'동(호)수'(거래 건수)만 쓰고 면적은 버린다. 시 행은 구 합계와 일치하므로(검증함) 일반구가 없는 시는 시 행을 쓴다.

지역 이름 → 코드
  이 표에는 지역 코드가 없어 이름으로 맞춘다. 시도·시군구·일반구 3개 열을 조합한다.
  (서울, 종로구, 종로구) / (경기, 수원시, 장안구) / (인천, 부평구, 부평구)
  개편으로 폐지된 구는 '(구)' 접두어가 붙는다(인천 2026-07: (구)중구·(구)동구·(구)서구).
  전국이 들어 있어도 우리 77개만 고른다.

행정구역 개편 (국토부 실거래 API와 반대다)
  실거래 API는 과거 거래까지 새 코드로 다시 매긴다(옛 코드는 전 기간 0건).
  R-ONE은 시점으로 나눈다: 옛 구는 개편 전 달까지, 새 구는 개편 후 달부터. 그래서 옛 체계로 보면
  2026-07 이후 인천 중구·동구·서구가 빈다. 비는 (달, 지역)은 결과에서 빼고 몇 개인지 알린다.
  조용히 0으로 채우면 그 지역 매매 자금 출발지가 전부 0이 된다.
  행이 아예 없으면(이름 대응 실패) 에러, 행은 있는데 전 기간 거래가 0이면 empty 로 알린다(옹진군).

출력: data/interim/buyer_origin_share.csv  ym,code,same_sgg,same_sido,seoul,other (합이 1)
"""
import csv, json, os, re
from pathlib import Path

GU_SI = ("수원시", "성남시", "안양시", "안산시", "고양시", "용인시")  # 일반구가 있는 경기 6개 시
SIDO = {"11": "서울", "28": "인천", "41": "경기"}
OLD = {"28110": "(구)중구", "28140": "(구)동구", "28260": "(구)서구"}  # 인천 2026-07 개편으로 폐지
BUCKETS = {"same_sgg": "관할시군구내", "same_sido": "관할시도내", "seoul": "관할시도외_서울", "other": "관할시도외_기타"}
ITEM = "동(호)수"
OUT_COLS = ["ym", "code", "same_sgg", "same_sido", "seoul", "other"]
GEO = os.path.join(os.path.dirname(__file__), "..", "..", "data", "regions_geo.json")


def region_key(name, code):
    """우리 지역 이름 → R-ONE 의 (시도, 시군구, 하위) 조합."""
    sd = SIDO[code[:2]]
    for si in GU_SI:
        if name.startswith(si):
            return sd, si, name[len(si):]
    nm = OLD.get(code, name)
    return sd, nm, nm


def _num(v):
    v = (v or "").replace(",", "").replace('"', "").strip()
    return None if v in ("", "-", "X") else float(v)


def load(src, dst, geo_path=GEO):
    """반환: {"rows": 출력 행 수, "months": [월], "gaps": {코드: [빈 달]}}"""
    with open(src, encoding="cp949", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"{os.path.basename(src)}: 빈 파일입니다.")
    hdr, body = rows[0], [r for r in rows[1:] if r and r[0].strip()]
    months = {}
    for i, h in enumerate(hdr[8:], 8):
        m = re.match(r"(\d{4})년\s*(\d{1,2})월", h.strip())
        if m:
            months[f"{m.group(1)}-{int(m.group(2)):02d}"] = i
    if not months:
        raise ValueError(f"{os.path.basename(src)}: 기간 열('2023년 1월' 형식)을 찾지 못했습니다. 머리글: {hdr[:10]}")
    table = {(r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip()): r for r in body if r[5].strip() == ITEM}
    if not table:
        raise ValueError(f"{os.path.basename(src)}: 항목이 '{ITEM}'인 행이 없습니다. 항목 열 값: {sorted({r[5] for r in body})[:5]}")
    regions = json.loads(Path(geo_path).read_text(encoding="utf-8"))["regions"]
    out, gaps, unmapped, empty = [], {}, [], []
    for reg in regions:
        code, key = reg["code"], region_key(reg["name"], reg["code"])
        picked = {b: table.get((*key, kr)) for b, kr in BUCKETS.items()}
        if all(v is None for v in picked.values()):
            unmapped.append((code, reg["name"], key))
            continue
        blank = []
        for ym, i in sorted(months.items()):
            vals = [(_num(picked[b][i]) if picked[b] is not None else None) for b in OUT_COLS[2:]]
            s = sum(v for v in vals if v is not None)
            if not s:
                blank.append(ym)
                continue
            out.append([ym, code] + [round((v or 0) / s, 4) for v in vals])
        if len(blank) == len(months):
            empty.append(code)  # 행은 있는데 전 기간 거래 0 (예: 옹진군 - 섬, 아파트 거래 없음)
        elif blank:
            gaps[code] = blank
    if unmapped:
        raise ValueError(f"R-ONE 자료에서 {len(unmapped)}개 지역을 찾지 못했습니다(이름 대응 또는 추출 범위 문제): "
                         f"{unmapped[:5]}. 시도·시군구·일반구 이름이 표와 같은지, 서울·인천·경기가 모두 들어 있는지 확인하세요.")
    with open(dst, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g); w.writerow(OUT_COLS); w.writerows(out)
    return {"rows": len(out), "months": sorted(months), "gaps": gaps, "empty": empty}
