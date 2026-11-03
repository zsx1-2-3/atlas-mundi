#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寰宇图志 · 图片优化（构建期，跑在采集之后）

用法：
    python scripts/optimize_images.py CN FR US
    python scripts/optimize_images.py --all

把采集下来的大图转成 WebP 双尺寸，并把 JSON 里的路径改过去：
    详情图 760px q72   —— 面板里显示宽度约 360px，760 是 2 倍图
    缩略图 400px q74   —— 入口卡封面高 132px

为什么要单独一步：
    Wikimedia 的原图动辄 200KB+，国内直连 GitHub Pages 拉一张要 8 秒。
    录屏要求「不能出现空白加载」，所以必须压。实测 960×615 的 272KB JPEG →
    108KB + 27KB，加载 8.4s → 1.8s。

不要做的事：
    不要上采样。把 960 拉到 1000 再压，产物 228KB，几乎没省 —— 白忙。
"""

import argparse
import json
import os
import sys

# 控制台可能是 cp936（Windows cmd 默认），打不出的字符用 ? 代替，别让整段崩掉。
# 之前 collect.py 在连不上某个站时打印一个 ✗ 就 UnicodeEncodeError 退出，
# 看起来像"采集失败"，其实连抓取都还没开始。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 详情图 1080：面板图区宽约 456 CSS px，手机 3 倍屏要 1368 物理像素，桌面 2 倍屏要 912。
# 原来的 760 连桌面 2 倍屏都不够（760 < 912），所以主图一直有点软。
# 1080 是折中：桌面 2 倍屏完全够，手机 3 倍屏到 1.9 倍，肉眼已经很难分辨。
DETAIL_W, DETAIL_Q = 1080, 75
# 缩略图 240：胶片带里每张只显示 64×46 CSS px，3 倍屏也只要 192 物理像素。
# 原来的 400 是六倍冗余 —— 240 仍然有 3.75 倍余量，体积却省一半多。
THUMB_W, THUMB_Q = 240, 78
THUMB_H = 180          # 竖图按宽度缩完仍然很高，再按最大高度兜一次（4:3）


def optimize(iso):
    slug = iso.lower()
    adir = os.path.join(ROOT, "assets", slug)
    data_path = os.path.join(ROOT, "data", "%s.json" % slug)
    if not os.path.exists(data_path):
        print("  ! data/%s.json 不存在，跳过" % slug)
        return
    with open(data_path, encoding="utf-8") as f:
        rec = json.load(f)

    images = rec.get("landmark_images") or []
    if not images:
        print("  ! %s 没有地标图" % iso)
        return

    from PIL import Image

    changed = False
    for im_meta in images:
        src_rel = im_meta.get("url") or ""
        src = os.path.join(ROOT, src_rel)

        # 第一遍压完之后 url 已经指向 .webp 了，这时候直接 continue 会让
        # 这个脚本变成"只能跑一次"。所以要回头按同名去捞原始 jpg/png ——
        # 原图一直在本地留着（PRUNE 默认关），正是为了能随时调压缩参数。
        if src_rel.lower().endswith(".webp"):
            stem = os.path.splitext(src)[0]
            for ext in (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"):
                if os.path.exists(stem + ext):
                    src = stem + ext
                    break
            else:
                continue          # 没有原图，保留现有 webp，不放大
        elif not src_rel.lower().endswith((".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")):
            continue

        if not os.path.exists(src):
            print("  ! 源图缺失 %s" % src_rel)
            continue

        # 幂等：已经压到目标宽度就不再重压（想强制重压用 --force）
        cur = (im_meta.get("size") or {}).get("detail") or ""
        if not FORCE["on"] and cur.startswith("%dx" % DETAIL_W):
            continue

        base = os.path.splitext(src)[0]
        detail_rel = os.path.relpath(base + ".webp", ROOT).replace("\\", "/")
        thumb_rel = os.path.relpath(base + ".thumb.webp", ROOT).replace("\\", "/")

        img = Image.open(src).convert("RGB")
        orig = os.path.getsize(src)

        def save(w, dest, q, max_h=None):
            w2 = min(w, img.width)          # 绝不放大
            h2 = round(img.height * w2 / img.width)
            # 竖图按宽度缩放后仍然很高（布达拉宫这类），再按最大高度兜一次
            if max_h and h2 > max_h:
                h2 = max_h
                w2 = max(1, round(img.width * h2 / img.height))
            img.resize((w2, h2), Image.LANCZOS).save(dest, "WEBP", quality=q, method=6)
            return os.path.getsize(dest), (w2, h2)

        s1, d1 = save(DETAIL_W, os.path.join(ROOT, detail_rel), DETAIL_Q)
        s2, d2 = save(THUMB_W, os.path.join(ROOT, thumb_rel), THUMB_Q, max_h=THUMB_H)

        im_meta["url"] = detail_rel
        im_meta["thumb_url"] = thumb_rel
        im_meta["bytes"] = s1
        im_meta["thumb_bytes"] = s2
        im_meta["size"] = {"detail": "%dx%d" % d1, "thumb": "%dx%d" % d2}
        im_meta["encoding"] = "webp q%d/q%d (源 %dx%d %.0fKB JPEG)" % (
            DETAIL_Q, THUMB_Q, img.width, img.height, orig / 1024)
        changed = True
        # 源图现在没用了（详情页读 webp）：留着 223 国要多出几百 MB
        if PRUNE["on"] and os.path.exists(src):
            try:
                os.remove(src)
            except OSError:
                pass
        print("  [ok] %s  %.0f KB → 详情 %dKB + 缩略 %dKB  (省 %.0f%%)" % (
            src_rel, orig / 1024, s1 / 1024, s2 / 1024,
            100 - (s1 + s2) * 100 / orig))

    if not changed:
        print("  - 无需处理")
        # 已经压过了也要清孤儿（换过地标图之后，采集那批 P18 图就没人引用了）
        if PRUNE["on"]:
            _prune_orphans(iso, rec)
        return

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 采集阶段自动拿的 P18 图（01.jpg 之类）在换成地标图后就成了孤儿文件：
    # 223 国每个都留 200~400KB，白占仓库。这里把没被任何字段引用的图删掉。
    if PRUNE["on"]:
        _prune_orphans(iso, rec)

    idx_path = os.path.join(ROOT, "data", "index.json")
    if os.path.exists(idx_path):
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
        for c in idx.get("countries", []):
            if c.get("iso_a2", "").upper() == iso.upper() and images:
                c["thumb"] = images[0].get("thumb_url")
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("  [ok] data/index.json 缩略图已同步")


# --prune 的开关。压图成功后源文件就没用了：详情页读的是 webp，
# 但留着的话 223 国 × 5 张 × 0.4MB ≈ 450MB，仓库和 Pages 都扛不住。
PRUNE = {"on": False}
FORCE = {"on": False}


def _prune_orphans(iso, rec):
    """删掉 assets/{iso}/ 里已经没被引用的图片文件。"""
    adir = os.path.join(ROOT, "assets", iso.lower())
    if not os.path.isdir(adir):
        return
    refs = set()
    for im in rec.get("landmark_images") or []:
        for k in ("url", "thumb_url"):
            v = im.get(k)
            if isinstance(v, str) and v:
                refs.add(os.path.basename(v))
    for l in rec.get("languages") or []:
        v = l.get("audio_url")
        if isinstance(v, str) and v:
            refs.add(os.path.basename(v))
    killed = 0
    for fn in os.listdir(adir):
        if not fn.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg")):
            continue
        if fn in refs:
            continue
        try:
            os.remove(os.path.join(adir, fn))
            killed += 1
        except OSError:
            pass
    if killed:
        print("  [prune] 清掉未引用的图 %d 个" % killed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("isos", nargs="*", help="ISO 码，如 CN FR US")
    ap.add_argument("--all", action="store_true", help="处理 data/ 下全部档案")
    ap.add_argument("--prune", action="store_true",
                    help="WebP 写好之后删掉源 jpg/png（223 国能省下几百 MB）")
    ap.add_argument("--force", action="store_true",
                    help="已经是目标宽度也重压一遍")
    args = ap.parse_args()
    PRUNE["on"] = bool(args.prune)
    FORCE["on"] = bool(args.force)

    isos = [x.upper() for x in args.isos]
    if args.all:
        d = os.path.join(ROOT, "data")
        isos = []
        for f in os.listdir(d):
            if not f.endswith(".json") or f in ("index.json", "landmarks.json"):
                continue
            try:
                with open(os.path.join(d, f), encoding="utf-8") as fp:
                    rec = json.load(fp)
            except Exception:
                continue
            # 只认带 iso_a2 的档案，配置类 JSON（landmarks 等）一律跳过
            if rec.get("iso_a2"):
                isos.append(rec["iso_a2"].upper())
    if not isos:
        ap.error("给个 ISO 码，或用 --all")

    for iso in isos:
        print("[%s]" % iso)
        optimize(iso)


if __name__ == "__main__":
    main()
