# -*- coding: utf-8 -*-
"""只读体检：把全库图片过一遍"这张到底是不是这处地标的照片"。

不下载、不改数据，只列清单。三类可疑：
  ① 2D 艺术品复制品（版画/石版画/油画/扫描件）—— 分类里带 PD-Art / Prints of / Lithographs…
  ② 文件名像"局部特写/入口/招牌/商品"—— 这类在图注里往往还写着地标名，最能骗人
  ③ 历史黑白照（二战、殖民地时期）—— 文件名或分类里带 marine / 194x / black and white

用法：
    python scripts/audit_images.py            # 全库
    python scripts/audit_images.py MX PG ES   # 只看这几个国家
"""

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F            # noqa: E402

ROOT = F.ROOT

HIST_RE = re.compile(r"marines?|soldiers?|troops|194[0-5]|black and white|"
                     r"b&w|war |battlefield|colonial|koninklijk", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("isos", nargs="*")
    args = ap.parse_args()
    only = {x.upper() for x in args.isos}

    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if not fn.endswith(".json"):
            continue
        iso = fn[:-5].upper()
        if len(iso) != 2 or (only and iso not in only):
            continue
        if fn in ("index.json", "search.json", "landmarks.json", "cards.json"):
            continue
        try:
            rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        except Exception:
            continue
        for it in (rec.get("landmark_images") or []):
            name = (it.get("title") or "").replace("File:", "")
            if name:
                rows.append((iso, (it.get("caption_zh") or "")[:20], name, it.get("url") or ""))

    print("共 %d 张图，开始取分类…" % len(rows), flush=True)
    names = [r[2] for r in rows]
    cats = F.fetch_cats(names)
    print("分类已取回 %d 条\n" % len(cats), flush=True)

    art, detail, hist = [], [], []
    for iso, cap, name, url in rows:
        c = cats.get(F.C.norm_title(name)) or []
        if F.is_artwork(c):
            art.append((iso, cap, name, c))
        elif HIST_RE.search(name) or any(HIST_RE.search(x) for x in c):
            hist.append((iso, cap, name, c))
        elif F.DETAIL_WORDS.search(name) or F.HARD_BAD_RE.search(name):
            detail.append((iso, cap, name, c))

    def dump(title, items, catpat=None):
        print("=" * 78)
        print("%s：%d 张" % (title, len(items)))
        for iso, cap, name, c in items:
            hit = ""
            if catpat:
                for x in c:
                    if catpat.search(x):
                        hit = x[:40]
                        break
            print("  %-3s %-20s %-46s %s" % (iso, cap, name[:46], hit))
        print()

    dump("① 2D 艺术品复制品", art, F.ART_REPRO_RE)
    dump("② 历史黑白照", hist, HIST_RE)
    dump("③ 局部/入口/商品", detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
