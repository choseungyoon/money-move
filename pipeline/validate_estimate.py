"""데모 전용: 숨겨 둔 2026년 '실제' 이동과 추정치를 비교해 추정 방법을 채점한다.

비교 대상
  A 최신 공개월 대체: MDIS 마지막 달 값을 그대로 쓴다(이전 화면 방식)
  B 최근 12개월 평균: 총계 조정 없음
  C IPF 추정: KOSIS 월별 총계에 맞춰 조정(estimate_od.py)
지표: WAPE = Σ|추정-실제| / Σ실제 (주택 사유 사람 수). 지도에 그리는 '시군구 간' 흐름과 상위 50개 흐름 기준.
실데이터에는 '실제 값'이 없으므로 이 채점은 데모 가정 안에서만 의미가 있다.
"""
import csv, os
from collections import defaultdict

INTERIM = os.path.join(os.path.dirname(__file__), "..", "data", "interim")


def load(name, key="persons"):
    out = defaultdict(dict)
    with open(os.path.join(INTERIM, name), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["ym"]][(r["src"], r["dst"])] = float(r[key])
    return out


def wape(est, true, pairs):
    num = sum(abs(est.get(p, 0) - true.get(p, 0)) for p in pairs)
    den = sum(true.get(p, 0) for p in pairs)
    return num / den if den else float("nan")


def main():
    truth, ipf, mdis = load("demo_truth_od.csv"), load("migration_od_est.csv"), load("migration_od_month.csv")
    last = max(mdis)
    base = sorted(mdis)[-12:]
    avg = defaultdict(float)
    for m in base:
        for k, v in mdis[m].items():
            avg[k] += v / len(base)
    res = {"A 최신 공개월 대체": [], "B 최근 12개월 평균": [], "C IPF 추정": []}
    top = {"A 최신 공개월 대체": [], "B 최근 12개월 평균": [], "C IPF 추정": []}
    for ym in sorted(truth):
        t = truth[ym]
        inter = [p for p in t if p[0] != p[1] and "00000" not in p]
        top50 = sorted(inter, key=lambda p: -t[p])[:50]
        for name, est in (("A 최신 공개월 대체", mdis[last]), ("B 최근 12개월 평균", avg), ("C IPF 추정", ipf.get(ym, {}))):
            res[name].append(wape(est, t, inter)); top[name].append(wape(est, t, top50))
    print(f"{len(truth)}개월 평균 오차(WAPE, 낮을수록 좋음)  [시군구 간 전체 | 상위 50개 흐름]")
    for name in res:
        print(f"  {name:<14} {sum(res[name]) / len(res[name]):6.1%} | {sum(top[name]) / len(top[name]):6.1%}")


if __name__ == "__main__":
    main()
