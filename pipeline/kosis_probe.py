"""KOSIS 응답 구조 진단 (GitHub Actions에서 실행). 결과를 ::notice:: 주석으로 남긴다.
키·개별 값은 출력하지 않고 구조(항목 이름, 지역 코드 형식, 행 수)만 요약한다."""
import json, os, sys, urllib.parse, urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from sources import kosis  # noqa: E402

key = os.environ["KOSIS_KEY"]
q = {"method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y", "prdSe": "M", "newEstPrdCnt": "1", **kosis.TABLE}
with urllib.request.urlopen(f"{kosis.BASE}?{urllib.parse.urlencode(q)}", timeout=60) as r:
    rows = json.loads(r.read())
if isinstance(rows, dict):
    print(f"::notice title=error::{json.dumps(rows, ensure_ascii=False)[:500]}"); sys.exit(0)
print(f"::notice title=summary::rows={len(rows)} keys={sorted(rows[0].keys())} prd={sorted({x.get('PRD_DE') for x in rows})}")
items = Counter((x.get("ITM_ID"), x.get("ITM_NM")) for x in rows)
print(f"::notice title=items::{json.dumps([list(k) for k in items], ensure_ascii=False)[:3000]}")
regions = [(x.get("C1"), x.get("C1_NM")) for x in rows]
uniq = list(dict.fromkeys(regions))
pick = [r for r in uniq if r[1] and any(s in r[1] for s in ("서울", "인천", "경기", "종로", "강남", "중구", "수원", "장안", "분당", "부천", "원미"))]
print(f"::notice title=regions::n={len(uniq)} sample={json.dumps(pick[:60], ensure_ascii=False)[:3500]}")
lvl = Counter(len(str(r[0])) for r in uniq)
print(f"::notice title=code_lengths::{dict(lvl)} first10={json.dumps(uniq[:10], ensure_ascii=False)}")
other = {k for x in rows[:1] for k in x if k.startswith(("C2", "C3", "OBJ"))}
print(f"::notice title=other_dims::{sorted(other)} sample={json.dumps({k: rows[0].get(k) for k in rows[0] if k not in ('DT',)}, ensure_ascii=False)[:1500]}")
q = {"method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y", "prdSe": "M", "startPrdDe": "202605", "endPrdDe": "202608",
     "orgId": kosis.TABLE["orgId"], "tblId": kosis.TABLE["tblId"], "itmId": "T10+T20+T30+", "objL1": "ALL"}
with urllib.request.urlopen(f"{kosis.BASE}?{urllib.parse.urlencode(q)}", timeout=60) as r:
    vals = json.loads(r.read())
want = ("28110", "28125", "28140", "28155", "28260", "28275", "28290", "41590", "41190")
for c in want:
    got = {(x["PRD_DE"], x["ITM_ID"]): x.get("DT") for x in vals if str(x.get("C1")) == c}
    print(f"[값] {c}", json.dumps(dict(sorted((f"{p}_{i}", v) for (p, i), v in got.items())), ensure_ascii=False))
