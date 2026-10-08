"""data/flows.json 을 web/template.html 에 인라인해 dist/index.html 한 파일로 만든다."""
import json, os

ROOT = os.path.join(os.path.dirname(__file__), "..")
data = json.load(open(os.path.join(ROOT, "data", "flows.json"), encoding="utf-8"))
tpl = open(os.path.join(ROOT, "web", "template.html"), encoding="utf-8").read()
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
assert tpl.count("/*__DATA__*/null") == 1
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
dst = os.path.join(ROOT, "dist", "index.html")
open(dst, "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/null", blob))
print(f"wrote {dst}: {os.path.getsize(dst) / 1e6:.2f} MB")
