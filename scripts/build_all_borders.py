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

为什么按国家分组（c: {ISO: [环...]}）而不是一个大数组
------------------------------------------------------
因为同一份数据还要给「点亮单个国家」当轮廓用。点一个国家时，如果球面用的
全量线已经在内存里，就没有任何理由再去请求 geo/n/<iso>.json 等那几百毫秒 ——
直接从这份里切出该国的环就行，零网络。分组只多 235 个键，gzip 后几乎没差别。

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


def encode(ring):
    s = dp(ring, TOL)
    if len(s) < 4:
        s = ring                       # 微型国家的整环一抽就散架，原样留
    q = []
    for x, y in s:
        p = [int(round(x * SCALE)), int(round(y * SCALE))]
        if q and q[-1] == p:
            continue
        q.append(p)
    if len(q) < 3:
        return None
    if len(q) == 3:
        q.append(q[0])
    return q


def main():
    by_iso = {}
    src_bytes = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "geo", "c*.json"))):
        src_bytes += os.path.getsize(path)
        with open(path, encoding="utf-8") as f:
            chunk = json.load(f)
        for iso, fc in chunk.items():
            rings = by_iso.setdefault(str(iso).upper(), [])
            for feat in ((fc or {}).get("features") or []):
                g = feat.get("geometry") or {}
                typ = g.get("type")
                polys = ([g.get("coordinates")] if typ == "Polygon"
                         else g.get("coordinates") if typ == "MultiPolygon" else [])
                for poly in (polys or []):
                    for ring in (poly or []):
                        if ring and len(ring) > 2:
                            q = encode([[float(p[0]), float(p[1])] for p in ring])
                            if q:
                                rings.append(q)

    by_iso = {k: v for k, v in by_iso.items() if v}
    payload = {"s": SCALE, "c": by_iso}
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size = os.path.getsize(OUT)
    n_ring = sum(len(v) for v in by_iso.values())
    pts = sum(len(r) for v in by_iso.values() for r in v)
    print("[all] %d 国 / %d 环 / %d 点  %.0f KB（分片合计 %.1f MB，省 %.0f%%）"
          % (len(by_iso), n_ring, pts, size / 1024, src_bytes / 1048576.0,
             (1 - size / float(src_bytes)) * 100))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

