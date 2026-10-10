"""파이프라인 진입점.

  python pipeline/run.py demo                  # 데모 데이터로 dist/index.html 생성
  python pipeline/run.py mdis                  # 내 PC에서: MDIS 원자료 → data/mdis/migration_od_month.csv.gz
      원자료(data/raw/mdis/*.csv|*.txt)는 재배포 금지라 커밋하지 않는다. 시군구×시군구×월 집계만 커밋해
      GitHub Actions의 real 실행이 원자료 없이 돌게 한다.
  python pipeline/run.py real --from 2024-01 --to 2026-08
      필요: MOLIT_KEY 환경변수(공공데이터포털 인증키, 디코딩 키)
            data/raw/reb_buyer_residence.csv   (R-ONE 매입자거주지별 아파트매매거래, 시군구·월)
            data/raw/mdis/*.csv                (MDIS 국내인구이동통계 > 세대관련연간자료, 연도별 CSV)
              또는 data/mdis/migration_od_month.csv.gz  (위 원자료를 `run.py mdis`로 집계해 커밋한 파일)
      권장: KOSIS_KEY 환경변수            (MDIS 이후 달 인구이동 추정용 KOSIS 월별 총계)
      선택: data/raw/nts_income.csv       (국세청 TASIS 시군구별 근로소득 연말정산, 주소지)
            data/raw/card_seoul.csv        (서울 열린데이터광장 OA-23094, 거주지 기준)
            data/raw/card_gyeonggi.csv     (경기데이터드림 카드 소비 데이터)
            data/raw/card_incheon.csv      (인천e음 군구별 결제금액)
"""
import argparse, glob, gzip, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)


def months(a, b):
    y, m = map(int, a.split("-")); out = []
    while f"{y}-{m:02d}" <= b:
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def load_dotenv(path=os.path.join(ROOT, ".env")):
    """로컬 개발용: 저장소 루트 .env의 KEY=VALUE를 환경변수로 읽는다(이미 설정된 값은 덮지 않는다).
    GitHub Actions는 .env 없이 Secrets를 쓴다."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def py(script, *args):
    subprocess.run([sys.executable, os.path.join(HERE, script), *args], check=True)


MDIS_AGG = os.path.join(ROOT, "data", "mdis", "migration_od_month.csv.gz")


def mdis_raw_files(raw):
    return sorted(glob.glob(os.path.join(raw, "mdis", "*.csv")) + glob.glob(os.path.join(raw, "mdis", "*.txt")))


def mdis_aggregate(paths, interim):
    """원자료 → interim 시군구 OD, 그리고 커밋용 gzip 사본(mtime=0이라 내용이 같으면 바이트도 같다)."""
    from sources import mdis
    dst = os.path.join(interim, "migration_od_month.csv")
    rep = mdis.load(paths, dst)
    for name, info in rep.items():
        if "skipped" in info:
            print(f"MDIS {name}: 건너뜀 - {info['skipped']}. data/raw/mdis/에서 빼도 됩니다.")
            continue
        print(f"MDIS {name}: {info['rows']:,}행, 코드 체계 {info['scheme']} (근거 {info['evidence']})" + (f", 대응 안 된 코드 {info['unmapped']}" if info["unmapped"] else ""))
    os.makedirs(os.path.dirname(MDIS_AGG), exist_ok=True)
    with open(dst, "rb") as s, open(MDIS_AGG, "wb") as raw_out, gzip.GzipFile(fileobj=raw_out, mode="wb", filename="", mtime=0) as g:
        shutil.copyfileobj(s, g)
    with open(dst, encoding="utf-8") as f:
        ms = sorted({line.split(",", 1)[0] for line in f if line[:1].isdigit()})
    print(f"→ {os.path.relpath(MDIS_AGG, ROOT)} ({os.path.getsize(MDIS_AGG) / 1e3:.0f} KB, {ms[0]}~{ms[-1]}). 이 파일만 커밋하세요.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["demo", "real", "mdis"])
    ap.add_argument("--from", dest="start", default="2024-01")
    ap.add_argument("--to", dest="end", default="2026-08")
    ap.add_argument("--refresh-all", action="store_true", help="캐시를 무시하고 요청 기간 전체를 다시 받는다")
    a = ap.parse_args()
    load_dotenv()
    if a.mode == "mdis":
        paths = mdis_raw_files(os.path.join(ROOT, "data", "raw"))
        if not paths:
            sys.exit("data/raw/mdis/ 에 MDIS 세대관련연간자료(연도별 .csv 또는 .txt)를 넣으세요.")
        interim = os.path.join(ROOT, "data", "interim"); os.makedirs(interim, exist_ok=True)
        mdis_aggregate(paths, interim)
        return
    if a.mode == "demo":
        py("synth.py"); py("estimate_od.py"); py("build_flows.py"); py("build_detail.py")
    else:
        from sources import molit, reb
        key = os.environ.get("MOLIT_KEY") or sys.exit("MOLIT_KEY 환경변수가 필요합니다.")
        interim = os.path.join(ROOT, "data", "interim"); os.makedirs(interim, exist_ok=True)
        raw = os.path.join(ROOT, "data", "raw")
        codes = [r["code"] for r in json.loads(Path(os.path.join(ROOT, "data", "regions_geo.json")).read_text(encoding="utf-8"))["regions"]]
        ms = months(a.start, a.end)
        # 캐시 재사용 정책: 계약월이 오늘로부터 몇 개월 전인지에 따라 다시 받는 주기가 다르다(molit.RefreshPolicy)
        client = molit.Client(key, cache_dir=os.path.join(raw, "molit"), policy=molit.RefreshPolicy(),
                              force_months=ms if a.refresh_all else (), change_log=os.path.join(raw, "molit_changes.csv"))
        calls = molit.fetch(client, codes, ms, interim)
        print(f"국토부 API 호출 {calls}회, 캐시 재사용 {client.cache_hits}묶음. 변경 기록: data/raw/molit_changes.csv")
        # 부동산원·MDIS는 수동 다운로드 자료다. 새 파일이 없으면 기존 중간 테이블을 그대로 쓴다.
        manifest_path = os.path.join(raw, "manifest.json")
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}

        def changed(*paths):
            """원천 파일이 지난번 적재 때와 같으면 다시 적재하지 않는다(내용 해시로 판단)."""
            h = hashlib.sha256()
            for p in sorted(paths):
                with open(p, "rb") as f:
                    h.update(f.read())
            key = "|".join(sorted(os.path.relpath(p, raw) for p in paths))
            if manifest.get(key) == h.hexdigest():
                return False
            manifest[key] = h.hexdigest()
            return True

        src, dst = os.path.join(raw, "reb_buyer_residence.csv"), os.path.join(interim, "buyer_origin_share.csv")
        if os.path.exists(src) and (changed(src) or not os.path.exists(dst)):
            reb.load(src, dst)
        elif os.path.exists(src):
            print("부동산원 매입자거주지: 원천 파일이 그대로라 기존 buyer_origin_share.csv를 사용합니다.")
        elif not os.path.exists(dst):
            sys.exit(f"부동산원 매입자거주지 자료가 없습니다: {src}")
        # MDIS: 원자료가 있으면(내 PC) 집계하고, 없으면(GitHub Actions) 커밋된 집계 파일을 쓴다
        od = os.path.join(interim, "migration_od_month.csv")
        mraw = mdis_raw_files(raw)
        if mraw and (changed(*mraw) or not os.path.exists(od)):
            mdis_aggregate(mraw, interim)
        elif mraw:
            print("MDIS 인구이동: 원천 파일이 그대로라 기존 migration_od_month.csv를 사용합니다.")
        elif os.path.exists(MDIS_AGG):
            with gzip.open(MDIS_AGG, "rb") as s, open(od, "wb") as d:
                shutil.copyfileobj(s, d)
            print(f"MDIS 인구이동: 커밋된 집계 {os.path.relpath(MDIS_AGG, ROOT)}를 사용합니다.")
        elif not os.path.exists(od):
            sys.exit("MDIS 인구이동 자료가 없습니다: data/raw/mdis/*.csv 를 넣거나 `run.py mdis` 로 만든 집계 파일을 커밋하세요.")
        # 소득·카드: 원천 파일이 있을 때만 갱신(없으면 상세 화면에서 '자료 없음'으로 표시)
        from sources import nts_income, card
        geo = os.path.join(ROOT, "data", "regions_geo.json")
        inc = os.path.join(raw, "nts_income.csv")
        if os.path.exists(inc) and (changed(inc) or not os.path.exists(os.path.join(interim, "income_year.csv"))):
            print(f"국세청 소득: {nts_income.load(inc, os.path.join(interim, 'income_year.csv'), geo)}행")
        rows = []
        for name, fn in (("card_seoul.csv", card.seoul), ("card_gyeonggi.csv", card.gyeonggi)):
            if os.path.exists(os.path.join(raw, name)):
                rows += fn(os.path.join(raw, name))
        if os.path.exists(os.path.join(raw, "card_incheon.csv")):
            rows += card.incheon(os.path.join(raw, "card_incheon.csv"), geo)
        card_files = [os.path.join(raw, n) for n in ("card_seoul.csv", "card_gyeonggi.csv", "card_incheon.csv") if os.path.exists(os.path.join(raw, n))]
        if rows and (changed(*card_files) or not os.path.exists(os.path.join(interim, "card_month.csv"))):
            card.write(rows, os.path.join(interim, "card_month.csv"))
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        # KOSIS 월별 시군구 총계 → MDIS 이후 달 인구이동 추정 (KOSIS_KEY 없으면 추정 없이 최신 MDIS 월로 대체 표시)
        from sources import kosis
        kkey = os.environ.get("KOSIS_KEY")
        if kkey:
            latest = kosis.latest_month(kkey)
            rows, missing = kosis.margins(kkey, ms[0], min(latest, ms[-1]), codes)
            with open(os.path.join(interim, "kosis_sgg_month.csv"), "w", newline="", encoding="utf-8") as f:
                import csv as _csv
                w = _csv.writer(f); w.writerow(kosis.MARGIN_COLS); w.writerows(rows)
            print(f"KOSIS 월별 총계 {len(rows)}행 (최신 {latest})" + (f", 응답에 없는 시군구 {missing}" if missing else ""))
        else:
            print("KOSIS_KEY 가 없어 MDIS 이후 달의 인구이동은 추정하지 않습니다.")
        py("estimate_od.py")
        py("build_flows.py", "--real")
        py("build_detail.py")
    py("build_web.py")


if __name__ == "__main__":
    main()
