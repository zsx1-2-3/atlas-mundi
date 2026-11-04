# -*- coding: utf-8 -*-
"""只读体检：库里到底有多少张图是"小图"（原图宽度不够，详情页会糊）。

为什么单独做一遍
----------------
2026-11-04 发现 `commons_imageinfo` 把「请求宽度」当成了「图片宽度」：
原图小于请求宽度时 Commons 直接回原图，`thumbwidth` 仍填 1400，
于是 `width >= 900` 这道闸对这类文件完全失效（一张 600×394 的水下照混进了库）。
这里绕开那个字段，直接读 imageinfo 的 `width`（真实原图宽度）。

用法
----
    python scripts/audit_resolution.py                 # 全库，只列小于 900 的
    python scripts/audit_resolution.py --min 1200      # 换标准
    python scripts/audit_resolution.py --fix-list      # 只输出 "ISO 题注 宽度" 便于挑
"""

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect as C                      # noqa: E402

ROOT = C.ROOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=900)
    ap.add_argument("--fix-list", action="store_true")
    args = ap.parse_args()

    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if not re.match(r"^[a-z]{2}\.json$", fn):
            continue
        iso = fn[:2].upper()
        try:
            rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        except Exception:
            continue
        for it in rec.get("landmark_images") or []:
            name = (it.get("title") or "").replace("File:", "")
            if name:
                rows.append((iso, (it.get("caption_zh") or "")[:18], name, it.get("url") or ""))

    print("共 %d 张，取真实尺寸…" % len(rows), flush=True)
    small, total = [], 0
    for i in range(0, len(rows), 10):
        chunk = rows[i:i + 10]
        info = C.commons_imageinfo([r[2] for r in chunk], width=1400)
        for iso, cap, name, url in chunk:
            m = C.lookup_meta(info, name)
            if not m:
                continue
            total += 1
            w = m.get("orig_w") or m.get("width") or 0
            if w and w < args.min:
                small.append((w, iso, cap, name))
        if (i // 10) % 20 == 0:
            print("  %d/%d" % (i + 10, len(rows)), flush=True)

    small.sort()
    print("\n拿到尺寸 %d 张，原图宽度 < %d 的：%d 张（%.1f%%）"
          % (total, args.min, len(small), 100.0 * len(small) / total if total else 0))
    for w, iso, cap, name in (small if args.fix_list else small[:60]):
        print("  %4d  %-3s %-18s %s" % (w, iso, cap, name[:62]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
