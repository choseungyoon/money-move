"""국토교통부 아파트 실거래가 (공공데이터포털) 수집기.

  매매  : /1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade
  전월세: /1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent
  파라미터: serviceKey, LAWD_CD(5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows

출력(중간 테이블, data/interim):
  trade_region_month.csv  ym,code,trades,value_eok,corp_trades,corp_value_eok,equity_eok
  rent_region_month.csv   ym,code,contracts,new_contracts,jeonse,wolse,deposit_eok
  complex_trade_month.csv ym,code,cid,trades,value_eok,area_m2       (단지별 매매)
  complex_rent_month.csv  ym,code,cid,contracts,new_contracts,deposit_eok,wolse  (단지별 전월세)
  complexes.csv           code,cid,name,umd,build_year               (단지 정보)

주의:
  - 계약 해제 건(cdealType == 'O')은 제외한다. 안 빼면 취소된 신고가가 '자금 유입'으로 잡힌다.
  - 전월세 갱신 계약은 이사가 없으므로 new_contracts(contractType == '신규')를 흐름에 쓴다.
  - 부천시 2024년 구 재설치 코드(41192/41194/41196)는 41190으로 합친다.
  - 신고기한이 계약 후 30일이라 최근 몇 달은 계속 바뀐다. refresh_recent 개월은 캐시를 무시하고 다시 받는다.
  - 개발 계정 일일 호출 한도(API별 1만 건)를 아끼려고 응답 XML을 data/raw/molit 에 캐시한다.
  - 출력 CSV는 (ym, code) 묶음 단위로 교체한다. 다시 받은 묶음의 기존 행은 모두 지우고 새로 쓴다.
    행 단위로 덮어쓰면, 다시 받았을 때 사라진 단지(계약 해제 등)의 옛 행이 남아 순위에 계속 나온다.
  - 단지 식별: 응답에 aptSeq(단지 일련번호)가 있으면 쓰고, 없으면 시군구+법정동+지번+단지명 조합.
    단지명만으로는 고유하지 않다(다른 동의 동명 단지, 띄어쓰기·명칭 변경).
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
CTRADE_COLS = ["ym", "code", "cid", "trades", "value_eok", "area_m2"]
CRENT_COLS = ["ym", "code", "cid", "contracts", "new_contracts", "deposit_eok", "wolse"]
COMPLEX_COLS = ["code", "cid", "name", "umd", "build_year"]


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


def complex_id(code, it):
    if it.get("aptSeq"):
        return it["aptSeq"]
    name = "".join((it.get("aptNm") or "").split())
    return f"{code}|{it.get('umdNm', '')}|{it.get('jibun', '')}|{name}"


def add_complex(meta, ctab, rows, ym, code, it):
    """단지 정보와 단지별 월 집계에 거래 1건을 더한다. rows는 더할 값 목록."""
    cid = complex_id(code, it)
    meta.setdefault((code, cid), [(it.get("aptNm") or "").strip(), it.get("umdNm", ""), it.get("buildYear", "")])
    acc = ctab[(ym, code, cid)]
    for k, v in enumerate(rows):
        acc[k] += v


def add_trade(acc, ym, code, it, meta=None, ctab=None):
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
    if ctab is not None:
        add_complex(meta, ctab, [1, price, num(it.get("excluUseAr"))], ym, code, it)


def add_rent(acc, ym, code, it, meta=None, ctab=None):
    r = acc[(ym, code)]
    monthly = num(it.get("monthlyRent"))
    if ctab is not None:
        add_complex(meta, ctab, [1, it.get("contractType") == "신규", num(it.get("deposit")) / 1e4, monthly > 0], ym, code, it)
    r[0] += 1
    r[1] += it.get("contractType") == "신규"
    r[2] += monthly == 0
    r[3] += monthly > 0
    r[4] += num(it.get("deposit")) / 1e4


def merge_write(path, header, new_rows, fetched, part=("ym", "code")):
    """기존 CSV에서 이번에 다시 받은 묶음(fetched: {(ym, code)})의 행을 모두 지우고 새 행을 더한다."""
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rows = [[r[h] for h in header] for r in csv.DictReader(f) if tuple(r[k] for k in part) not in fetched]
    rows += [[str(x) for x in r] for r in new_rows]
    idx = [header.index(k) for k in header if k != header[-1]]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header)
        w.writerows(sorted(rows, key=lambda r: [r[i] for i in idx]))


def fetch(client, codes, months, out_dir):
    trade, rent = defaultdict(lambda: [0, 0.0, 0, 0.0, 0.0]), defaultdict(lambda: [0, 0, 0, 0, 0.0])
    ctrade, crent, meta = defaultdict(lambda: [0, 0.0, 0.0]), defaultdict(lambda: [0, 0, 0.0, 0]), {}
    fetched = {(ym, c) for c in codes for ym in months}
    for code in codes:
        for ym in months:
            for q in QUERY_CODES.get(code, [code]):
                c = CODE_ALIAS.get(q, q)
                for it in client.items(TRADE, q, ym):
                    add_trade(trade, ym, c, it, meta, ctrade)
                for it in client.items(RENT, q, ym):
                    add_rent(rent, ym, c, it, meta, crent)
            print(f"  {code} {ym} (API 호출 누적 {client.calls})", file=sys.stderr)
    os.makedirs(out_dir, exist_ok=True)
    merge_write(os.path.join(out_dir, "trade_region_month.csv"), TRADE_COLS,
                [[ym, c, t[0], round(t[1], 2), t[2], round(t[3], 2), round(t[4], 2)] for (ym, c), t in trade.items()], fetched)
    merge_write(os.path.join(out_dir, "rent_region_month.csv"), RENT_COLS,
                [[ym, c, *r[:4], round(r[4], 2)] for (ym, c), r in rent.items()], fetched)
    merge_write(os.path.join(out_dir, "complex_trade_month.csv"), CTRADE_COLS,
                [[ym, c, cid, n, round(v, 2), round(a, 1)] for (ym, c, cid), (n, v, a) in ctrade.items()], fetched)
    merge_write(os.path.join(out_dir, "complex_rent_month.csv"), CRENT_COLS,
                [[ym, c, cid, n, new, round(d, 2), w] for (ym, c, cid), (n, new, d, w) in crent.items()], fetched)
    # 단지 정보는 누적한다(이전에 받은 단지도 유지). 새로 본 이름이 있으면 갱신.
    path = os.path.join(out_dir, "complexes.csv"); known = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            known = {(r["code"], r["cid"]): [r["name"], r["umd"], r["build_year"]] for r in csv.DictReader(f)}
    known.update(meta)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(COMPLEX_COLS)
        w.writerows([c, cid, *v] for (c, cid), v in sorted(known.items()))
    return client.calls
