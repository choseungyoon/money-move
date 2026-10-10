"""행정동 GeoJSON → 수도권 시군구 경계(단순화·양자화) + 중심점.

입력: vuski/admdongkor 행정동 경계 (ver20260401)
  ver20260401 을 쓰는 이유: 화성 일반구 신설(2026-02-01) 이후이고 인천 개편(2026-07-01) 이전이다.
  그래서 화성은 4개 구로, 인천은 우리 옛 체계(중구·동구·서구)로 나온다. 더 새 버전을 쓰면 인천이 새 구로 나온다.
  부천은 2024년에 구가 재설치됐지만 지역은 부천시 하나로 둔다(MERGE). 다른 자료도 41190 으로 합친다.
출력: data/regions_geo.json  {regions:[{code,sido,name,short,cx,cy,rings:[[x,y,...]]}], bbox}
"""
import csv, json, os, sys
from pathlib import Path
from collections import defaultdict
from shapely.geometry import shape, box, mapping
from shapely.ops import unary_union

SIDO = {"11": "서울", "28": "인천", "41": "경기"}
CLIP = box(126.30, 36.88, 127.90, 38.32)  # 옹진군 원거리 도서 제외
TOL = 0.0018  # ~150m 단순화
# 구는 있지만 지역으로는 시 하나로 두는 곳: 새 코드 → (우리 코드, 이름, 통계청 코드)
MERGE = {c: ("41190", "부천시", "31050") for c in ("41192", "41194", "41196")}
Q = 1e4       # 좌표 양자화 (경도·위도 소수점 4자리 ≈ 10m)

def short_name(sido, sgg):
    # '성남시분당구' → '분당구', '수원시' 그대로, 인천 '중구'/'동구'/'서구'는 시도명으로 구분
    for city in ("수원시", "성남시", "고양시", "용인시", "안산시", "안양시", "화성시"):
        if sgg.startswith(city) and sgg != city:
            return sgg[len(city):]
    if sido == "인천" and sgg in ("중구", "동구", "서구"):
        return "인천" + sgg
    return sgg

def main(src, dst):
    d = json.loads(Path(src).read_text(encoding="utf-8"))
    groups, meta, kostat = defaultdict(list), {}, defaultdict(set)
    for f in d["features"]:
        p = f["properties"]
        if p["sido"] not in SIDO:
            continue
        code, name, kcode = p["sgg"], p["sggnm"], p["adm_cd"][:5]   # adm_cd 앞 5자리 = 통계청 시군구 코드
        if code in MERGE:
            code, name, kcode = MERGE[code]
        groups[code].append(shape(f["geometry"]).buffer(0))
        kostat[code].add(kcode)
        meta[code] = (SIDO[p["sido"]], name)
    out = []
    for code, geoms in sorted(groups.items()):
        g = unary_union(geoms).intersection(CLIP).simplify(TOL, preserve_topology=True)
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        polys = [p for p in polys if p.geom_type == "Polygon" and p.area > 2e-6]
        rings = []
        for p in polys:
            xs = []
            for x, y in p.exterior.coords:
                xs += [round(x * Q) / Q, round(y * Q) / Q]
            rings.append(xs)
        main_poly = max(polys, key=lambda p: p.area)
        c = main_poly.representative_point() if not main_poly.contains(main_poly.centroid) else main_poly.centroid
        sido, sgg = meta[code]
        out.append(dict(code=code, sido=sido, name=sgg, short=short_name(sido, sgg),
                        cx=round(c.x, 4), cy=round(c.y, 4), rings=rings))
    xs = [v for r in out for ring in r["rings"] for v in ring[0::2]]
    ys = [v for r in out for ring in r["rings"] for v in ring[1::2]]
    json.dump({"bbox": [min(xs), min(ys), max(xs), max(ys)], "regions": out},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # 행정안전부 ↔ 통계청 시군구 코드 대응표. 통계청 자료(MDIS 등)는 시도부터 다르다(인천 23, 경기 31)
    # 일부 코드(11110 등)는 두 체계에 모두 있지만 가리키는 구가 달라 대응표 없이 쓰면 조용히 틀린다.
    # 통계청은 그 사이 군 코드를 다시 매겼다(강화 23310 → 23510, 연천 31350 → 31550 등). 이 열은 MDIS 통계청 체계
    # 파일(2021~22년)을 읽는 데 쓰이므로, 이미 있던 지역은 예전 대응을 그대로 둔다. 새 지역(화성 구)만 새 코드를 쓴다.
    codes_path = os.path.join(os.path.dirname(dst), "sgg_codes.csv")
    prev = {}
    if os.path.exists(codes_path):
        with open(codes_path, encoding="utf-8") as f:
            prev = {r["code"]: r["kostat"] for r in csv.DictReader(f)}
    with open(codes_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["code", "kostat", "sido", "name"])
        for code in sorted(kostat):
            assert len(kostat[code]) == 1, (code, kostat[code])
            w.writerow([code, prev.get(code, next(iter(kostat[code]))), meta[code][0], meta[code][1]])
    print(len(out), "regions,", sum(len(ring) for r in out for ring in r["rings"]) // 2, "points")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
