#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把全部国家的国界合并成一份「全量国境线」专用数据：geo/all.json。

为什么单独做一份
----------------
"显示全部国境线"原来是把 geo/c0..c15.json 十六个分片（合计 4.0MB）全下下来，
再把 25 万个点合并成一条 LineSegments。两个问题：

  1. 4.0MB 太重。开机预取会跟首屏的 8K 贴图抢链路，不预取又要点下去等十几秒
     —— 之前为了省流量把它砍了，结果每次点按钮都要现拉。
  2. 分片是 DP 0.006° 的精度，而球面上 1px 约 0.3°。也就是说那份数据里
     99% 的点落在同一个像素里，纯属白传。

这里按 0.06°（约 6.6km，屏幕上是五分之一个像素）重新抽稀、量化成 0.02° 的整数，
体积降到 0.7MB —— 是原来的六分之一，开机预取也就不心疼了。

前端拿到后直接建 LineSegments 并设 visible=false，按钮只切 visible，秒开。
分片那条老路保留作兜底（all.json 404 或解析失败时用）。

用法
----
    python scripts/build_all_borders.py
"""
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from geo_simplify import dp  # noqa: E402

OUT = os.path.join(ROOT, "geo", "all.json")
TOL = 0.06      # 度。球面 1px≈0.3°，这是五分之一像素
SCALE = 50      # 坐标单位 0.02°，int16 装得下，JSON 里也就 5~6 个字符


def main():
    rings = []
    src_bytes = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "geo", "c*.json"))):
        src_bytes += os.path.getsize(path)
        with open(path, encoding="utf-8") as f:
            chunk = json.load(f)
        for fc in chunk.values():
            for feat in ((fc or {}).get("features") or []):
                g = feat.get("geometry") or {}
                typ = g.get("type")
                polys = ([g.get("coordinates")] if typ == "Polygon"
                         else g.get("coordinates") if typ == "MultiPolygon" else [])
                for poly in (polys or []):
                    for ring in (poly or []):
                        if ring and len(ring) > 2:
                            rings.append([[float(p[0]), float(p[1])] for p in ring])

    out = []
    pts = 0
    for r in rings:
        s = dp(r, TOL)
        if len(s) < 4:
            s = r                      # 微型国家的整环一抽就散架，原样留
        q = []
        for x, y in s:
            p = [int(round(x * SCALE)), int(round(y * SCALE))]
            if q and q[-1] == p:
                continue
            q.append(p)
        if len(q) < 3:
            continue
        if len(q) == 3:
            q.append(q[0])
        out.append(q)
        pts += len(q)

    payload = {"s": SCALE, "r": out}
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(OUT)
    print("[all] %d 环 / %d 点  %.0f KB（分片合计 %.1f MB，省 %.0f%%）"
          % (len(out), pts, size / 1024, src_bytes / 1048576.0,
             (1 - size / float(src_bytes)) * 100))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
