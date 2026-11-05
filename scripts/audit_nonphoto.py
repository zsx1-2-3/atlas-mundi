# -*- coding: utf-8 -*-
"""只读体检：全库里还有多少"不是实景照片"的图（地图/油画/书页/卫星图/标牌/黑白）。

为什么不在选图时一刀切
----------------------
`fill_images` 的 HARD_BAD_RE 是**选择期**的闸门，只挡新选的图；
而库里 1000+ 张里有一批是在闸门加上之前就进来的，还有一些 Commons
分类缺失（大量老文件没有 category），分类判据抓不到 —— 于是"地图/油画"
反复冒出来。这个脚本按**文件名**做一次全库回溯扫描，输出修复清单。

六个桶
------
map       地图/图表/定位图/卫星影像（OSM、Karte、Plan、Landsat、MODIS…）
art       油画/版画/素描/壁画复制品（Braun & Hogenberg 这类古图册也算）
doc       书页/封面/手稿/档案（"Last Page"、"cover"、"001-130"）
sign      标牌/告示/警示牌/纪念牌
bw        黑白/早期照片（194x、Photochrom、sepia）
none      以上都不是

用法
----
    python scripts/audit_nonphoto.py            # 汇总 + 前 80 条
    python scripts/audit_nonphoto.py --all      # 全部列出
    python scripts/audit_nonphoto.py --json     # 机器可读，供批量替换用
"""

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUCKETS = [
    ("map", re.compile(
        r"\bmap\b|maps?\b|karte\b|\bcarte\b|\bplan\b|atlas|chart|topograph|"
        r"location|satellite|landsat|modis|sentinel|copernicus|\biss\b|"
        r"orthophoto|geological|\bplan\b|kaart|plano\b|plattegrond|"
        r"administrative|districts|outline", re.I)),
    ("art", re.compile(
        r"\bpainting\b|oil on canvas|lithograph|engraving|etching|woodcut|"
        r"watercol|gouache|\bdrawing\b|sketch|gravure|\bstich\b|estampe|"
        r"braun|hogenberg|hoefnagel|nebel|photochrom|"
        r"de slag bij|scuola|after (the|a) |copia|reproduction", re.I)),
    ("doc", re.compile(
        r"last page|title page|frontispiece|\bcover\b|page \d|manuscript|"
        r"document|\bletter\b|\bbook\b|postcard|post card|stamp of|\bstamp\b|"
        r"\b\d{3}-\d{3}\b|\b\d{4}-\d{2}-\d{2}\b.*\b\d{4}-\d{2}-\d{2}\b", re.I)),
    ("sign", re.compile(
        r"caution|warning|\bsign\b|signboard|notice|plaque|marker|"
        r"billboard|admission|ticket|information board", re.I)),
    ("bw", re.compile(
        r"black and white|b&w|sepia|\b19[0-4]\d\b|photochrom", re.I)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    hits = {}
    total = 0
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if len(fn) != 7 or not fn.endswith(".json"):
            continue
        iso = fn[:2].upper()
        try:
            rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        except Exception:
            continue
        for it in rec.get("landmark_images") or []:
            name = (it.get("title") or "").replace("File:", "")
            if not name:
                continue
            total += 1
            for label, pat in BUCKETS:
                if pat.search(name):
                    hits.setdefault(label, []).append(
                        dict(iso=iso, cap=it.get("caption_zh") or "",
                             title=name, url=it.get("url") or ""))
                    break

    if args.json:
        json.dump(hits, io.open(os.path.join(ROOT, "review", "nonphoto.json"), "w",
                                encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=1)
        print("已写 review/nonphoto.json")
        return 0

    print("全库 %d 张图，命中非实景特征：" % total)
    for label, pat in BUCKETS:
        items = hits.get(label) or []
        print("  %-5s %4d 张" % (label, len(items)))
    print()
    for label, pat in BUCKETS:
        items = hits.get(label) or []
        if not items:
            continue
        print("=" * 78)
        print("%s：%d 张" % (label, len(items)))
        for x in (items if args.all else items[:20]):
            print("  %-3s %-20s %s" % (x["iso"], x["cap"][:20], x["title"][:58]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
