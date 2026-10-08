"""행정동 GeoJSON → 수도권 시군구 경계(단순화·양자화) + 중심점.

입력: vuski/admdongkor 행정동 경계 (ver20230701)
출력: data/regions_geo.json  {regions:[{code,sido,name,short,cx,cy,rings:[[x,y,...]]}], bbox}
"""
import json, sys
from collections import defaultdict
from shapely.geometry import shape, box, mapping
from shapely.ops import unary_union

SIDO = {"11": "서울", "28": "인천", "41": "경기"}
CLIP = box(126.30, 36.88, 127.90, 38.32)  # 옹진군 원거리 도서 제외
TOL = 0.0018  # ~150m 단순화
Q = 1e4       # 좌표 양자화 (경도·위도 소수점 4자리 ≈ 10m)

def short_name(sido, sgg):
    # '성남시분당구' → '분당구', '수원시' 그대로, 인천 '중구'/'동구'/'서구'는 시도명으로 구분
    for city in ("수원시", "성남시", "고양시", "용인시", "안산시", "안양시"):
        if sgg.startswith(city) and sgg != city:
            return sgg[len(city):]
    if sido == "인천" and sgg in ("중구", "동구", "서구"):
        return "인천" + sgg
    return sgg

def main(src, dst):
    d = json.load(open(src, encoding="utf-8"))
    groups, meta = defaultdict(list), {}
    for f in d["features"]:
        p = f["properties"]
        if p["sido"] not in SIDO:
            continue
        groups[p["sgg"]].append(shape(f["geometry"]).buffer(0))
        meta[p["sgg"]] = (SIDO[p["sido"]], p["sggnm"])
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
    print(len(out), "regions,", sum(len(ring) for r in out for ring in r["rings"]) // 2, "points")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
