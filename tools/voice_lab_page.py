"""Build a local listening page for the voice candidates:

    .venv/bin/python tools/voice_lab_page.py CANDS_DIR OUT_DIR

Copies the clips to OUT_DIR and writes OUT_DIR/index.html (open it in a
browser; no server needed). Ticked picks are kept in the browser and can be
copied as one line to paste back.
"""

import html
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voice_styles import STYLES  # noqa: E402

GROUPS = [("narration", "解说 · 旁白"), ("lifestyle", "生活 · Vlog"), ("fun", "搞怪 · 方言"), ("english", "English")]


def main(root: Path, out: Path) -> None:
    scores = json.loads((root / "scores.json").read_text("utf-8"))
    clones_p = root / "clone_scores.json"
    clones = json.loads(clones_p.read_text("utf-8")) if clones_p.is_file() else {}
    out.mkdir(parents=True, exist_ok=True)

    def ship(rel: str) -> str:
        dst = out / "audio" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, dst)
        return "audio/" + rel

    def fmt(r: dict) -> str:
        rb = "—" if r["read_back"] is None else f"{r['read_back'] * 100:.0f}%"
        return f"自然度 {r['utmos']:.2f} · 读回误差 {rb} · 音高起伏 {r['pitch_st']} 半音"

    parts = []
    for gid, gname in GROUPS:
        parts.append(f"<h2>{gname}</h2>")
        for s in (s for s in STYLES if s.group == gid):
            rows = sorted((r for r in scores.values() if r["slug"] == s.slug), key=lambda r: -r["utmos"])
            items = []
            for r in rows:
                key = f"{s.slug}-{r['seed']}"
                cl = [c for c in clones.values() if c["slug"] == s.slug and c["seed"] == r["seed"]]
                clone_html = "".join(
                    f'<div class="clone"><span class="eng">{c["engine"]} 读新句子</span>'
                    f'<audio controls preload="none" src="{ship(c["file"])}"></audio>'
                    f'<span class="m">{fmt(c)}</span></div>' for c in sorted(cl, key=lambda c: c["engine"]))
                items.append(
                    f'<div class="cand"><label><input type="checkbox" data-k="{key}"> '
                    f'<b>#{r["seed"] - 100}</b></label>'
                    f'<audio controls preload="none" src="{ship(r["file"])}"></audio>'
                    f'<span class="m">{fmt(r)}</span>{clone_html}</div>')
            parts.append(
                f'<section><h3>{html.escape(s.name_zh)} <small>{html.escape(s.name_en)}</small></h3>'
                f'<p class="d">设定：{html.escape(s.instruct)}</p>'
                f'<p class="d">原句：{html.escape(s.line)}</p>{"".join(items)}</section>')

    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ThunderTalk 音色候选</title>
<style>
:root {{ --bg:#F7F6F3; --card:#fff; --ink:#111; --mute:#787774; --line:#EAEAEA; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#191919; --card:#202020; --ink:#eee; --mute:#9b9a97; --line:#333; }} }}
body {{ background:var(--bg); color:var(--ink); font:15px/1.6 -apple-system, "PingFang SC", sans-serif;
       max-width:880px; margin:0 auto; padding:24px 16px 120px; }}
h1 {{ font-family: "Songti SC", serif; font-weight:600; letter-spacing:-0.02em; }}
h2 {{ margin-top:40px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
section {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px 20px; margin:16px 0; }}
h3 {{ margin:0 0 4px; }} small {{ color:var(--mute); font-weight:400; }}
.d {{ color:var(--mute); margin:2px 0; font-size:13px; }}
.cand {{ border-top:1px solid var(--line); padding:10px 0; display:grid; gap:4px; }}
.clone {{ margin-left:24px; display:grid; gap:2px; }}
audio {{ width:100%; height:32px; }} .m, .eng {{ color:var(--mute); font-size:12px; }}
#bar {{ position:fixed; left:0; right:0; bottom:0; background:var(--card); border-top:1px solid var(--line);
        padding:12px 16px; display:flex; gap:12px; align-items:center; justify-content:center; }}
button {{ background:var(--ink); color:var(--bg); border:0; border-radius:6px; padding:8px 14px; font-size:14px; }}
</style></head><body>
<h1>ThunderTalk 音色候选</h1>
<p class="d">每种风格 4 个候选（VoxCPM2 按描述原创设计，不模仿任何真人或平台音色）。候选下方是 VoxCPM2 / IndexTTS 用它读一句新话——这才是实际使用时听到的效果。
自然度是 UTMOS 预测分（1–5，英文训练，中文只宜同风格内比较）；读回误差越低越好；音高起伏太小会显得平板。勾选你喜欢的，最后点"复制选择"发给我。</p>
{"".join(parts)}
<div id="bar"><span id="n">已选 0 个</span><button id="copy">复制选择</button></div>
<script>
const K = "tt-voice-picks";
let picks = [];
try {{ picks = JSON.parse(localStorage.getItem(K) || "[]"); }} catch (e) {{}}
const boxes = [...document.querySelectorAll("input[data-k]")];
function sync() {{
  document.getElementById("n").textContent = "已选 " + picks.length + " 个";
  try {{ localStorage.setItem(K, JSON.stringify(picks)); }} catch (e) {{}}
}}
boxes.forEach(b => {{
  b.checked = picks.includes(b.dataset.k);
  b.addEventListener("change", () => {{
    picks = boxes.filter(x => x.checked).map(x => x.dataset.k); sync();
  }});
}});
document.querySelectorAll("audio").forEach(a => a.addEventListener("play", () =>
  document.querySelectorAll("audio").forEach(o => {{ if (o !== a) o.pause(); }})));
document.getElementById("copy").addEventListener("click", async () => {{
  const txt = "选中的音色：" + picks.join(", ");
  try {{ await navigator.clipboard.writeText(txt); document.getElementById("n").textContent = "已复制 " + picks.length + " 个"; }}
  catch (e) {{ prompt("复制这行：", txt); }}
}});
sync();
</script></body></html>"""
    (out / "index.html").write_text(page, "utf-8")
    print(out / "index.html")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
