"""중간 테이블 4종 → 웹용 흐름 데이터(data/flows.json).

레이어
  trade : 매매 자금. 출발지 = 매수자 거주지(추정), 도착지 = 매물 소재지.
          F_ij = N_j × share_bucket(i) × w_ij / Σ_bucket w,  w_ij = 최근 12개월 이주 OD(i→j) + 평활항
          금액 = F_ij × 평균거래가_j
  rent  : 전월세 보증금. 신규 계약만 쓰고, 출발지는 이주 OD 비율로 배분.
  equity: 매매 자금 중 추정 자기자본분 (leverage.py 가정). 흐름 배분은 trade와 같다.
  move  : 인구이동(주택 사유). 유일하게 '실측' OD 레이어.

인구이동 자료는 실거래보다 늦게 공개된다. 각 월의 가중치는 '그 달 이전에 실제로 존재하는
최근 12개월 OD'를 쓴다(od_window). 자료가 비어 있는 달을 그대로 합치면 가중치가 평활항만 남아
출발지가 균등 배분되는데, 에러 없이 틀린 지도가 나오므로 반드시 막아야 한다.

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
from leverage import equity  # noqa: E402

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


BUCKETS = ("same_sgg", "same_sido", "seoul", "other")
LAYER_KEYS = ("trade", "equity", "rent", "move")


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_tables(interim):
    trade = {}
    for r in read_csv(os.path.join(interim, "trade_region_month.csv")):
        n, v = int(r["trades"]), float(r["value_eok"])
        # 구버전 스키마(법인·자기자본 열 없음)도 읽는다. 자기자본은 평균가로 근사.
        eq = float(r["equity_eok"]) if r.get("equity_eok") else (n * equity(v / n, r["code"], r["ym"]) if n else 0.0)
        trade[(r["ym"], r["code"])] = dict(n=n, value=v, corp_n=int(r.get("corp_trades") or 0),
                                          corp_value=float(r.get("corp_value_eok") or 0), equity=eq)
    rent = {(r["ym"], r["code"]): dict(contracts=int(r["contracts"]), new=int(r["new_contracts"]),
                                       deposit=float(r["deposit_eok"]))
            for r in read_csv(os.path.join(interim, "rent_region_month.csv"))}
    share = {(r["ym"], r["code"]): {b: float(r[b]) for b in BUCKETS}
             for r in read_csv(os.path.join(interim, "buyer_origin_share.csv"))}
    od = defaultdict(dict)
    for r in read_csv(os.path.join(interim, "migration_od_month.csv")):
        od[r["ym"]][(r["src"], r["dst"])] = int(r["persons"])
    return dict(trade=trade, rent=rent, share=share, od=dict(od))


def od_window(od_months, ym, size=12):
    """ym 시점에 쓸 OD 월 목록: ym 이하에서 실제로 존재하는 최근 size개월. 없으면 가장 이른 size개월."""
    past = [m for m in od_months if m <= ym]
    return past[-size:] if past else od_months[:size]


def candidates(bucket, j, cap_codes):
    sj = SIDO_OF(j)
    if bucket == "same_sgg":
        return [j]
    if bucket == "same_sido":
        return [c for c in cap_codes if SIDO_OF(c) == sj and c != j]
    if bucket == "seoul":
        return [] if sj == "서울" else [c for c in cap_codes if SIDO_OF(c) == "서울"]
    # 기타: 서울·자기 시도를 뺀 수도권 + 비수도권
    return [c for c in cap_codes if SIDO_OF(c) not in (sj, "서울")] + [NONCAP["code"]]


def estimate(tables, ym, cap_codes, window):
    """한 달의 전체 OD 행렬을 추정한다. {layer: {(src, dst): [count, value]}}"""
    trail = defaultdict(int)
    for m in window:
        for k, v in tables["od"].get(m, {}).items():
            trail[k] += v
    by_dst = defaultdict(list)
    for (a, b), v in trail.items():
        by_dst[b].append((a, v))

    mats = {k: defaultdict(lambda: [0.0, 0.0]) for k in LAYER_KEYS}
    for j in cap_codes:
        t, sh = tables["trade"].get((ym, j)), tables["share"].get((ym, j))
        if t and t["n"] and sh:
            avg, avg_eq = t["value"] / t["n"], t["equity"] / t["n"]
            # 후보 출발지가 없는 버킷(예: 지역 일부만 돌릴 때)의 비중은 버리지 않고 나머지에 재배분한다.
            # 버리면 유입 합계가 실거래 건수보다 작아지는데 에러 없이 지나간다.
            cand = {b: candidates(b, j, cap_codes) for b in BUCKETS}
            tot_share = sum(sh[b] for b in BUCKETS if cand[b]) or 1
            for bucket in BUCKETS:
                s, cands = sh[bucket] / tot_share, cand[bucket]
                if s <= 0 or not cands:
                    continue
                w = {i: trail.get((i, j), 0) + SMOOTH for i in cands}
                tot = sum(w.values())
                for i, wi in w.items():
                    c = t["n"] * s * wi / tot
                    m = mats["trade"][(i, j)]; m[0] += c; m[1] += c * avg
                    m = mats["equity"][(i, j)]; m[0] += c; m[1] += c * avg_eq
        r, src = tables["rent"].get((ym, j)), by_dst.get(j)
        if r and src:
            dep = r["deposit"] / (r["contracts"] or 1)
            tot = sum(v for _, v in src)
            for a, pv in src:
                c = r["new"] * pv / tot
                m = mats["rent"][(a, j)]; m[0] += c; m[1] += c * dep
    for (a, b), pv in tables["od"].get(ym, {}).items():
        mats["move"][(a, b)][0] += pv
    return mats


def summarize(mat, layer, idx):
    """전체 행렬 → 웹용 요약 (지역별 통계, 시도 간 합계, 표시할 흐름 목록)."""
    key = 0 if layer == "move" else 1
    nc = idx[NONCAP["code"]]
    # in_cnt,out_cnt,in_val,out_val,intra_cnt,intra_val, 그중 비수도권 상대분: nc_in_cnt,nc_out_cnt,nc_in_val,nc_out_val
    stats = [[0.0] * 10 for _ in idx]
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
    ranked = sorted(((a, b, c, val) for (a, b), (c, val) in mat.items() if a != b), key=lambda x: -x[2 + key])
    # 전체 상위 N + 지역별 유입/유출 상위 PER_NODE: 작은 시군을 클릭해도 목록이 비지 않게
    keep, seen_in, seen_out = set(range(min(TOP_N, len(ranked)))), defaultdict(int), defaultdict(int)
    for k, (a, b, _, _) in enumerate(ranked):
        if seen_out[a] < PER_NODE or seen_in[b] < PER_NODE:
            keep.add(k)
        seen_out[a] += 1; seen_in[b] += 1
    return {
        "flows": [[idx[a], idx[b], round(c, 1), round(val)] for a, b, c, val in (ranked[k] for k in sorted(keep))],
        "stats": [[round(x, 1) for x in s] for s in stats],
        "sido": {k: [round(c), round(v)] for k, (c, v) in sido.items()},
    }


def load_nodes(geo_path):
    geo = json.load(open(geo_path, encoding="utf-8"))
    nodes = []
    for r in geo["regions"]:
        ax, ay = ANCHOR_OVERRIDE.get(r["code"], (r["cx"], r["cy"]))
        nodes.append(dict(code=r["code"], name=r["name"], short=r["short"], sido=r["sido"], ax=ax, ay=ay, rings=r["rings"]))
    nodes.append(dict(code=NONCAP["code"], name=NONCAP["name"], short=NONCAP["short"], sido="지방",
                      ax=NONCAP["anchor"][0], ay=NONCAP["anchor"][1], rings=[]))
    return nodes


def build(tables, nodes, demo):
    idx = {n["code"]: k for k, n in enumerate(nodes)}
    cap_codes = [n["code"] for n in nodes if n["code"] != NONCAP["code"]]
    months = sorted({ym for ym, _ in tables["trade"]})
    od_months = sorted(tables["od"])
    if not od_months:
        raise ValueError("인구이동 OD가 비어 있습니다. 매매·전월세 출발지를 배분할 수 없습니다.")
    out, windows = {}, {}
    for ym in months:
        win = od_window(od_months, ym)
        windows[ym] = [win[0], win[-1]]
        mats = estimate(tables, ym, cap_codes, win)
        base = []
        for n in nodes:
            t = tables["trade"].get((ym, n["code"]))
            r = tables["rent"].get((ym, n["code"]))
            base.append([t["n"] if t else 0, round(t["value"]) if t else 0,
                         r["contracts"] if r else 0, r["new"] if r else 0, round(r["deposit"]) if r else 0,
                         t["corp_n"] if t else 0, round(t["equity"]) if t else 0])
        out[ym] = {"layers": {k: summarize(mats[k], k, idx) for k in LAYER_KEYS}, "base": base}
    return {
        "meta": {"demo": demo, "months": months, "provisional": months[-2:] if not demo else [],
                 "events": EVENTS, "od_window": windows,
                 "od_missing": [m for m in months if m not in tables["od"]],
                 "base_cols": ["trades", "value_eok", "rent_contracts", "rent_new", "deposit_eok", "corp_trades", "equity_eok"],
                 "stat_cols": ["in_cnt", "out_cnt", "in_val", "out_val", "intra_cnt", "intra_val",
                               "nc_in_cnt", "nc_out_cnt", "nc_in_val", "nc_out_val"]},
        "nodes": nodes,
        "months": out,
    }


def main(demo):
    data = build(load_tables(INTERIM), load_nodes(os.path.join(ROOT, "data", "regions_geo.json")), demo)
    dst = os.path.join(ROOT, "data", "flows.json")
    json.dump(data, open(dst, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    m = data["meta"]
    print(f"wrote {dst}: {os.path.getsize(dst) / 1e6:.2f} MB, {len(m['months'])} months"
          + (f", OD 없는 달 {len(m['od_missing'])}개 (최근 가용 OD로 대체)" if m["od_missing"] else ""))


if __name__ == "__main__":
    main(demo="--real" not in sys.argv)
