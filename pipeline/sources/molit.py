"""국토교통부 아파트 실거래가 (공공데이터포털) 수집기.

  매매  : /1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade
  전월세: /1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent
  파라미터: serviceKey, LAWD_CD(5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows

출력(중간 테이블, data/interim):
  trade_region_month.csv  ym,code,trades,value_eok,corp_trades,corp_value_eok,equity_eok
  rent_region_month.csv   ym,code,contracts,new_contracts,jeonse,wolse,deposit_eok

주의:
  - 계약 해제 건(cdealType == 'O')은 제외한다. 안 빼면 취소된 신고가가 '자금 유입'으로 잡힌다.
  - 전월세 갱신 계약은 이사가 없으므로 new_contracts(contractType == '신규')를 흐름에 쓴다.
  - 부천시 2024년 구 재설치 코드(41192/41194/41196)는 41190으로 합친다.
  - 신고기한이 계약 후 30일이라 최근 몇 달은 계속 바뀐다. refresh_recent 개월은 캐시를 무시하고 다시 받는다.
  - 개발 계정 일일 호출 한도(API별 1만 건)를 아끼려고 응답 XML을 data/raw/molit 에 캐시한다.
  - 출력 CSV는 덮어쓰지 않고 (ym, code) 단위로 병합한다. 최근 몇 달만 받아도 과거 이력이 남는다.
"""
import csv, os, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from leverage import equity  # noqa: E402

BASE = "https://apis.data.go.kr/1613000"
TRADE = "RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
RENT = "RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
CODE_ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190"}
QUERY_CODES = {"41190": ["41192", "41194", "41196", "41190"]}
PAGE = 1000

TRADE_COLS = ["ym", "code", "trades", "value_eok", "corp_trades", "corp_value_eok", "equity_eok"]
RENT_COLS = ["ym", "code", "contracts", "new_contracts", "jeonse", "wolse", "deposit_eok"]


def http_get(url, retries=4):
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception:  # 공공 API는 간헐적으로 502/timeout이 잦다
            if k == retries - 1:
                raise
            time.sleep(2 ** (k + 1))


def parse(xml_bytes):
    """응답 XML → (items, totalCount). 오류 코드면 예외."""
    root = ET.fromstring(xml_bytes)
    code = root.findtext(".//resultCode")
    if code not in ("00", "000"):
        raise RuntimeError(f"API error {code}: {root.findtext('.//resultMsg')}")
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.findall(".//item")]
    return items, int(root.findtext(".//totalCount") or 0)


class Client:
    def __init__(self, key, cache_dir=None, fresh_months=(), getter=http_get):
        self.key, self.cache_dir, self.fresh, self.get = key, cache_dir, set(fresh_months), getter
        self.calls = 0

    def _page(self, path, lawd, ymd, page):
        cache = None
        if self.cache_dir:
            cache = os.path.join(self.cache_dir, path.split("/")[0], f"{lawd}_{ymd}_{page}.xml")
            if os.path.exists(cache) and f"{ymd[:4]}-{ymd[4:]}" not in self.fresh:
                with open(cache, "rb") as f:
                    return f.read()
        q = urllib.parse.urlencode({"serviceKey": self.key, "LAWD_CD": lawd, "DEAL_YMD": ymd,
                                    "pageNo": page, "numOfRows": PAGE})
        body = self.get(f"{BASE}/{path}?{q}")
        self.calls += 1
        parse(body)  # 오류 응답은 캐시하지 않는다
        if cache:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            tmp = cache + ".part"  # 중단돼도 반쯤 쓴 파일이 캐시로 남지 않게
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, cache)
        return body

    def items(self, path, lawd, ym):
        ymd, page = ym.replace("-", ""), 1
        while True:
            items, total = parse(self._page(path, lawd, ymd, page))
            yield from items
            if page * PAGE >= total:
                return
            page += 1


def num(s):
    return float(s.replace(",", "")) if s else 0.0


def add_trade(acc, ym, code, it):
    if it.get("cdealType") == "O":
        return
    price = num(it.get("dealAmount")) / 1e4  # 만원 → 억
    t = acc[(ym, code)]
    t[0] += 1
    t[1] += price
    if it.get("buyerGbn") == "법인":
        t[2] += 1
        t[3] += price
    t[4] += equity(price, code, ym)


def add_rent(acc, ym, code, it):
    r = acc[(ym, code)]
    monthly = num(it.get("monthlyRent"))
    r[0] += 1
    r[1] += it.get("contractType") == "신규"
    r[2] += monthly == 0
    r[3] += monthly > 0
    r[4] += num(it.get("deposit")) / 1e4


def merge_write(path, header, new_rows):
    """기존 CSV와 (ym, code) 키로 병합해 저장한다."""
    rows = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["ym"], r["code"])] = [r[h] for h in header]
    for r in new_rows:
        rows[(r[0], r[1])] = r
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header)
        w.writerows(rows[k] for k in sorted(rows))


def fetch(client, codes, months, out_dir):
    trade, rent = defaultdict(lambda: [0, 0.0, 0, 0.0, 0.0]), defaultdict(lambda: [0, 0, 0, 0, 0.0])
    for code in codes:
        for ym in months:
            for q in QUERY_CODES.get(code, [code]):
                c = CODE_ALIAS.get(q, q)
                for it in client.items(TRADE, q, ym):
                    add_trade(trade, ym, c, it)
                for it in client.items(RENT, q, ym):
                    add_rent(rent, ym, c, it)
            print(f"  {code} {ym} (API 호출 누적 {client.calls})", file=sys.stderr)
    os.makedirs(out_dir, exist_ok=True)
    merge_write(os.path.join(out_dir, "trade_region_month.csv"), TRADE_COLS,
                [[ym, c, t[0], round(t[1], 2), t[2], round(t[3], 2), round(t[4], 2)] for (ym, c), t in trade.items()])
    merge_write(os.path.join(out_dir, "rent_region_month.csv"), RENT_COLS,
                [[ym, c, *r[:4], round(r[4], 2)] for (ym, c), r in rent.items()])
    return client.calls
