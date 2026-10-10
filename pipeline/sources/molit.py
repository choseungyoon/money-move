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
  - 새 구 코드는 우리 80개 지역으로 합친다(region_of). 근거는 행정안전부 법정동코드(jscode20260701, 말소코드 포함).
      부천 2024 구 재설치 41192/41194/41196 → 41190
      화성 2026-02-01 구 신설 41591 만세·41593 효행·41595 병점·41597 동탄 → 합치지 않고 구별 지역으로 둔다(2026-10).
        API 가 과거 거래까지 새 구 코드로 다시 매기므로 2023년부터 구별 자료가 있다.
      인천 2026-07-01 개편(28110 중구·28140 동구·28260 서구 말소)
        28155 영종구 → 28110 (법정동 8개 모두 옛 중구), 28275 서해구·28290 검단구 → 28260 (모두 옛 서구)
        28125 제물포구 = 옛 중구 내륙 44개 동 + 옛 동구 7개 동 → 법정동 이름(umdNm)으로 나눈다(SPLIT).
        옛 중구·동구 사이에 같은 이름의 법정동이 없어 깔끔하게 나뉜다. 모르는 이름이 오면 에러.
  - 응답 XML을 data/raw/molit 에 캐시하고, 계약월의 나이에 따라 다시 받는 주기를 달리한다(RefreshPolicy).
    공공데이터포털 API는 무료지만 일일 호출 한도가 있고, 전체 재수집은 시간이 오래 걸린다.
  - 다시 받을 때마다 이전 응답과 비교한 변경 건수를 molit_changes.csv 에 남긴다(cancel_lag.py가 분석).
  - 출력 CSV는 (ym, code) 묶음 단위로 교체한다. 다시 받은 묶음의 기존 행은 모두 지우고 새로 쓴다.
    행 단위로 덮어쓰면, 다시 받았을 때 사라진 단지(계약 해제 등)의 옛 행이 남아 순위에 계속 나온다.
  - 단지 식별: 시군구+법정동+지번+단지명(띄어쓰기 무시) 조합. 단지명만으로는 고유하지 않다(다른 동의 동명 단지).
    aptSeq(단지 일련번호)는 전월세 응답에만 있고 매매 응답에는 없다(2026-10 molit-probe 확인).
    aptSeq를 쓰면 같은 단지가 매매·전월세에서 서로 다른 단지가 되므로 두 API 모두 조합 키를 쓴다.
"""
import csv, os, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from leverage import equity  # noqa: E402

BASE = "https://apis.data.go.kr/1613000"
TRADE = "RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
RENT = "RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
CODE_ALIAS = {"41192": "41190", "41194": "41190", "41196": "41190",
              "28155": "28110", "28275": "28260", "28290": "28260"}
_DONGGU = {"만석동", "화수동", "송현동", "화평동", "창영동", "금곡동", "송림동"}
_JUNGGU = {"경동", "내동", "답동", "도원동", "사동", "선린동", "선화동", "신생동", "신포동", "용동", "유동", "율목동",
           "인현동", "전동", *(f"관동{k}가" for k in range(1, 4)), *(f"북성동{k}가" for k in range(1, 4)),
           *(f"송월동{k}가" for k in range(1, 4)), *(f"송학동{k}가" for k in range(1, 4)),
           *(f"신흥동{k}가" for k in range(1, 4)), *(f"중앙동{k}가" for k in range(1, 5)),
           *(f"항동{k}가" for k in range(1, 8)), *(f"해안동{k}가" for k in range(1, 5))}
SPLIT = {"28125": {"28110": _JUNGGU, "28140": _DONGGU}}  # 제물포구 법정동 → 옛 구
# 우리 지역 → 조회할 LAWD_CD. API는 개편 전 달(2024-01 포함)도 새 코드로만 주고 옛 코드는 0건이다
# (molit-probe 2026-10: 41590, 28110, 28140, 28260 모두 0건, 옛·새 겹침 없음). 그래서 새 코드만 조회한다.
QUERY_CODES = {"41190": ["41192", "41194", "41196", "41190"],
               "28110": ["28155", "28125"], "28140": ["28125"], "28260": ["28275", "28290"]}
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


class RefreshPolicy:
    """계약월의 '나이'(오늘 기준 몇 개월 전 계약인가)에 따라 캐시를 얼마나 믿을지 정한다.

    실거래 자료는 한 번 받으면 끝이 아니다. 신고기한(계약 후 30일) 동안 건이 늘고, 해제·정정이 나중에
    반영되며, 등기일이 뒤늦게 붙는다. 해제 신고는 '해제 확정일부터 30일'이라 계약일 기준 상한이 없다.
      뜨거운 구간 (0~hot개월)  : hot_ttl_days(1일) 지나면 다시 받는다. 같은 날 재실행은 캐시 사용.
      따뜻한 구간 (~warm개월)  : warm_ttl_days(30일)마다.
      차가운 구간 (그 이후)    : cold_ttl_days(180일)마다. 드문 늦은 해제·정정을 잡는다.
    구간 경계는 추측값이다. changes.csv(변경 기록)와 cancel_lag.py(해제 시차 분석)로 측정해 조정한다.
    """

    def __init__(self, now=None, hot=3, warm=12, hot_ttl_days=1, warm_ttl_days=30, cold_ttl_days=180):
        self.now = now or time.time()
        self.hot, self.warm = hot, warm
        self.ttl = (hot_ttl_days, warm_ttl_days, cold_ttl_days)

    def age_months(self, ym):
        t = time.localtime(self.now)
        y, m = int(ym[:4]), int(ym[5:7])
        return (t.tm_year * 12 + t.tm_mon) - (y * 12 + m)

    def ttl_days(self, ym):
        age = self.age_months(ym)
        return self.ttl[0] if age <= self.hot else self.ttl[1] if age <= self.warm else self.ttl[2]

    def is_fresh(self, ym, fetched_at):
        return (self.now - fetched_at) < self.ttl_days(ym) * 86400


NEVER_EXPIRE = None  # policy=None 이면 캐시를 영구히 쓴다(테스트·재현용)

# 같은 거래인지 판단할 때 쓰지 않는 열: 나중에 바뀌는 값들
VOLATILE = {"cdealType", "cdealDay", "dealAmount", "rgstDate", "deposit", "monthlyRent", "dealingGbn",
            "estateAgentSggNm", "slerGbn", "buyerGbn", "contractType", "preDeposit", "preMonthlyRent", "useRRRight"}
PRICE = ("dealAmount", "deposit", "monthlyRent")


def diff_items(old, new):
    """같은 (API, 지역, 월)의 이전 응답과 새 응답 비교 → 건수 요약."""
    from collections import Counter
    ident = lambda it: tuple(sorted((k, v) for k, v in it.items() if k not in VOLATILE))
    groups_old, groups_new = defaultdict(list), defaultdict(list)
    for it in old:
        groups_old[ident(it)].append(it)
    for it in new:
        groups_new[ident(it)].append(it)
    out = Counter()
    for key in set(groups_old) | set(groups_new):
        a, b = groups_old.get(key, []), groups_new.get(key, [])
        out["removed"] += max(0, len(a) - len(b)); out["added"] += max(0, len(b) - len(a))
        for x, y in zip(a, b):
            if x.get("cdealType") != "O" and y.get("cdealType") == "O":
                out["cancelled"] += 1
            elif any(x.get(k) != y.get(k) for k in PRICE):
                out["price_changed"] += 1
            elif x != y:
                out["other_changed"] += 1  # 등기일 추가 등
    return out


CHANGE_COLS = ["fetched_at", "api", "lawd", "ym", "age_months", "old_n", "new_n",
               "added", "removed", "cancelled", "price_changed", "other_changed"]


class Client:
    """(API, 지역, 월) 묶음 단위로 캐시를 쓰거나 통째로 다시 받는다.

    페이지마다 따로 판단하면 새 1페이지와 옛 2페이지가 섞여 같은 달이 서로 다른 시점의 자료가 된다.
    다시 받을 때는 모든 페이지를 교체하고, 건수가 줄어 남는 옛 페이지 파일은 지운다.
    """

    def __init__(self, key, cache_dir=None, policy=NEVER_EXPIRE, force_months=(), getter=http_get, change_log=None):
        self.key, self.cache_dir, self.policy, self.force, self.get = key, cache_dir, policy, set(force_months), getter
        self.change_log = change_log
        self.calls = self.cache_hits = 0

    def _paths(self, path, lawd, ymd):
        d = os.path.join(self.cache_dir, path.split("/")[0])
        return d, lambda page: os.path.join(d, f"{lawd}_{ymd}_{page}.xml")

    def _read_cached(self, page_path):
        items, page = [], 1
        while True:
            p = page_path(page)
            if not os.path.exists(p):
                return None if page == 1 else items  # 1페이지가 없으면 캐시 없음
            with open(p, "rb") as f:
                got, total = parse(f.read())
            items += got
            if page * PAGE >= total:
                return items
            page += 1

    def _use_cache(self, ym, page_path):
        if not self.cache_dir or ym in self.force or not os.path.exists(page_path(1)):
            return False
        return self.policy is None or self.policy.is_fresh(ym, os.path.getmtime(page_path(1)))

    def _fetch_all(self, path, lawd, ymd):
        bodies, page = [], 1
        while True:
            q = urllib.parse.urlencode({"serviceKey": self.key, "LAWD_CD": lawd, "DEAL_YMD": ymd,
                                        "pageNo": page, "numOfRows": PAGE})
            body = self.get(f"{BASE}/{path}?{q}")
            self.calls += 1
            _, total = parse(body)  # 오류 응답이면 여기서 예외: 캐시를 건드리지 않는다
            bodies.append(body)
            if page * PAGE >= total:
                return bodies
            page += 1

    def items(self, path, lawd, ym):
        ymd = ym.replace("-", "")
        if not self.cache_dir:
            for body in self._fetch_all(path, lawd, ymd):
                yield from parse(body)[0]
            return
        d, page_path = self._paths(path, lawd, ymd)
        if self._use_cache(ym, page_path):
            self.cache_hits += 1
            yield from self._read_cached(page_path)
            return
        old = self._read_cached(page_path)
        bodies = self._fetch_all(path, lawd, ymd)  # 전부 받은 뒤에 교체한다(중간 실패 시 옛 캐시 유지)
        os.makedirs(d, exist_ok=True)
        for k, body in enumerate(bodies, 1):
            tmp = page_path(k) + ".part"  # 중단돼도 반쯤 쓴 파일이 캐시로 남지 않게
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, page_path(k))
        k = len(bodies) + 1
        while os.path.exists(page_path(k)):  # 건수가 줄어 남은 옛 페이지 삭제
            os.remove(page_path(k)); k += 1
        new = [it for body in bodies for it in parse(body)[0]]
        if old is not None and self.change_log:
            self._log(path, lawd, ym, old, new)
        yield from new

    def _log(self, path, lawd, ym, old, new):
        c = diff_items(old, new)
        age = self.policy.age_months(ym) if self.policy else ""
        first = not os.path.exists(self.change_log)
        os.makedirs(os.path.dirname(self.change_log) or ".", exist_ok=True)
        with open(self.change_log, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if first:
                w.writerow(CHANGE_COLS)
            w.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), path.split("/")[0], lawd, ym, age, len(old), len(new),
                        c["added"], c["removed"], c["cancelled"], c["price_changed"], c["other_changed"]])


def region_of(lawd, it):
    """조회 코드와 거래 1건 → 우리 80개 지역 코드. 나눌 수 없는 법정동이면 에러."""
    split = SPLIT.get(lawd)
    if not split:
        return CODE_ALIAS.get(lawd, lawd)
    umd = (it.get("umdNm") or "").strip()
    for code, names in split.items():
        if umd in names:
            return code
    raise ValueError(f"{lawd}의 법정동 '{umd}'을 기존 지역으로 나눌 수 없습니다. molit.SPLIT을 확인하세요.")


def num(s):
    return float(s.replace(",", "")) if s else 0.0


def complex_id(code, it):
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
                for it in client.items(TRADE, q, ym):
                    if region_of(q, it) == code:  # 제물포구는 중구·동구 차례에 각자 몫만 센다
                        add_trade(trade, ym, code, it, meta, ctrade)
                for it in client.items(RENT, q, ym):
                    if region_of(q, it) == code:
                        add_rent(rent, ym, code, it, meta, crent)
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
