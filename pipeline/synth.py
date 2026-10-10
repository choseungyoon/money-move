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
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from regions import PRIORS, ANCHOR_OVERRIDE, NONCAP  # noqa: E402
from leverage import equity  # noqa: E402
from sources.kosis import SI_OF_GU  # noqa: E402

random.seed(20261008)
ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "interim")

MONTHS = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)][:32]  # 2024-01 ~ 2026-08
# 인구이동 공개 구조 재현: MDIS(어디서 어디로)는 연 단위로 2025년까지만, KOSIS 월별 총계는 2026-08까지.
MDIS_LAST = "2025-12"
KOSIS_LAST = "2026-08"

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
BALLOON = {"41310", "41597", "41570", "41281", "41285", "41287", "41360", "41190", "28185", "28260"}

# 대표적인 이주 회랑(철도·도로축). 거리감쇠만으로는 안 잡히는 방향성 보정.
CORRIDORS = [
    ({"11680", "11650", "11710"}, {"41135", "41465", "41450", "41290", "41131", "41463"}),
    ({"11440", "11380", "11410"}, {"41281", "41285", "41287", "41480", "41570"}),
    ({"11350", "11320", "11260", "11305"}, {"41360", "41150", "41310", "41630"}),
    ({"11500", "11470"}, {"41570", "41190", "28260", "28245"}),
    ({"11620", "11545", "11530", "11590"}, {"41210", "41171", "41173", "41190"}),
    ({"11740", "11710"}, {"41450", "41310", "41360"}),
    ({"11680", "11650"}, {"41597", "41117"}),
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
    geo = json.loads(Path(os.path.join(ROOT, "data", "regions_geo.json")).read_text(encoding="utf-8"))
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

    trade_rows, rent_rows, share_rows, mig_rows, truth_rows = [], [], [], [], []
    HOUSING_SHARE = {c: random.uniform(0.32, 0.45) for c in codes}
    HOUSING_SHARE[NONCAP["code"]] = 0.3
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
            avg = price * growth * noise(0.04)
            # 지역 안에서도 가격이 퍼져 있다(로그정규). 대출 한도가 가격 구간별 계단이라 건별로 계산해야 한다.
            sample = [avg * math.exp(random.gauss(-0.06, 0.35)) for _ in range(min(n, 400))]
            scale = n * avg / sum(sample)
            eq = sum(equity(x * scale / n * len(sample), c, ym) for x in sample) * n / len(sample)
            corp_n = sum(random.random() < (0.025 + 0.03 * (price < 4)) for _ in range(n))
            trade_rows.append([ym, c, n, round(n * avg, 2), corp_n, round(corp_n * avg * 0.8, 2), round(eq, 2)])

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

        # 인구이동: 모든 달의 '실제' 이동을 만들고, 공개 구조대로 MDIS(~2025-12)와 KOSIS 총계(~2026-08)로 나눈다.
        # 2026년 실제 이동은 숨겨 두었다가 추정치 채점에 쓴다(validate_estimate.py).
        mm = season * (0.75 + 0.25 * (MKT_SEOUL[t] + MKT_GG[t]) / 2)
        cells = []
        for (i, j), v in base_od.items():
            f = v * k * mm
            if j in BALLOON and i.startswith("11") and after(ym, "2025-11"):
                f *= 1.2
            if ym >= "2026-01" and i.startswith("11") and j.startswith("41"):
                f *= 1.08  # 2026년 구조 변화: 서울→경기 이동이 기준 패턴보다 늘어난다(추정이 쉽게 못 맞히게)
            n = round(f * noise(0.12))
            if n >= 3:
                cells.append((i, j, n))
        for c in codes:
            cells.append((c, c, round(pop[c] * 1e4 * 0.0024 * mm * noise())))
            cells.append((NONCAP["code"], c, round(pop[c] * 1e4 * 0.00022 * mm * noise() * (1.3 if c.startswith("11") else 1))))
            cells.append((c, NONCAP["code"], round(pop[c] * 1e4 * 0.0002 * mm * noise())))
        for i, j, n in cells:
            # 전체 사유 이동 = 주택 사유 / 주택 사유 비율(도착지별 32~45%, 같은 구 안 이동은 더 높다)
            share = HOUSING_SHARE[j] * (1.25 if i == j else 1.0)
            all_n = max(n, round(n / min(0.9, share) * noise(0.05)))
            if ym <= MDIS_LAST:
                mig_rows.append([ym, i, j, n, all_n, max(1, round(n / 1.9)), max(1, round(all_n / 1.7))])
            truth_rows.append([ym, i, j, n, all_n])

    write("trade_region_month.csv", ["ym", "code", "trades", "value_eok", "corp_trades", "corp_value_eok", "equity_eok"], trade_rows)
    write("rent_region_month.csv", ["ym", "code", "contracts", "new_contracts", "jeonse", "wolse", "deposit_eok"], rent_rows)
    write("buyer_origin_share.csv", ["ym", "code", "same_sgg", "same_sido", "seoul", "other"], share_rows)
    write("migration_od_month.csv", ["ym", "src", "dst", "persons", "persons_all", "households", "households_all"], mig_rows)
    # KOSIS 시군구별 월 총계(총전입·총전출·시군구 내 이동)는 실제 이동에서 집계: 비수도권 노드는 KOSIS에 없음
    # 실제 KOSIS처럼 경기 일반구는 시로 묶는다(구 사이 이동은 시군구 내로 잡힌다)
    mg = defaultdict(lambda: [0, 0, 0])
    for ym, i, j, n, all_n in truth_rows:
        if ym > KOSIS_LAST:
            continue
        i, j = SI_OF_GU.get(i, i), SI_OF_GU.get(j, j)
        if j != NONCAP["code"]:
            mg[(ym, j)][0] += all_n
        if i != NONCAP["code"]:
            mg[(ym, i)][1] += all_n
        if i == j:
            mg[(ym, i)][2] += all_n
    write("kosis_sgg_month.csv", ["ym", "code", "in_total", "out_total", "intra"], [[ym, c, *v] for (ym, c), v in sorted(mg.items())])
    write("demo_truth_od.csv", ["ym", "src", "dst", "persons", "persons_all"], [r for r in truth_rows if r[0] > MDIS_LAST])
    print(f"demo interim: {len(MONTHS)} months, {len(trade_rows)} trade rows, {len(mig_rows)} OD rows")
    details(trade_rows, rent_rows)


def details(trade_rows, rent_rows):
    """단지별 거래·소득·카드 데모 데이터.

    단지 이름은 '강남 예시07단지'처럼 가짜임이 드러나게 짓는다. 실제 단지명에 지어낸 거래를 붙이면
    데모라고 표시해도 특정 단지의 거래 기록을 만들어낸 것이 된다.
    단지별 건수·금액 합계는 지역 합계와 정확히 같게 배분한다(실데이터도 같은 거래를 집계하므로 같아야 한다).
    """
    geo = json.loads(Path(os.path.join(ROOT, "data", "regions_geo.json")).read_text(encoding="utf-8"))
    short = {r["code"]: r["short"] for r in geo["regions"]}
    cx = {}  # code → [(cid, weight, price_factor, deposit_factor, area)]
    meta_rows = []
    for c, (pop, price, dep) in sorted(PRIORS.items()):
        k = max(18, min(120, int(pop * 1.6)))
        lst = []
        for n in range(1, k + 1):
            year = random.randint(1986, 2024)
            newness = (year - 1986) / 38
            pf = math.exp(random.gauss(0, 0.28)) * (0.8 + 0.45 * newness)
            lst.append((f"D{n:03d}", math.exp(random.gauss(0, 0.8)), pf, pf ** 0.8 * math.exp(random.gauss(0, 0.1)),
                        random.choice([59, 74, 84, 84, 101, 114])))
            meta_rows.append([c, f"D{n:03d}", f"{short[c]} 예시{n:02d}단지", "", year])
        cx[c] = lst

    def allocate(n, weights):
        counts = [0] * len(weights)
        for i in random.choices(range(len(weights)), weights=weights, k=n):
            counts[i] += 1
        return counts

    ct, cr = [], []
    for ym, c, n, value, *_ in trade_rows:
        lst = cx[c]
        counts = allocate(n, [w for _, w, *_ in lst])
        raw = [cnt * pf for cnt, (_, _, pf, _, _) in zip(counts, lst)]
        scale = value / (sum(raw) or 1)
        for cnt, r, (cid, *_rest) in zip(counts, raw, lst):
            if cnt:
                ct.append([ym, c, cid, cnt, round(r * scale, 2), cnt * _rest[3]])
    for ym, c, contracts, new, jeon, wolse, deposit in rent_rows:
        lst = cx[c]
        counts = allocate(contracts, [w for _, w, *_ in lst])
        raw = [cnt * df for cnt, (_, _, _, df, _) in zip(counts, lst)]
        scale = deposit / (sum(raw) or 1)
        new_share, wolse_share = new / contracts, wolse / contracts
        for cnt, r, (cid, *_rest) in zip(counts, raw, lst):
            if cnt:
                cr.append([ym, c, cid, cnt, round(cnt * new_share), round(r * scale, 2), round(cnt * wolse_share)])
    write("complex_trade_month.csv", ["ym", "code", "cid", "trades", "value_eok", "area_m2"], ct)
    write("complex_rent_month.csv", ["ym", "code", "cid", "contracts", "new_contracts", "deposit_eok", "wolse"], cr)
    write("complexes.csv", ["code", "cid", "name", "umd", "build_year"], meta_rows)

    # 소득: 국세청 연말정산은 'N년 귀속'이 N+1년 12월 공개 → 2026-10 기준 최신은 2024년 귀속
    inc = []
    for c, (pop, price, _) in PRIORS.items():
        base = 2000 + 1150 * math.sqrt(price)
        for y in (2021, 2022, 2023, 2024):
            avg = base * 1.03 ** (y - 2024) * random.gauss(1, 0.02)
            earners = pop * 1e4 * 0.47 * random.gauss(1, 0.02)
            inc.append([y, c, round(earners), round(avg * earners / 1e4, 1), round(avg), "sgg"])
    write("income_year.csv", ["year", "code", "earners", "total_pay_eok", "avg_pay_manwon", "level"], inc)

    # 카드: 공개 시차 2개월 재현(2026-06까지). 시도마다 기준이 다르다(sources/card.py 참고).
    card = []
    basis = {"11": "resident", "41": "merchant", "28": "local_currency"}
    for c, (pop, price, _) in PRIORS.items():
        per_cap = 60 + 8 * math.sqrt(price)  # 만원/월/인. 아이·노인 포함 1인당이라 100만원 안팎
        b = basis[c[:2]]
        scale = 1.0 if b == "resident" else (1.15 if b == "merchant" else 0.07)  # 지역화폐는 전체 소비의 일부
        for ym in MONTHS:
            if ym > "2026-06":
                continue
            m = int(ym[5:])
            season = 1.12 if m in (12, 1) else 0.94 if m in (2, 7) else 1.0
            amt = pop * per_cap * scale * season * random.gauss(1, 0.03)
            card.append([ym, c, round(amt, 1), round(amt * 1e4 / 4.5), b])
    write("card_month.csv", ["ym", "code", "amount_eok", "count", "basis"], card)
    print(f"demo details: {len(ct)} complex-trade rows, {len(cr)} complex-rent rows, {len(meta_rows)} complexes")


if __name__ == "__main__":
    main()
