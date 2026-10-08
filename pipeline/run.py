"""파이프라인 진입점.

  python pipeline/run.py demo                  # 데모 데이터로 dist/index.html 생성
  python pipeline/run.py real --from 2024-01 --to 2026-08
      필요: MOLIT_KEY 환경변수(공공데이터포털 인증키, 디코딩 키)
            data/raw/reb_buyer_residence.csv   (R-ONE 매입자거주지별 아파트매매거래, 시군구·월)
            data/raw/mdis/*.csv                (MDIS 국내인구이동 마이크로데이터)
"""
import argparse, glob, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)


def months(a, b):
    y, m = map(int, a.split("-")); out = []
    while f"{y}-{m:02d}" <= b:
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def py(script, *args):
    subprocess.run([sys.executable, os.path.join(HERE, script), *args], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["demo", "real"])
    ap.add_argument("--from", dest="start", default="2024-01")
    ap.add_argument("--to", dest="end", default="2026-08")
    a = ap.parse_args()
    if a.mode == "demo":
        py("synth.py"); py("build_flows.py")
    else:
        from sources import molit, reb, mdis
        key = os.environ.get("MOLIT_KEY") or sys.exit("MOLIT_KEY 환경변수가 필요합니다.")
        interim = os.path.join(ROOT, "data", "interim"); os.makedirs(interim, exist_ok=True)
        raw = os.path.join(ROOT, "data", "raw")
        codes = [r["code"] for r in json.load(open(os.path.join(ROOT, "data", "regions_geo.json"), encoding="utf-8"))["regions"]]
        ms = months(a.start, a.end)
        # 신고기한(30일) 때문에 최근 3개월은 매번 다시 받는다. 나머지는 캐시를 쓴다.
        client = molit.Client(key, cache_dir=os.path.join(raw, "molit"), fresh_months=ms[-3:])
        calls = molit.fetch(client, codes, ms, interim)
        print(f"국토부 API 호출 {calls}회 (캐시 제외)")
        # 부동산원·MDIS는 수동 다운로드 자료다. 새 파일이 없으면 기존 중간 테이블을 그대로 쓴다.
        for name, loader, src, dst in (
            ("부동산원 매입자거주지", reb.load, os.path.join(raw, "reb_buyer_residence.csv"), "buyer_origin_share.csv"),
            ("MDIS 인구이동", mdis.load, os.path.join(raw, "mdis", "*.csv"), "migration_od_month.csv"),
        ):
            if glob.glob(src):
                loader(src, os.path.join(interim, dst))
            elif not os.path.exists(os.path.join(interim, dst)):
                sys.exit(f"{name} 자료가 없습니다: {src}")
            else:
                print(f"{name}: 새 원천 파일이 없어 기존 {dst}를 사용합니다.")
        py("build_flows.py", "--real")
    py("build_web.py")


if __name__ == "__main__":
    main()
