"""겹쳐 받을 기간(RefreshPolicy의 hot/warm 경계)을 측정으로 정하기 위한 분석.

법은 '해제 확정일부터 30일 내 신고'만 정해서 계약일 기준 상한이 없다. 그래서 고정값을 믿지 않고
우리가 받아 둔 자료로 잰다.

1) 해제 시차: 캐시된 매매 응답에서 해제 건(cdealType=O)의 계약일 → 해제일(cdealDay) 간격.
   해제 신고는 해제일로부터 최대 30일 늦게 들어오므로 '데이터가 바뀌는 시점' = 간격 + 30일.
2) 변경 기록: molit_changes.csv 에서 계약월 나이별로 다시 받았을 때 실제로 바뀐 비율.

    python3 pipeline/cancel_lag.py            # data/raw 기준
"""
import csv, glob, math, os, sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from sources.molit import parse  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
REPORT_DAYS = 30  # 해제 확정일부터 신고 기한


def parse_day(s):
    """cdealDay 형식이 'YY.MM.DD', 'YYYYMMDD', 'YYYY-MM-DD' 중 무엇이든 읽는다."""
    s = (s or "").strip()
    try:
        if "." in s:
            y, m, d = s.split(".")
            y = int(y) + (2000 if len(y) == 2 else 0)
            return date(y, int(m), int(d))
        s = s.replace("-", "")
        return date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) == 8 else None
    except ValueError:
        return None


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(math.ceil(p * len(sorted_vals))) - 1)] if sorted_vals else None


def cancel_lags(cache_dir):
    lags = []
    for path in glob.glob(os.path.join(cache_dir, "RTMSDataSvcAptTrade", "*.xml")):
        with open(path, "rb") as f:
            items, _ = parse(f.read())
        for it in items:
            if it.get("cdealType") != "O":
                continue
            end = parse_day(it.get("cdealDay"))
            try:
                start = date(int(it["dealYear"]), int(it["dealMonth"]), int(it["dealDay"]))
            except (KeyError, ValueError):
                continue
            if end and end >= start:
                lags.append((end - start).days)
    return sorted(lags)


def change_summary(log_path):
    by_age = defaultdict(lambda: [0, 0, 0])  # 다시 받은 횟수, 무언가 바뀐 횟수, 해제 건수
    if not os.path.exists(log_path):
        return {}
    with open(log_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["age_months"]:
                continue
            a = by_age[int(r["age_months"])]
            changed = sum(int(r[k]) for k in ("added", "removed", "cancelled", "price_changed", "other_changed"))
            a[0] += 1; a[1] += changed > 0; a[2] += int(r["cancelled"])
    return dict(sorted(by_age.items()))


def recommend(lags, coverage=0.99):
    """해제의 coverage 비율을 덮으려면 계약월로부터 몇 개월까지 다시 받아야 하나."""
    p = pct(lags, coverage)
    return None if p is None else math.ceil((p + REPORT_DAYS) / 30.44)


def main():
    raw = os.path.join(ROOT, "data", "raw")
    lags = cancel_lags(os.path.join(raw, "molit"))
    if lags:
        print(f"해제 {len(lags):,}건의 계약일→해제일 간격(일): 중앙값 {pct(lags, .5)}, 90% {pct(lags, .9)}, "
              f"95% {pct(lags, .95)}, 99% {pct(lags, .99)}, 최대 {lags[-1]}")
        print(f"→ 해제의 95%를 잡으려면 계약월로부터 {recommend(lags, .95)}개월, 99%는 {recommend(lags, .99)}개월까지 다시 받아야 합니다.")
        print("   RefreshPolicy(warm=...) 값을 이 결과에 맞추세요.")
    else:
        print("캐시에 해제 건이 없습니다. 실데이터를 먼저 받으세요.")
    summary = change_summary(os.path.join(raw, "molit_changes.csv"))
    if summary:
        print("\n계약월 나이별 재수집 결과 (나이: 다시 받은 횟수, 바뀐 비율, 해제 건수)")
        for age, (n, changed, cancelled) in summary.items():
            print(f"  {age:>3}개월: {n:>4}회, {changed / n:6.1%}, 해제 {cancelled}건")


if __name__ == "__main__":
    main()
