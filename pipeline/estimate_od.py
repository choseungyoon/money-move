"""MDIS 이후 기간의 월별 인구이동 OD 추정 (KOSIS 월별 총계 + 최근 12개월 MDIS 패턴, IPF).

MDIS 마이크로데이터(어디서 어디로)는 연 단위로 1년 넘게 늦게 나오고, KOSIS(시군구별 총전입·총전출·
시군구 내 이동)는 약 한 달 늦게 나온다. 둘을 합쳐 MDIS 이후 달을 추정한다.

  KOSIS는 경기 일반구를 시 단위로만 준다(kosis.SI_OF_GU). 그래서 제약은 '묶음' 단위다: 일반구는 시 하나로,
  나머지 시군구는 혼자서 묶음. 시 총계의 시군구 내 이동에는 그 시의 구 사이 이동이 들어 있다.
  1) 묶음 안 칸(대각선, 일반구는 같은 시의 구 사이 칸까지) 합 = KOSIS T30. 기준 패턴 비율로 나눈다
  2) 묶음 사이 칸: 기준 패턴(최근 12개월 MDIS, 전체 사유)에서 시작해
     묶음 행 합 = 총전출 - 시군구내, 묶음 열 합 = 총전입 - 시군구내 가 되도록 번갈아 비례 조정(IPF)
     비수도권 노드는 총계가 없어 고정하지 않는다(수도권 쪽 제약을 따라간다)
     같은 시 안 구끼리의 비율은 KOSIS에 정보가 없어 기준 패턴을 따른다
  3) 주택 사유 = 추정치 × 칸별 주택 사유 비율(기준 기간). 비율이 없으면 시군구 평균 비율

가정: 이동 패턴과 칸별 주택 사유 비율이 기준 기간과 같다. 정책 변화 등으로 패턴이 바뀌면 오차가 커진다.
출력: data/interim/migration_od_est.csv  ym,src,dst,persons,persons_all  (화면에서 '추정' 표시)
"""
import csv, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from sources.kosis import SI_OF_GU  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
INTERIM = os.path.join(ROOT, "data", "interim")
NONCAP = "00000"
ITER = 60


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def base_pattern(od_rows, months):
    """최근 12개월 MDIS: 전체 사유 행렬과 칸별 주택 사유 비율."""
    allm, hous = defaultdict(float), defaultdict(float)
    keep = set(months)
    for r in od_rows:
        if r["ym"] in keep:
            k = (r["src"], r["dst"])
            allm[k] += float(r.get("persons_all") or r["persons"]); hous[k] += float(r["persons"])
    return allm, hous


def group(c):
    return SI_OF_GU.get(c, c)


def ipf(seed, row_target, col_target, iters=ITER):
    """seed: {(i,j): v} 서로 다른 묶음 사이 칸. row_target/col_target: 제약이 있는 묶음만. 반환: 조정된 칸."""
    m = {k: v for k, v in seed.items() if v > 0}
    rows, cols = defaultdict(list), defaultdict(list)
    for (i, j) in m:
        rows[group(i)].append((i, j)); cols[group(j)].append((i, j))
    for _ in range(iters):
        for i, t in row_target.items():
            s = sum(m[k] for k in rows[i])
            if s > 0:
                f = t / s
                for k in rows[i]:
                    m[k] *= f
        for j, t in col_target.items():
            s = sum(m[k] for k in cols[j])
            if s > 0:
                f = t / s
                for k in cols[j]:
                    m[k] *= f
    return m


def estimate(od_rows, margin_rows, base_months=12):
    od_months = sorted({r["ym"] for r in od_rows})
    if not od_months:
        raise ValueError("기준이 될 MDIS 인구이동 자료가 없습니다.")
    last = od_months[-1]
    base = od_months[-base_months:]
    allm, hous = base_pattern(od_rows, base)
    seed, block = {}, defaultdict(dict)  # 묶음 사이 칸, 묶음 안 칸
    for k, v in allm.items():
        a, b = group(k[0]), group(k[1])
        (seed if a != b else block[a])[k] = v / len(base)
    # 시군구별 평균 주택 사유 비율(칸 비율이 없을 때 대체)
    dst_all, dst_h = defaultdict(float), defaultdict(float)
    for k, v in allm.items():
        dst_all[k[1]] += v; dst_h[k[1]] += hous[k]
    by_month = defaultdict(dict)
    for r in margin_rows:
        if r["ym"] > last:
            by_month[r["ym"]][r["code"]] = r
    out = []
    for ym in sorted(by_month):
        mg = by_month[ym]
        rt = {c: max(0.0, float(r["out_total"]) - float(r["intra"])) for c, r in mg.items()}
        ct = {c: max(0.0, float(r["in_total"]) - float(r["intra"])) for c, r in mg.items()}
        est = ipf(seed, rt, ct)
        for c, r in mg.items():
            cells = block.get(c)
            if not cells or not sum(cells.values()):
                if c in SI_OF_GU.values():
                    raise ValueError(f"{ym} {c}: 기준 기간 MDIS에 이 시의 구 안 이동이 없어 시군구 내 이동을 구별로 나눌 수 없습니다.")
                cells = {(c, c): 1.0}
            s = sum(cells.values())
            for k, v in cells.items():
                est[k] = float(r["intra"]) * v / s
        for (a, b), v in sorted(est.items()):
            if v < 0.5:
                continue
            ratio = hous[(a, b)] / allm[(a, b)] if allm.get((a, b)) else (dst_h[b] / dst_all[b] if dst_all.get(b) else 0)
            out.append([ym, a, b, round(v * ratio), round(v)])
    return out, last, sorted(by_month)


def main():
    od_path, mg_path = os.path.join(INTERIM, "migration_od_month.csv"), os.path.join(INTERIM, "kosis_sgg_month.csv")
    dst = os.path.join(INTERIM, "migration_od_est.csv")
    if not os.path.exists(mg_path):
        print("KOSIS 월별 총계(kosis_sgg_month.csv)가 없어 추정을 건너뜁니다.")
        if os.path.exists(dst):
            os.remove(dst)
        return
    rows, last, months = estimate(read(od_path), read(mg_path))
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ym", "src", "dst", "persons", "persons_all"]); w.writerows(rows)
    print(f"인구이동 추정: MDIS {last} 이후 {len(months)}개월 ({months[0] if months else '-'} ~ {months[-1] if months else '-'}), {len(rows)}행")


if __name__ == "__main__":
    main()
