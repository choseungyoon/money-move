"""dist/ 생성: index.html(흐름 데이터 인라인) + detail/{code}.json(지역 상세, 누를 때 받음)."""
import json, os, shutil
from pathlib import Path

ROOT = os.path.join(os.path.dirname(__file__), "..")
DIST = os.path.join(ROOT, "dist")
data = json.loads(Path(os.path.join(ROOT, "data", "flows.json")).read_text(encoding="utf-8"))
detail_src = os.path.join(ROOT, "data", "detail")
data["meta"]["detail"] = os.path.isdir(detail_src)
tpl = open(os.path.join(ROOT, "web", "template.html"), encoding="utf-8").read()
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
assert tpl.count("/*__DATA__*/null") == 1
os.makedirs(DIST, exist_ok=True)
dst = os.path.join(DIST, "index.html")
open(dst, "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/null", blob))
if data["meta"]["detail"]:
    shutil.rmtree(os.path.join(DIST, "detail"), ignore_errors=True)
    shutil.copytree(detail_src, os.path.join(DIST, "detail"))
print(f"wrote {dst}: {os.path.getsize(dst) / 1e6:.2f} MB" + (" + detail/" if data["meta"]["detail"] else ""))
