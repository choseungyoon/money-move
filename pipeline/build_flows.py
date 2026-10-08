"""중간 테이블 4종 → 웹용 흐름 데이터(data/flows.json).

레이어
  trade : 매매 자금. 출발지 = 매수자 거주지(추정), 도착지 = 매물 소재지.
          F_ij = N_j × share_bucket(i) × w_ij / Σ_bucket w,  w_ij = 최근 12개월 이주 OD(i→j) + 평활항
          금액 = F_ij × 평균거래가_j
  rent  : 전월세 보증금. 신규 계약만 쓰고, 출발지는 이주 OD 비율로 배분.
  move  : 인구이동(주택 사유). 유일하게 '실측' OD 레이어.

매입자 거주지 버킷(부동산원 분류)별 후보 출발지
  same_sgg  : j 자신(구 내부 거래, 화살표 없음)
  same_sido : 같은 시도 내 다른 시군구
  seoul     : 서울 25개 구 (j가 서울이면 해당 없음)
  other     : 나머지 수도권 + 비수도권
"""
import csv, json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from regions import ANCHOR_OVERRIDE, NONCAP, SIDO_OF  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
INTERIM = os.path.join(ROOT, "data", "interim")
TOP_N = 220           # 월·레이어별로 화면에 보낼 전체 상위 흐름 수 (집계 통계는 전체 행렬 기준)
PER_NODE = 6          # 지역별로 추가 보장하는 유입·유출 상위 흐름 수
SMOOTH = 0.5          # OD 가중치 평활항: 이주 기록이 없는 쌍도 매수 가능성을 0으로 두지 않는다
EVENTS = [
    {"ym": "2025-03", "label": "강남3구·용산 토허제 지정", "short": "토허제"},
    {"ym": "2025-06", "label": "6·27 대출규제", "short": "6·27"},
    {"ym": "2025-10", "label": "10·15 대책", "short": "10·15"},
]


def rows(name):
    with open(os.path.join(INTERIM, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(demo):
    geo = json.load(open(os.path.join(ROOT, "data", "regions_geo.json"), encoding="utf-8"))
    nodes = [dict(code=r["code"], name=r["name"], short=r["short"], sido=r["sido"],
                  ax=ANCHOR_OVERRIDE.get(r["code"], (r["cx"], r["cy"]))[0],
                  ay=ANCHOR_OVERRIDE.get(r["code"], (r["cx"], r["cy"]))[1],
                  rings=r["rings"]) for r in geo["regions"]]
    nodes.append(dict(code=NONCAP["code"], name=NONCAP["name"], short=NONCAP["short"], sido="지방",
                      ax=NONCAP["anchor"][0], ay=NONCAP["anchor"][1], rings=[]))
    idx = {n["code"]: k for k, n in enumerate(nodes)}
    cap = [n["code"] for n in nodes if n["code"] != NONCAP["code"]]

    trade = {(r["ym"], r["code"]): (int(r["trades"]), float(r["value_eok"])) for r in rows("trade_region_month.csv")}
    rent = {(r["ym"], r["code"]): r for r in rows("rent_region_month.csv")}
    share = {(r["ym"], r["code"]): r for r in rows("buyer_origin_share.csv")}
    od = defaultdict(dict)
    for r in rows("migration_od_month.csv"):
        od[r["ym"]][(r["src"], r["dst"])] = int(r["persons"])
    months = sorted({ym for ym, _ in trade})

    def candidates(bucket, j):
        sj = SIDO_OF(j)
        if bucket == "same_sgg":
            return [j]
        if bucket == "same_sido":
            return [c for c in cap if SIDO_OF(c) == sj and c != j]
        if bucket == "seoul":
            return [] if sj == "서울" else [c for c in cap if SIDO_OF(c) == "서울"]
        return [c for c in cap if SIDO_OF(c) not in (sj, "서울" if sj != "서울" else "-")] + [NONCAP["code"]]

    out_months = {}
    for t, ym in enumerate(months):
        trail = defaultdict(int)  # 최근 12개월 이주 OD
        for ym2 in months[max(0, t - 11): t + 1]:
            for k, v in od[ym2].items():
                trail[k] += v
        dest_in = defaultdict(int)
        for (a, b), v in trail.items():
            dest_in[b] += v

        mats = {"trade": defaultdict(lambda: [0.0, 0.0]), "rent": defaultdict(lambda: [0.0, 0.0]),
                "move": defaultdict(lambda: [0.0, 0.0])}
        for j in cap:
            n, v = trade.get((ym, j), (0, 0.0))
            sh = share.get((ym, j))
            if n and sh:
                avg = v / n
                for bucket in ("same_sgg", "same_sido", "seoul", "other"):
                    s = float(sh[bucket])
                    cands = candidates(bucket, j)
                    if s <= 0 or not cands:
                        continue
                    w = {i: trail.get((i, j), 0) + SMOOTH for i in cands}
                    tot = sum(w.values())
                    for i, wi in w.items():
                        c = n * s * wi / tot
                        m = mats["trade"][(i, j)]
                        m[0] += c; m[1] += c * avg
            r = rent.get((ym, j))
            if r and dest_in[j]:
                new = int(r["new_contracts"]); contracts = int(r["contracts"]) or 1
                dep = float(r["deposit_eok"]) / contracts
                for (a, b), pv in trail.items():
                    if b != j:
                        continue
                    c = new * pv / dest_in[j]
                    m = mats["rent"][(a, j)]
                    m[0] += c; m[1] += c * dep
        for (a, b), pv in od[ym].items():
            mats["move"][(a, b)][0] += pv

        layers = {}
        for layer, mat in mats.items():
            key = 0 if layer == "move" else 1
            # in_cnt,out_cnt,in_val,out_val,intra_cnt,intra_val, 그중 비수도권 상대분: nc_in_cnt,nc_out_cnt,nc_in_val,nc_out_val
            stats = [[0.0] * 10 for _ in nodes]
            nc = idx[NONCAP["code"]]
            sido = defaultdict(lambda: [0.0, 0.0])
            for (a, b), (c, val) in mat.items():
                ia, ib = idx[a], idx[b]
                if a == b:
                    stats[ia][4] += c; stats[ia][5] += val
                    continue
                stats[ib][0] += c; stats[ib][2] += val
                stats[ia][1] += c; stats[ia][3] += val
                if ia == nc:
                    stats[ib][6] += c; stats[ib][8] += val
                if ib == nc:
                    stats[ia][7] += c; stats[ia][9] += val
                sk = f"{SIDO_OF(a)}>{SIDO_OF(b)}"
                sido[sk][0] += c; sido[sk][1] += val
            ranked = sorted(((a, b, c, val) for (a, b), (c, val) in mat.items() if a != b),
                            key=lambda x: -x[2 + key])
            # 전체 상위 N + 지역별 유입/유출 상위 PER_NODE: 작은 시군을 클릭해도 목록이 비지 않게
            keep, seen_in, seen_out = set(range(min(TOP_N, len(ranked)))), defaultdict(int), defaultdict(int)
            for k, (a, b, _, _) in enumerate(ranked):
                if seen_out[a] < PER_NODE or seen_in[b] < PER_NODE:
                    keep.add(k)
                seen_out[a] += 1; seen_in[b] += 1
            top = [ranked[k] for k in sorted(keep)]
            layers[layer] = {
                "flows": [[idx[a], idx[b], round(c, 1), round(val)] for a, b, c, val in top],
                "stats": [[round(x, 1) for x in s] for s in stats],
                "sido": {k: [round(c), round(v)] for k, (c, v) in sido.items()},
            }
        base = []
        for n in nodes:
            tr = trade.get((ym, n["code"]), (0, 0.0)); rr = rent.get((ym, n["code"]))
            base.append([tr[0], round(tr[1]), int(rr["contracts"]) if rr else 0,
                         int(rr["new_contracts"]) if rr else 0, round(float(rr["deposit_eok"])) if rr else 0])
        out_months[ym] = {"layers": layers, "base": base}

    data = {
        "meta": {"demo": demo, "months": months, "provisional": months[-2:] if not demo else [],
                 "events": EVENTS,
                 "base_cols": ["trades", "value_eok", "rent_contracts", "rent_new", "deposit_eok"],
                 "stat_cols": ["in_cnt", "out_cnt", "in_val", "out_val", "intra_cnt", "intra_val",
                               "nc_in_cnt", "nc_out_cnt", "nc_in_val", "nc_out_val"]},
        "nodes": nodes,
        "months": out_months,
    }
    dst = os.path.join(ROOT, "data", "flows.json")
    json.dump(data, open(dst, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {dst}: {os.path.getsize(dst) / 1e6:.2f} MB, {len(months)} months")


if __name__ == "__main__":
    main(demo="--real" not in sys.argv)
