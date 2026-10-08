"""데모용 중간 테이블 생성기.

실데이터 수집기(sources/*)와 '같은 스키마'의 CSV를 data/interim 에 만든다.
이후 build_flows.py 는 실데이터/데모를 구분하지 않는다.

모형:
  - 매매 건수   N_j,t = 인구_j × 기본회전율 × 시장지수_t(권역) × 정책효과 × 잡음
  - 인구이동 OD M_ij,t = K × P_i^0.9 × P_j^0.8 × exp(-d_ij/λ) × 가격차^0.5 × 회랑가중 × 계절
  - 매입자 거주지 비중: 권역 기본값 + 서울 근접도 + 정책 시나리오
수치는 공개 통계의 '규모감'에 맞춘 추정이며, 실제 통계가 아니다.
"""
import csv, json, math, os, random, sys

sys.path.insert(0, os.path.dirname(__file__))
from regions import PRIORS, ANCHOR_OVERRIDE, NONCAP  # noqa: E402

random.seed(20261008)
ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "interim")

MONTHS = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)][:32]  # 2024-01 ~ 2026-08

# 거래량 지수(월). 2025년 이벤트 구간은 실제 시장 흐름의 '방향'만 반영한 시나리오.
MKT_SEOUL = [.55, .55, .70, .75, .85, 1.05, 1.40, 1.10, .60, .70, .60, .55,
             .55, .95, 1.50, .80, .90, 1.50, .70, .65, 1.10, 1.00, .45, .45,
             .50, .55, .65, .70, .72, .74, .70, .72]
MKT_GG = [.70, .70, .80, .85, .90, 1.00, 1.15, 1.05, .80, .85, .75, .70,
          .70, .85, 1.10, .90, .95, 1.20, .85, .80, 1.00, 1.05, .90, .85,
          .80, .85, .92, .95, .95, .97, .95, .96]

TOP4 = {"11680", "11650", "11710", "11170"}  # 강남·서초·송파·용산 (2025.3.24 토허제)
REG12 = {"41290", "41210", "41131", "41133", "41135", "41111", "41115", "41117",
         "41173", "41465", "41430", "41450"}  # 10·15 대책 경기 12곳
BALLOON = {"41310", "41590", "41570", "41281", "41285", "41287", "41360", "41190", "28185", "28260"}

# 대표적인 이주 회랑(철도·도로축). 거리감쇠만으로는 안 잡히는 방향성 보정.
CORRIDORS = [
    ({"11680", "11650", "11710"}, {"41135", "41465", "41450", "41290", "41131", "41463"}),
    ({"11440", "11380", "11410"}, {"41281", "41285", "41287", "41480", "41570"}),
    ({"11350", "11320", "11260", "11305"}, {"41360", "41150", "41310", "41630"}),
    ({"11500", "11470"}, {"41570", "41190", "28260", "28245"}),
    ({"11620", "11545", "11530", "11590"}, {"41210", "41171", "41173", "41190"}),
    ({"11740", "11710"}, {"41450", "41310", "41360"}),
    ({"11680", "11650"}, {"41590", "41117"}),
]


def ym_idx(ym):
    return MONTHS.index(ym)


def after(ym, cut):
    return ym >= cut


def haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def load_anchors():
    geo = json.load(open(os.path.join(ROOT, "data", "regions_geo.json"), encoding="utf-8"))
    return {r["code"]: ANCHOR_OVERRIDE.get(r["code"], (r["cx"], r["cy"])) for r in geo["regions"]}


def noise(s=0.08):
    return max(0.5, random.gauss(1, s))


def write(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    anc = load_anchors()
    codes = sorted(PRIORS)
    pop = {c: PRIORS[c][0] for c in codes}
    seoul = [c for c in codes if c.startswith("11")]

    # 서울 근접도: 서울 구 앵커까지 최소 거리 기반
    prox = {c: 0.0 if c.startswith("11") else
            math.exp(-min(haversine(anc[c], anc[s]) for s in seoul) / 9) for c in codes}

    corridor = {}
    for srcs, dsts in CORRIDORS:
        for a in srcs:
            for b in dsts:
                corridor[(a, b)] = corridor.get((a, b), 1) * 1.7
                corridor[(b, a)] = corridor.get((b, a), 1) * 1.25

    trade_rows, rent_rows, share_rows, mig_rows = [], [], [], []
    base_od = {}
    for i in codes:
        for j in codes:
            if i == j:
                continue
            d = max(haversine(anc[i], anc[j]), 2.5)
            push = (PRIORS[i][1] / PRIORS[j][1]) ** 0.5
            base_od[(i, j)] = pop[i] ** 0.9 * pop[j] ** 0.8 * math.exp(-d / 11) * push * corridor.get((i, j), 1)
    # 서울→경기 '주택' 사유 이동이 기준월에 월 1.2만 명 수준이 되도록 K 보정
    k = 12000 / sum(v for (i, j), v in base_od.items() if i.startswith("11") and j.startswith("41"))

    for t, ym in enumerate(MONTHS):
        mo = int(ym[5:])
        yrs = t / 12
        season = 1.25 if mo in (2, 3) else 1.1 if mo in (8, 9) else 0.92 if mo in (5, 6, 11) else 1.0

        for c in codes:
            p, price, dep = PRIORS[c]
            is_seoul = c.startswith("11")
            mkt = MKT_SEOUL[t] if is_seoul else MKT_GG[t]
            policy = 1.0
            if c in TOP4 and after(ym, "2025-04"):
                policy *= 0.55
            if is_seoul and after(ym, "2025-11"):
                policy *= 0.7
            if c in REG12 and after(ym, "2025-11"):
                policy *= 0.75
            if c in BALLOON and after(ym, "2025-11"):
                policy *= 1.3
            rate = 0.00060 if is_seoul else 0.00068
            n = max(1, round(p * 1e4 * rate * mkt * policy * noise()))
            growth = (1.09 if is_seoul else 1.02) ** yrs * (1 + 0.06 * (c in TOP4) * min(yrs, 1.2))
            trade_rows.append([ym, c, n, round(n * price * growth * noise(0.04), 2)])

            rc = max(1, round(p * 1e4 * 0.0022 * season * noise()))
            new = round(rc * random.uniform(0.52, 0.6))
            jeon = round(rc * (0.48 if is_seoul else 0.55))
            rent_rows.append([ym, c, rc, new, jeon, rc - jeon, round(rc * dep * noise(0.03), 2)])

            # 매입자 거주지 비중 (REB 분류)
            if is_seoul:
                same_sgg, seoul_sh = 0.40, 0.0
                same_sido = 0.38
                other = 0.22 + (0.05 if c in TOP4 else 0)
                if c in TOP4 and after(ym, "2025-04"):
                    same_sido *= 0.8; other *= 0.7
                if after(ym, "2025-11"):
                    other *= 0.8
            else:
                same_sgg = 0.58 - 0.18 * prox[c]
                same_sido = 0.16 if c.startswith("41") else 0.22
                seoul_sh = 0.05 + 0.30 * prox[c] + (0.08 if c in {"41290", "41450", "41135"} else 0)
                other = 0.07
                if after(ym, "2025-11"):
                    if c in REG12:
                        seoul_sh *= 0.65
                    elif c in BALLOON:
                        seoul_sh *= 1.45
                # 서울 매수세는 서울 시장과 같이 움직인다
                seoul_sh *= 0.75 + 0.25 * MKT_SEOUL[t]
            v = [same_sgg, same_sido, seoul_sh, other]
            v = [x * noise(0.05) for x in v]
            s = sum(v)
            share_rows.append([ym, c, *[round(x / s, 4) for x in v]])

        # 인구이동 OD
        mm = season * (0.75 + 0.25 * (MKT_SEOUL[t] + MKT_GG[t]) / 2)
        for (i, j), v in base_od.items():
            f = v * k * mm
            if j in BALLOON and i.startswith("11") and after(ym, "2025-11"):
                f *= 1.2
            n = round(f * noise(0.12))
            if n >= 3:
                mig_rows.append([ym, i, j, n])
        for c in codes:
            mig_rows.append([ym, c, c, round(pop[c] * 1e4 * 0.0024 * mm * noise())])
            mig_rows.append([ym, NONCAP["code"], c, round(pop[c] * 1e4 * 0.00022 * mm * noise() * (1.3 if c.startswith("11") else 1))])
            mig_rows.append([ym, c, NONCAP["code"], round(pop[c] * 1e4 * 0.0002 * mm * noise())])

    write("trade_region_month.csv", ["ym", "code", "trades", "value_eok"], trade_rows)
    write("rent_region_month.csv", ["ym", "code", "contracts", "new_contracts", "jeonse", "wolse", "deposit_eok"], rent_rows)
    write("buyer_origin_share.csv", ["ym", "code", "same_sgg", "same_sido", "seoul", "other"], share_rows)
    write("migration_od_month.csv", ["ym", "src", "dst", "persons"], mig_rows)
    print(f"demo interim: {len(MONTHS)} months, {len(trade_rows)} trade rows, {len(mig_rows)} OD rows")


if __name__ == "__main__":
    main()
