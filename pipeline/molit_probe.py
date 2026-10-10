"""국토부 실거래 API 인증키 진단 (GitHub Actions에서 실행). 결과를 ::notice:: 주석으로 남긴다.
키와 개별 거래 값은 출력하지 않고 응답 코드·건수·필드 구성만 요약한다."""
import os, sys, urllib.parse
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from sources import molit  # noqa: E402

key = os.environ.get("MOLIT_KEY", "")
if not key:
    print("::error title=MOLIT_KEY::Secret이 비어 있습니다. Settings > Secrets and variables > Actions에 MOLIT_KEY를 등록하세요.")
    sys.exit(1)
hide = lambda s: s.replace(key, "***").replace(urllib.parse.quote(key, safe=""), "***")  # noqa: E731
print(f"::notice title=key_shape::길이 {len(key)}, '%' 포함 {'%' in key} (포함이면 인코딩 키일 가능성: 디코딩 키를 넣어야 함)")

t = date.today()
recent = f"{t.year if t.month > 1 else t.year - 1}{(t.month - 2) % 12 + 1:02d}"  # 지난달
old = f"{int(recent[:4]) - 1}{recent[4:]}"
ok = True
for name, path in (("매매", molit.TRADE), ("전월세", molit.RENT)):
    for ymd in (recent, old):
        q = urllib.parse.urlencode({"serviceKey": key, "LAWD_CD": "11680", "DEAL_YMD": ymd, "pageNo": 1, "numOfRows": 1000})
        try:
            body = molit.http_get(f"{molit.BASE}/{path}?{q}", retries=2)
        except Exception as e:  # HTTP 오류(401/403 등)
            ok = False
            print(f"::error title={name} {ymd}::요청 실패 {type(e).__name__}: {hide(str(e))[:300]}")
            continue
        try:
            items, total = molit.parse(body)
        except Exception as e:  # resultCode 오류 또는 XML이 아닌 응답
            ok = False
            print(f"::error title={name} {ymd}::{hide(str(e))[:200]} | 응답 앞부분: {hide(body[:300].decode('utf-8', 'replace'))}")
            continue
        fields = sorted({k for it in items for k in it})
        extra = ""
        if name == "매매":
            extra = f", 해제(cdealType=O) {sum(it.get('cdealType') == 'O' for it in items)}건, " \
                    f"cdealDay 형식 {sorted({len(it.get('cdealDay', '')) for it in items if it.get('cdealDay')})}"
        else:
            extra = f", 계약구분 {dict(Counter(it.get('contractType', '') for it in items))}"
        print(f"::notice title={name} {ymd}::강남구 totalCount {total}, 받은 행 {len(items)}{extra}, "
              f"aptSeq {'있음' if 'aptSeq' in fields else '없음'}, 필드 {fields}")
sys.exit(0 if ok else 1)
