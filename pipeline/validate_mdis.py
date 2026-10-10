"""MDIS 집계(시군구 OD)를 KOSIS 공식 월별 총계와 대조한다. 같은 주민등록 전입신고가 원천이라 거의 같아야 한다.

  python3 pipeline/validate_mdis.py                 # .env의 KOSIS_KEY로 KOSIS를 받아 비교
  python3 pipeline/validate_mdis.py --kosis FILE    # 받아 둔 KOSIS 총계 CSV(ym,code,in_total,out_total,intra)로 비교

MDIS에서 계산: 총전출 = 그 시군구에서 출발한 모든 이동(시군구 내, 비수도권행 포함), 총전입 = 들어온 모든 이동,
시군구 내 = 대각선. KOSIS T10·T20은 시군구 내 이동(T30)을 포함한다(estimate_od.py와 같은 정의).

시도 조건이 걸린 추출, 빠진 달, 코드 대응 오류(예: 부천 구 코드 이중 합산)는 비율이 1에서 크게 벗어나는 것으로 드러난다.
판정 기준(가정): 연도별 전체 합 비율이 ±2% 안, 시군구·월·항목 칸의 95% 이상이 ±5% 안. 벗어나면 종료 코드 1.
"""
import argparse, csv, gzip, io, json, os, sys
from collections import defaultdict
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
NONCAP = "00000"
TOTAL_TOL, CELL_TOL, CELL_SHARE = 0.02, 0.05, 0.95
FIELDS = ("in_total", "out_total", "intra")


def mdis_margins(od_rows):
    """OD 행(ym,src,dst,persons_all…) → {(ym, code): {in_total, out_total, intra}} (수도권 시군구만)."""
    m = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    for r in od_rows:
        ym, a, b, n = r["ym"], r["src"], r["dst"], int(r["persons_all"])
        if a != NONCAP:
            m[(ym, a)]["out_total"] += n
        if b != NONCAP:
            m[(ym, b)]["in_total"] += n
        if a == b != NONCAP:
            m[(ym, a)]["intra"] += n
    return m


def compare(od_rows, kosis_rows):
    """반환: {"years": {연도: {항목: 비율}}, "cells": [(비율, ym, code, 항목, mdis, kosis)], "ok": bool, "months": [...]}"""
    md = mdis_margins(od_rows)
    ks = {(r["ym"], r["code"]): {f: int(float(r[f])) for f in FIELDS} for r in kosis_rows}
    # MDIS에 있는 달이면 KOSIS의 모든 시군구를 비교한다. MDIS에서 통째로 빠진 시군구(시도 조건 추출 등)는 0으로 본다.
    md_months = {k[0] for k in md}
    common = sorted(k for k in ks if k[0] in md_months)
    tot = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    cells = []
    for key in common:
        for f in FIELDS:
            a, b = md[key][f] if key in md else 0, ks[key][f]
            t = tot[key[0][:4]][f]; t[0] += a; t[1] += b
            if b:
                cells.append((a / b, key[0], key[1], f, a, b))
    years = {y: {f: (v[0] / v[1] if v[1] else None) for f, v in fs.items()} for y, fs in sorted(tot.items())}
    within = sum(abs(c[0] - 1) <= CELL_TOL for c in cells) / len(cells) if cells else 0
    ok = bool(cells) and within >= CELL_SHARE and all(
        r is not None and abs(r - 1) <= TOTAL_TOL for fs in years.values() for r in fs.values())
    cells.sort(key=lambda c: -abs(c[0] - 1))
    return {"years": years, "cells": cells, "within": within, "ok": ok, "months": sorted({k[0] for k in common}),
            "mdis_only": sorted({k[0] for k in md} - {k[0] for k in ks})}


def read_od(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--od", default=os.path.join(ROOT, "data", "mdis", "migration_od_month.csv.gz"))
    ap.add_argument("--kosis", help="KOSIS 총계 CSV. 없으면 KOSIS_KEY로 받는다")
    a = ap.parse_args()
    od = read_od(a.od)
    months = sorted({r["ym"] for r in od})
    geo = json.loads(Path(os.path.join(ROOT, "data", "regions_geo.json")).read_text(encoding="utf-8"))["regions"]
    names = {r["code"]: r["name"] for r in geo}
    if a.kosis:
        with open(a.kosis, encoding="utf-8") as f:
            kosis_rows = list(csv.DictReader(f))
    else:
        import run
        from sources import kosis
        run.load_dotenv()
        key = os.environ.get("KOSIS_KEY") or sys.exit("KOSIS_KEY가 없습니다(.env). --kosis 로 파일을 줄 수도 있습니다.")
        rows, missing = kosis.margins(key, months[0], months[-1], list(names))
        kosis_rows = [dict(zip(kosis.MARGIN_COLS, r)) for r in rows]
        if missing:
            print(f"KOSIS 응답에 없는 시군구: {missing}")
    res = compare(od, kosis_rows)
    print(f"MDIS 집계 {months[0]}~{months[-1]}, KOSIS와 겹치는 달 {len(res['months'])}개")
    if res["mdis_only"]:
        print(f"KOSIS에 없는 달(비교 제외): {res['mdis_only']}")
    print("\n연도별 MDIS/KOSIS 합계 비율 (1.000이 일치)")
    for y, fs in res["years"].items():
        print(f"  {y}: " + ", ".join(f"{f} {r:.3f}" if r is not None else f"{f} -" for f, r in fs.items()))
    print(f"\n시군구·월·항목 {len(res['cells']):,}칸 중 ±{CELL_TOL:.0%} 안: {res['within']:.1%} (기준 {CELL_SHARE:.0%})")
    print("가장 크게 어긋난 칸:")
    for r, ym, c, f, x, k in res["cells"][:12]:
        print(f"  {ym} {c} {names.get(c, '?'):<10} {f:<9} MDIS {x:>7,} / KOSIS {k:>7,} = {r:.3f}")
    print("\n판정:", "통과" if res["ok"] else "불일치 - 위 목록의 시군구·달·항목부터 원인을 찾으세요")
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
