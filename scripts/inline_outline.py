#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「粗轮廓」写进 data/{iso}.json 的 outline 字段。

为什么
------
点开一个国家时，球面上的金线和卡片是两条完全独立的请求：
    data/{iso}.json  同源 GitHub Pages，约 0.8~1.5s
    geo/n/{iso}.json jsDelivr CDN，    约 0.4~1.2s
谁先到谁先出现。CDN 那次偶尔更慢，于是就出现「卡片都展开了、国土还在路上」。
实测同一批国家，两条链路的最大差值接近 700ms —— 这段时间里画面是断的。

把轮廓塞进档案本身，两条链路就变成一个：
    档案到了 → 卡片和轮廓同时出现，永远不会错位。
代价是每个档案大 1~3KB（0.05° 抽稀，球面一像素约 0.3°，肉眼看不出）。
精细几何（geo/n）仍然照常请求，到了就把这条粗线换成 0.006° 的版本，
所以面板里那张 SVG 和放大后的球面细节一点没丢。

字段格式
--------
    "outline": [[[lon_i, lat_i], ...], ...]      # 环数组，坐标是整数的 0.01°

取整是为了体积：保留三位小数的话每个点 12~16 字节，取整后 8~10 字节。
0.01° 约 1.1km，比抽稀容差还小，取整不损失形状。

可重复运行：已有 outline 会被覆盖（方便调参），其余字段原样保留。
采集脚本不会写这个字段，重新采集后需要再跑一次本脚本。
"""
import argparse
import glob
import json
import os
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from geo_simplify import dp  # noqa: E402

GEO_N = os.path.join(ROOT, "geo", "n")
DATA = os.path.join(ROOT, "data")
TOL = 0.05          # 度，约 5.5km；球面 1px≈0.3°，这是 1/6 像素
MAX_PTS = 900       # 单国上限，加拿大北极群岛那种不设限能到 4000+
SCALE = 100         # 0.01° 一格

# 这几个国家的轮廓有合规/辨识度要求，不能为了省字节把碎岛丢掉。
KEEP_SMALL = {"CN", "JP", "ID", "PH", "MV", "GR", "HR", "GB", "NO", "CL", "VN", "MY"}


def rings_of(gj):
    out = []
    for f in gj.get("features", []) or []:
        g = f.get("geometry") or {}
        t = g.get("type")
        polys = [g.get("coordinates")] if t == "Polygon" else (
            g.get("coordinates") or [] if t == "MultiPolygon" else [])
        for poly in polys:
            for r in poly:
                if r and len(r) > 2:
                    out.append([[float(p[0]), float(p[1])] for p in r])
    return out


def bbox_area(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return max(1e-9, (max(xs) - min(xs)) * (max(ys) - min(ys)))


def substep(ring, n):
    """等间隔抽到 n 个点。首点必留、闭合点补回 —— 国界是环，断了就画不出来。"""
    m = len(ring)
    if n >= m:
        return ring
    n = max(4, n)
    step = m / float(n)
    out = [ring[int(i * step)] for i in range(n)]
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def encode(rings, iso, tol=TOL, budget=MAX_PTS):
    items = []
    for r in rings:
        s = dp(r, tol)
        if len(s) < 4:
            # 微型国家（摩纳哥、瑙鲁、直布罗陀）本身就十几个点，
            # 一抽就散架 —— 原样留着，否则球上连一个点都画不出来。
            s = r
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
        items.append(q)
    if not items:
        return []

    # 第一步：按面积留岛。
    # 加拿大有 412 个环、智利 376 个 —— 全留的话光"每环 4 点"就 1600 点，
    # 预算全被碎岛吃掉，主陆反而被抽成折线。改成累加面积到 99% 就截断：
    # 剩下的加起来只有全国面积的 1%，在球上是几颗灰尘。
    items.sort(key=lambda q: -bbox_area(q))
    area_total = sum(bbox_area(q) for q in items)
    if iso in KEEP_SMALL:
        keep_n = len(items)          # 合规国家不许丢岛，改用压点数来控体积
    else:
        acc, keep_n = 0.0, 0
        for q in items:
            acc += bbox_area(q)
            keep_n += 1
            if acc >= area_total * 0.99 and keep_n >= 4:
                break
    items = items[:max(1, keep_n)]

    # 第二步：按面积分预算 —— 大环多给点，小环保底 4~6 点。
    total = sum(len(q) for q in items)
    if total <= budget:
        return items
    floor = 4 if len(items) > 60 else (5 if len(items) > 20 else 8)
    floor = min(floor, max(4, budget // max(1, len(items))))
    spare = budget - floor * len(items)
    if spare < 0:
        spare = 0
    extra_area = sum(bbox_area(q) for q in items) or 1.0
    out = []
    for q in items:
        share = int(spare * (bbox_area(q) / extra_area))
        out.append(substep(q, floor + share))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真的写文件")
    ap.add_argument("--stats", action="store_true", help="打印每个国家的体积")
    a = ap.parse_args()

    geo_files = {os.path.basename(p)[:-5].upper(): p
                 for p in glob.glob(os.path.join(GEO_N, "*.json"))}
    data_files = [p for p in sorted(glob.glob(os.path.join(DATA, "*.json")))
                  if os.path.basename(p)[0] != "_"
                  and os.path.basename(p) not in
                  ("index.json", "landmarks.json", "search.json", "search_index.json")]

    done, missing, before, after = 0, [], 0, 0
    for p in data_files:
        iso = os.path.basename(p)[:-5].upper()
        gp = geo_files.get(iso)
        if not gp:
            missing.append(iso)
            continue
        with open(gp, encoding="utf-8") as f:
            gj = json.load(f)
        outline = encode(rings_of(gj), iso)
        if not outline:
            missing.append(iso)
            continue
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
        b0 = os.path.getsize(p)
        rec["outline"] = outline
        blob = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        if a.write:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(blob)
            b1 = os.path.getsize(p)
        else:
            b1 = len(blob.encode("utf-8"))
        before += b0
        after += b1
        done += 1
        if a.stats:
            pts = sum(len(q) for q in outline)
            print("  %-3s 环 %2d 点 %4d  档案 %5.1f → %5.1f KB"
                  % (iso, len(outline), pts, b0 / 1024.0, b1 / 1024.0))

    print("处理 %d 个国家%s" % (done, "（已写入）" if a.write else "（未写，加 --write 才落盘）"))
    print("档案总体积 %.1f → %.1f KB（+%.1f%%）"
          % (before / 1024.0, after / 1024.0, (after - before) * 100.0 / max(before, 1)))
    if missing:
        print("没有几何（跳过 %d）：%s" % (len(missing), "、".join(missing[:12])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
