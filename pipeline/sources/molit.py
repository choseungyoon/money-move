"""국토교통부 아파트 실거래가 (공공데이터포털) 수집기.

  매매  : /1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade
  전월세: /1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent
  파라미터: serviceKey, LAWD_CD(5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows

출력(중간 테이블, data/interim):
  trade_region_month.csv  ym,code,trades,value_eok
  rent_region_month.csv   ym,code,contracts,new_contracts,jeonse,wolse,deposit_eok

주의:
  - 계약 해제 건(cdealType == 'O')은 제외한다. 안 빼면 취소된 신고가가 '자금 유입'으로 잡힌다.
  - 전월세 갱신 계약은 이사가 없으므로 new_contracts(contractType == '신규')를 흐름에 쓴다.
  - 부천시 2024년 구 재설치 코드(41192/41194/41196)는 41190으로 합친다.
  - 신고기한이 계약 후 30일이라 최근 1~2개월은 과소집계된다. build_flows에서 'provisional' 표시.
"""
import csv, os, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

BASE = "https://apis.data.go.kr/1613000"
CODE_ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}
QUERY_CODES = {"41190": ["41192", "41194", "41196", "41190"]}


def _get(path, params, retries=4):
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params, safe="%")
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return ET.fromstring(r.read())
        except Exception as e:  # 공공 API는 간헐적으로 502/timeout이 잦다
            if k == retries - 1:
                raise
            time.sleep(2 ** (k + 1))


def _items(path, key, lawd, ym):
    page = 1
    while True:
        root = _get(path, {"serviceKey": key, "LAWD_CD": lawd, "DEAL_YMD": ym, "pageNo": page, "numOfRows": 1000})
        code = root.findtext(".//resultCode")
        if code not in ("00", "000"):
            raise RuntimeError(f"{path} {lawd} {ym}: {code} {root.findtext('.//resultMsg')}")
        items = root.findall(".//item")
        for it in items:
            yield {c.tag: (c.text or "").strip() for c in it}
        total = int(root.findtext(".//totalCount") or 0)
        if page * 1000 >= total:
            return
        page += 1


def _num(s):
    return float(s.replace(",", "")) if s else 0.0


def fetch(key, codes, months, out_dir):
    trade, rent = defaultdict(lambda: [0, 0.0]), defaultdict(lambda: [0, 0, 0, 0, 0.0])
    for code in codes:
        for ym in months:
            ymd = ym.replace("-", "")
            for q in QUERY_CODES.get(code, [code]):
                for it in _items("RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade", key, q, ymd):
                    if it.get("cdealType") == "O":
                        continue
                    t = trade[(ym, CODE_ALIAS.get(q, q))]
                    t[0] += 1
                    t[1] += _num(it.get("dealAmount")) / 1e4  # 만원 → 억
                for it in _items("RTMSDataSvcAptRent/getRTMSDataSvcAptRent", key, q, ymd):
                    r = rent[(ym, CODE_ALIAS.get(q, q))]
                    is_new = it.get("contractType") == "신규"
                    monthly = _num(it.get("monthlyRent"))
                    r[0] += 1
                    r[1] += is_new
                    r[2] += monthly == 0
                    r[3] += monthly > 0
                    r[4] += _num(it.get("deposit")) / 1e4
            print(f"  {code} {ym}", file=sys.stderr)
    os.makedirs(out_dir, exist_ok=True)
    with open(f"{out_dir}/trade_region_month.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ym", "code", "trades", "value_eok"])
        for (ym, c), (n, v) in sorted(trade.items()):
            w.writerow([ym, c, n, round(v, 2)])
    with open(f"{out_dir}/rent_region_month.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["ym", "code", "contracts", "new_contracts", "jeonse", "wolse", "deposit_eok"])
        for (ym, c), r in sorted(rent.items()):
            w.writerow([ym, c, *r[:4], round(r[4], 2)])
