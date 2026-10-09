"""지역별 상세 파일(data/detail/{code}.json) 생성.

지도에서 지역을 누를 때만 그 지역 파일을 받는다. 77개 지역의 단지별·월별 거래를 첫 화면 HTML에
모두 넣으면 수 MB가 되어 첫 화면이 느려지기 때문이다.

파일 구조 (월 인덱스 mi는 flows.json meta.months 기준)
  complexes: [[이름, 법정동, 준공연도], ...]          단지 번호 = 배열 위치
  trade:     [[단지, mi, 건수, 거래대금(억), 전용면적합(㎡)], ...]
  rent:      [[단지, mi, 계약수, 신규, 보증금합(억), 월세건수], ...]
  income:    연도별 1인당 평균 총급여(만원), 근로자 수, 수도권 77곳 중 순위, level(sgg|city)
  card:      basis(resident|merchant|local_currency), 월별 금액(억). 순위는 같은 basis끼리만.
"""
import csv, json, os, sys
from pathlib import Path
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..")
INTERIM = os.path.join(ROOT, "data", "interim")
OUT = os.path.join(ROOT, "data", "detail")


def read(name):
    path = os.path.join(INTERIM, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build(months, regions):
    mi = {m: i for i, m in enumerate(months)}
    codes = [r["code"] for r in regions]
    cx = defaultdict(dict)  # code → cid → local idx
    meta = defaultdict(list)
    for r in read("complexes.csv"):
        cx[r["code"]][r["cid"]] = len(meta[r["code"]])
        meta[r["code"]].append([r["name"], r["umd"], int(r["build_year"]) if r["build_year"].isdigit() else None])

    def local(code, cid, name=""):
        # 단지 정보 파일에 없는 단지가 거래에만 나오면 이름 없이라도 추가한다(조용히 버리지 않는다)
        if cid not in cx[code]:
            cx[code][cid] = len(meta[code]); meta[code].append([name or cid, "", None])
        return cx[code][cid]

    trade, rent = defaultdict(list), defaultdict(list)
    for r in read("complex_trade_month.csv"):
        if r["ym"] in mi:
            trade[r["code"]].append([local(r["code"], r["cid"]), mi[r["ym"]], int(r["trades"]),
                                     round(float(r["value_eok"]), 1), round(float(r["area_m2"]))])
    for r in read("complex_rent_month.csv"):
        if r["ym"] in mi:
            rent[r["code"]].append([local(r["code"], r["cid"]), mi[r["ym"]], int(r["contracts"]), int(r["new_contracts"]),
                                    round(float(r["deposit_eok"]), 1), int(r["wolse"])])

    income = defaultdict(lambda: {"years": [], "avg": [], "earners": [], "rank": [], "level": "sgg"})
    by_year = defaultdict(list)
    for r in read("income_year.csv"):
        by_year[int(r["year"])].append(r)
    for y in sorted(by_year):
        ranked = sorted(by_year[y], key=lambda r: -int(r["avg_pay_manwon"]))
        for k, r in enumerate(ranked):
            d = income[r["code"]]
            d["years"].append(y); d["avg"].append(int(r["avg_pay_manwon"])); d["earners"].append(int(r["earners"]))
            d["rank"].append(k + 1); d["of"] = len(ranked); d["level"] = r["level"]

    card = defaultdict(lambda: {"basis": None, "mi": [], "amount": []})
    for r in read("card_month.csv"):
        if r["ym"] in mi:
            d = card[r["code"]]
            d["basis"] = r["basis"]; d["mi"].append(mi[r["ym"]]); d["amount"].append(float(r["amount_eok"]))

    os.makedirs(OUT, exist_ok=True)
    sizes = []
    for reg in regions:
        c = reg["code"]
        doc = {"code": c, "name": reg["name"], "complexes": meta[c], "trade": trade[c], "rent": rent[c],
               "income": income[c] if c in income else None, "card": card[c] if c in card else None}
        path = os.path.join(OUT, f"{c}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        sizes.append(os.path.getsize(path))
    return sizes


def main():
    flows = json.loads(Path(os.path.join(ROOT, "data", "flows.json")).read_text(encoding="utf-8"))
    regions = [n for n in flows["nodes"] if n["code"] != "00000"]
    sizes = build(flows["meta"]["months"], regions)
    print(f"wrote {len(sizes)} detail files to {OUT}: total {sum(sizes) / 1e6:.2f} MB, max {max(sizes) / 1e3:.0f} KB")


if __name__ == "__main__":
    main()
