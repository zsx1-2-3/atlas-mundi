# -*- coding: utf-8 -*-
"""生成"选图台"：给每个待补/待换的格子找 4 个候选，拼成一个可点选的本地网页。

输出：
  review/choose/           候选缩略图（相对路径引用，HTML 可离线打开）
  review/选图台.html        点选 → 自动生成一行可复制的清单

为什么要有这个：纯 Markdown 清单要来回翻文件、复制文件名，容易错；
点一下就是 `AW:2 NE:1 ...`，粘回来我就能照做。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
import fill_images as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "review", "choose")
HTML = os.path.join(ROOT, "选图台.html")   # 放项目根目录，好找

# (iso, 中文题注, 检索词, 文件名必须含, 说明)
JOBS = [
    ("AW", "天然桥", "Aruba Natural Bridge", "natural bridge", "原图是黄石公园的天然桥"),
    ("NE", "阿加德兹大清真寺宣礼塔", "Agadez Grand Mosque", "agadez", "原图是阿曼尼兹瓦堡"),
    ("SV", "罗萨里奥教堂", "Iglesia El Rosario San Salvador", "rosario", "原图在西班牙"),
    ("BD", "孙德尔本斯红树林", "Sundarbans Bangladesh", "sundarban", "原图在印度西孟加拉邦"),
    ("CI", "宁巴山严格自然保护区", "Mount Nimba Cote d'Ivoire", "nimba", "原图在几内亚"),
    ("LR", "宁巴山自然保护区", "Mount Nimba Liberia", "nimba", "原图在几内亚"),
    ("LI", "瓦杜兹城堡", "Vaduz Castle Liechtenstein", "vaduz", "原图在奥地利"),
    ("MK", "奥赫里德圣索菲亚教堂", "Saint Sophia Ohrid", "sophia", "原图在希腊塞萨洛尼基"),
    ("SD", "青尼罗河", "Blue Nile Sudan", "nile", "原图在埃塞俄比亚"),
    ("CG", "桑加河", "Sangha River Congo", "sangha", "原图在 Central African Republic"),
    ("AT", "因斯布鲁克黄金屋顶", "Goldenes Dachl Innsbruck", "dachl", "现图是缆车站"),
    ("BY", "白俄罗斯国家图书馆", "National Library of Belarus", "library", "现图是城市远景"),
    ("CY", "帕福斯考古公园", "Paphos Archaeological Park", "paphos", "现图是公路切坡"),
    ("DZ", "塔西里岩画高原", "Tassili n'Ajjer rock art", "tassili", "现图是高空航拍沙丘"),
    ("EH", "提法里提岩画", ["Tifariti", "Western Sahara rock art"], "", "现图是医院外观"),
    ("GN", "富塔贾隆高原", ["Fouta Djallon", "Fouta Djallon Guinea"], "", "现图是水下特写"),
    ("GW", "卡谢乌古堡", "Cacheu fortress Guinea-Bissau", "cacheu", "现图是古堡雕像"),
    ("IQ", "泰西封拱门", "Taq Kasra Ctesiphon", "ctesiphon", "现图是博物馆展陈"),
    ("JM", "鲍勃·马利故居", ["Bob Marley Museum", "Nine Mile Jamaica Marley"], "marley", "现图是工艺品陈列"),
    ("LA", "万象凯旋门", "Patuxai Vientiane", "patuxai", "现图是罗马的凯旋门"),
    ("MH", "马朱罗环礁航拍", ["Majuro atoll", "Majuro Marshall Islands"], "majuro", "现图有军舰占主体"),
    ("MK2", "斯科普里石桥", "Stone Bridge Skopje", "bridge", "现图是广场街景"),
    ("SL", "塔凯拉瀑布", "Tokeh Sierra Leone", "tokeh", "现图是海滩不是瀑布"),
]

PER = 5          # 每个格子给几个候选
MIN_W = 900      # 候选最小宽度（冷门地标没有更大的）


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cards = []
    for idx, (iso, cap, kw, must, note) in enumerate(JOBS):
        kws = kw if isinstance(kw, list) else [kw]
        picks = []
        for k in kws:
            if len(picks) >= PER:
                break
            try:
                names = F.search_files(k, 14)
            except Exception as e:
                print("x", iso, str(e)[:40])
                continue
            info = F.C.commons_imageinfo(names, width=1400)
            for n in names:
                if any(n == p[0] for p in picks):
                    continue
                m = F.C.lookup_meta(info, n) or {}
                if (m.get("orig_w") or 0) < MIN_W:
                    continue
                if must and must.lower() not in n.lower():
                    continue
                low = n.lower()
                if any(k in low for k in ("map", "karte", "carte", "painting", "drawing",
                                          "stamp", "coin", "banknote", "satellite",
                                          "logo", "diagram", "chart")):
                    continue
                picks.append((n, m))
                if len(picks) >= PER:
                    break
        items = []
        for j, (n, m) in enumerate(picks):
            rel = "review/choose/%02d_%d.jpg" % (idx, j)
            dst = os.path.join(ROOT, rel)
            try:
                F.C.download(m.get("thumb_url") or m.get("remote_url"), dst)
            except Exception as e:
                print("  下载失败", n[:40], str(e)[:30])
                continue
            items.append({"file": rel, "name": n,
                          "w": m.get("orig_w") or 0, "h": m.get("orig_h") or 0})
        cards.append({"iso": iso, "cap": cap, "note": note,
                      "kw": (kw[0] if isinstance(kw, list) else kw), "items": items})
        print("%-3s %-24s 候选 %d" % (iso, cap[:24], len(items)))

    html = build_html(cards)
    io.open(HTML, "w", encoding="utf-8", newline="\n").write(html)
    print("\n生成", HTML, "共", len(cards), "行")
    json.dump(cards, io.open(os.path.join(ROOT, "review", "choose.json"), "w",
                             encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)


def build_html(cards):
    rows = []
    for c in cards:
        cells = []
        for i, it in enumerate(c["items"], 1):
            cells.append(
                ('<label class="opt"><input type="radio" name="__ISO__" value="__I__">'
                 '<img src="__F__" loading="lazy"><span class="num">__I__</span>'
                 '<span class="dim">__W__×__H__</span></label>')
                .replace("__ISO__", c["iso"]).replace("__I__", str(i))
                .replace("__F__", it["file"]).replace("__W__", str(it["w"]))
                .replace("__H__", str(it["h"])))
        cells.append(
            ('<label class="opt del"><input type="radio" name="__ISO__" value="del">'
             '<div class="ph">删掉这格<br><small>宁缺勿错</small></div></label>')
            .replace("__ISO__", c["iso"]))
        cells.append(
            ('<label class="opt keep"><input type="radio" name="__ISO__" value="keep" checked>'
             '<div class="ph">不动<br><small>暂时保留</small></div></label>')
            .replace("__ISO__", c["iso"]))
        rows.append("""
        <section class="row">
          <div class="hd"><b>__ISO__</b> __CAP__<span class="note">__NOTE__</span>
            <span class="kw">检索：__KW__</span></div>
          <div class="opts">__CELLS__</div>
        </section>""".replace("__ISO__", c["iso"]).replace("__CAP__", c["cap"])
                     .replace("__NOTE__", c["note"]).replace("__KW__", c["kw"])
                     .replace("__CELLS__", "".join(cells)))

    TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>寰宇图志 · 选图台</title>
<style>
  :root{--bg:#0b0f16;--panel:#121822;--line:#222c3a;--ink:#e6edf6;--dim:#8fa1b6;--acc:#5eead4}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
  header{position:sticky;top:0;z-index:9;background:rgba(11,15,22,.94);
         backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
         padding:14px 22px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.3px}
  .hint{color:var(--dim);font-size:12px}
  textarea{flex:1;min-width:320px;height:38px;background:#0a0e15;color:var(--acc);
           border:1px solid var(--line);border-radius:8px;padding:8px 10px;
           font-family:ui-monospace,Consolas,monospace;font-size:13px}
  button{background:var(--acc);color:#06251f;border:0;border-radius:8px;
         padding:9px 16px;font-weight:600;cursor:pointer}
  button.ghost{background:transparent;color:var(--dim);border:1px solid var(--line)}
  main{padding:18px 22px 60px}
  .row{background:var(--panel);border:1px solid var(--line);border-radius:12px;
       padding:14px 16px;margin-bottom:14px}
  .hd{font-size:14px;margin-bottom:10px}
  .hd b{color:var(--acc);margin-right:8px;font-family:ui-monospace,monospace}
  .note{color:#f2a3a3;font-size:12px;margin-left:10px}
  .kw{color:var(--dim);font-size:11px;margin-left:10px}
  .opts{display:flex;gap:10px;align-items:stretch}
  .opt{position:relative;display:block;cursor:pointer;border:2px solid transparent;
       border-radius:10px;overflow:hidden;background:#0a0e15;
       flex:1 1 0;min-width:0;max-width:216px}
  .opt img{display:block;width:100%;height:110px;object-fit:cover}
  .opt .num{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.65);
            color:#fff;border-radius:6px;padding:1px 7px;font-size:12px}
  .opt .dim{position:absolute;bottom:4px;right:6px;color:#cfe;font-size:10px;
            background:rgba(0,0,0,.55);border-radius:4px;padding:0 5px}
  .opt input{position:absolute;opacity:0;pointer-events:none}
  .opt:has(input:checked){border-color:var(--acc);box-shadow:0 0 0 3px rgba(94,234,212,.18)}
  .opt.keep:has(input:checked),.opt.del:has(input:checked){border-color:#f2a3a3}
  .ph{height:110px;min-width:96px;display:flex;flex-direction:column;align-items:center;
      justify-content:center;color:var(--dim);font-size:12px;text-align:center}
  .ph small{opacity:.7}
  @media(max-width:900px){.opts{flex-wrap:wrap}.opt{flex:1 1 30%}}
</style></head><body>
<header>
  <h1>寰宇图志 · 选图台</h1>
  <span class="hint">每行挑一个（或选"删掉/不动"），下面那行会自动更新，复制给我就行</span>
  <textarea id="out" readonly></textarea>
  <button onclick="copyOut()">复制清单</button>
  <button class="ghost" onclick="location.reload()">重置</button>
</header>
<main>__ROWS__</main>
<script>
const O = document.getElementById('out');
function sync(){{
  const parts=[];
  document.querySelectorAll('.row').forEach(r=>{{
    const iso=r.querySelector('.hd b').textContent.trim();
    const c=r.querySelector('input[type=radio]:checked');
    if(!c||c.value==='keep') return;
    parts.push(iso+':'+c.value);
  }});
  O.value = parts.join(' ');
}}
document.addEventListener('change', sync);
sync();
function copyOut(){{
  O.select(); document.execCommand('copy');
  const b=event.target; const t=b.textContent; b.textContent='已复制';
  setTimeout(()=>b.textContent=t,1200);
}}
</script></body></html>"""
    return TPL.replace("__ROWS__", "".join(rows))


if __name__ == "__main__":
    main()
