# -*- coding: utf-8 -*-
"""生成 2D 平面地图专用的两份小资源。

为什么要单独做一份，而不是直接复用 3D 的东西：

* 底图。3D 的 earth-day.webp 是 4096×2048 / 1.25MB，那是给球面贴图用的。
  2D 里每份世界图最宽也就 1800px（视口高度 ×2），拉 1.25MB 过来纯属浪费 ——
  实测线上点开 2D 要等 20 秒以上，其中一大半是在下这两份大文件。
* 国界。geo/c0..c7.json 共 1.1MB，是完整精度（有些国家几千个点）。
  2D 线宽只有 1.6~2.4px，1800px 宽的一幅图约 5px/度，
  0.02° 的抖动连半个像素都不到，肉眼完全不可见。

所以这里做两件事：
  1. 从 earth-day.webp 缩一张 world-2d.webp（3072×1536，约为原来的 1/6 体积）
  2. 把 8 个分片的边界抽稀 + 量化，合成一个 geo/flat.json

抽稀用「点到上一点距离超过阈值才保留」的流式算法，而不是 Douglas-Peucker：
边界是多边形闭合环，DP 要递归、要算垂距，200 万个点跑下来几十秒；
流式阈值法一遍过，而且对海岸线这种高频抖动的折线效果已经足够。
"""
import json
import os
import sys

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEX_OUT = os.path.join(ROOT, "assets", "tex", "world-2d.webp")
TEX_LOW = os.path.join(ROOT, "assets", "tex", "world-2d-low.webp")
FLAT_OUT = os.path.join(ROOT, "geo", "flat.json")

TEX_W, TEX_H = 3072, 1536
MIN_STEP = 0.10         # 相邻保留点的最小间距（度）。约 11km。
                        # 2D 里一幅世界图宽 1800px，5px/度，0.1° 只有半个像素 ——
                        # 肉眼绝对看不出来，但点数能砍掉七成。
MIN_RING = 3            # 少于这么多个点的环丢掉（小岛礁）


def build_texture():
    src = os.path.join(ROOT, "assets", "tex", "earth-day.webp")
    im = Image.open(src).convert("RGB")
    im.resize((TEX_W, TEX_H), Image.LANCZOS).save(
        TEX_OUT, "WEBP", quality=82, method=6)
    # 低清版：只用来"立刻有东西看"。512×256 拉到全屏是一团糊，
    # 但用户能马上分辨出海洋和大陆的分布，比对着一个黑屏等十秒好。
    im.resize((512, 256), Image.LANCZOS).save(
        TEX_LOW, "WEBP", quality=58, method=6)
    old = os.path.getsize(src) / 1024
    for f, label in ((TEX_OUT, "高清"), (TEX_LOW, "低清")):
        print("[tex] %-4s %6.0f KB（原图 %.0f KB，省 %.0f%%）"
              % (label, os.path.getsize(f) / 1024, old,
                 (1 - os.path.getsize(f) / 1024 / old) * 100))


def simplify(ring):
    """流式抽稀。保留首点，之后每点都跟「上一个保留点」比距离。"""
    if not ring:
        return []
    out = [ring[0]]
    last = ring[0]
    for p in ring[1:]:
        if p is None or last is None:
            continue
        dx = p[0] - last[0]
        dy = p[1] - last[1]
        if dx * dx + dy * dy >= MIN_STEP * MIN_STEP:
            out.append(p)
            last = p
    # 闭合环：末点离首点太近就去掉，让浏览器自己闭合，省一组坐标
    if len(out) > 2 and abs(out[-1][0] - out[0][0]) < MIN_STEP \
            and abs(out[-1][1] - out[0][1]) < MIN_STEP:
        out.pop()
    return out


SCALE = 10              # 坐标以 0.1° 为单位存整数


def q(v):
    """坐标量化成整数（单位 0.1°）。

    直接写小数的话每个点要 7~8 个字符（-123.45），整数只要 4~5 个（-1235）。
    四十万个点，这一个改动就是几百 KB。前端读到之后除以 SCALE 还原。"""
    return int(round(v * SCALE))


def build_flats():
    data = {}
    total_in = total_out = 0
    # 分片数量会变（8 → 16 桶重排过），写死 range(8) 会漏掉 c8..c15 里的国家 ——
    # 实测漏了一半：那半个地球在 2D/点击判定的索引里压根不存在，
    # 点上去没有任何反应。所以按目录里实际存在的文件来。
    import glob
    shards = sorted(glob.glob(os.path.join(ROOT, "geo", "c*.json")))
    if not shards:
        print("[flat] 找不到 geo/c*.json")
        return
    for path in shards:
        total_in += os.path.getsize(path)
        with open(path, encoding="utf-8") as f:
            shard = json.load(f)
        for iso, fc in shard.items():
            rings = data.setdefault(iso, [])
            raw_rings = []
            for feat in ((fc or {}).get("features") or []):
                g = feat.get("geometry") or {}
                typ = g.get("type")
                polys = ([g.get("coordinates")] if typ == "Polygon"
                         else g.get("coordinates") if typ == "MultiPolygon" else [])
                for poly in (polys or []):
                    for ring in (poly or []):
                        if len(ring) >= MIN_RING:
                            raw_rings.append(ring)
                        s = simplify(ring)
                        if len(s) >= MIN_RING:
                            rings.append([[q(p[0]), q(p[1])] for p in s])
            # 微型国家（摩纳哥、瑙鲁、圣马力诺）整环都过不了 0.1° 的步长，
            # 简化后一个环都不剩 —— 它们会从索引里整个消失，点上去没有任何反应。
            # 对这些国家退回原始环：本来就没几个点，不差这点体积。
            if not rings and raw_rings:
                data[iso] = [[[q(p[0]), q(p[1])] for p in r] for r in raw_rings[:3]]

    payload = {"scale": SCALE,
               "countries": [{"iso": k, "rings": v} for k, v in data.items() if v]}
    # separators 去掉空格：坐标数组是纯数字，空格能省下几十 KB
    with open(FLAT_OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    total_out = os.path.getsize(FLAT_OUT)
    n_ring = sum(len(c["rings"]) for c in payload["countries"])
    print("[flat] %d 国 / %d 个环  %.0f KB（原 %.0f KB，省 %.0f%%）"
          % (len(payload["countries"]), n_ring,
             total_out / 1024, total_in / 1024,
             (1 - total_out / total_in) * 100))


if __name__ == "__main__":
    build_texture()
    build_flats()
