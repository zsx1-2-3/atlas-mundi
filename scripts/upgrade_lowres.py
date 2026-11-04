# -*- coding: utf-8 -*-
"""把库里"小图"换成更清晰的版本 —— 只换真的有更好素材的那几张。

规则（缺一不可）
----------------
1. 现图真实宽度 < --max-w（默认 700，900 那条线太靠上，见 audit_resolution）
2. 新候选真实宽度 >= --min-new（默认 1200），否则不如不换
3. 新候选要过原有的黑名单（地图/版画/标牌/局部）和专名闸门
4. 新候选不能是同一张文件

用法
----
    python scripts/upgrade_lowres.py                # 只出方案（默认）
    python scripts/upgrade_lowres.py --limit 5      # 先看 5 条
    python scripts/upgrade_lowres.py --apply        # 真的换
    python scripts/upgrade_lowres.py --max-w 900    # 放宽到 900 再试

尺寸查询会走 Commons，一次约 1~2 分钟；结果缓存在 review/lowres_cache.json，
重跑时加 --no-cache 可强制刷新。
"""

import argparse
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F                       # noqa: E402
from replace_image import NOT_REP, do_swap, load_rec, find_target   # noqa: E402

ROOT = F.ROOT
CACHE = os.path.join(ROOT, "review", "lowres_cache.json")


def sizes_of(titles, use_cache=True):
    """批量取真实宽度。缓存按文件名存，避免每次重跑都打接口。"""
    cache = {}
    if use_cache and os.path.exists(CACHE):
        try:
            cache = json.load(io.open(CACHE, encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [t for t in titles if t not in cache]
    for i in range(0, len(todo), 10):
        chunk = todo[i:i + 10]
        info = F.C.commons_imageinfo(chunk, width=1400)
        for t in chunk:
            m = F.C.lookup_meta(info, t)
            cache[t] = (m or {}).get("orig_w") or 0
        if (i // 10) % 20 == 0:
            print("  取尺寸 %d/%d" % (i + 10, len(todo)), flush=True)
    if use_cache:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with io.open(CACHE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cache, f, ensure_ascii=False)
    return cache


def must_map():
    """(iso, 中文题注) -> must。改过题注的查不到就返回 None。"""
    path = os.path.join(ROOT, "data", "landmarks.json")
    try:
        conf = json.load(io.open(path, encoding="utf-8")).get("countries") or {}
    except Exception:
        return {}
    out = {}
    for iso, items in conf.items():
        for it in items:
            out[(iso, it.get("zh"))] = it.get("must") or ""
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-w", type=int, default=700)
    ap.add_argument("--min-new", type=int, default=1200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("iso", nargs="*")
    args = ap.parse_args()
    only = {x.upper() for x in args.iso}

    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if len(fn) != 7 or not fn.endswith(".json"):
            continue
        iso = fn[:2].upper()
        if only and iso not in only:
            continue
        rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        for it in rec.get("landmark_images") or []:
            name = (it.get("title") or "").replace("File:", "")
            if name:
                rows.append((iso, (it.get("caption_zh") or ""), name,
                             it.get("kw") or "", it.get("url") or ""))
    print("库里 %d 张，查真实宽度…" % len(rows), flush=True)
    wmap = sizes_of([r[2] for r in rows], use_cache=not args.no_cache)

    small = [r for r in rows if 0 < (wmap.get(r[2]) or 0) < args.max_w]
    print("宽度 < %d 的：%d 张\n" % (args.max_w, len(small)))
    if args.limit:
        small = small[:args.limit]

    mst = must_map()
    plans, skipped = [], []
    for n, (iso, cap, name, kw, url) in enumerate(small, 1):
        cur_w = wmap.get(name) or 0
        entry = {"zh": cap or kw, "kw": kw or cap}
        m = mst.get((iso, cap)) or ""
        if m:
            entry["must"] = m
        print("[%d/%d] %s %s（现 %dpx）" % (n, len(small), iso, cap[:14], cur_w), flush=True)
        try:
            picks, errs = F.ranked_picks(entry)
        except Exception as e:
            skipped.append((iso, cap, cur_w, "检索异常 %s" % str(e)[:40]))
            continue

        better = []
        for p in picks:
            nm = p["name"]
            if nm == name or NOT_REP.search(nm.replace("_", " ").lower()):
                continue
            pw = (p["meta"].get("orig_w") or 0)
            if pw < args.min_new:
                continue
            if m and not F.must_ok(nm, m):
                continue
            better.append((pw, nm, p["meta"], p["tier"]))
        if not better:
            skipped.append((iso, cap, cur_w, "没有 >=%dpx 的替代" % args.min_new))
            continue
        better.sort(key=lambda x: -x[0])
        pw, nm, meta, tier = better[0]
        plans.append(dict(iso=iso, cap=cap, old=name, old_w=cur_w,
                          new=nm, new_w=pw, meta=meta, tier=tier,
                          why="原图仅 %dpx 宽，详情面板里是放大显示" % cur_w))

    print("\n" + "=" * 78)
    print("可以换的 %d 张：" % len(plans))
    for p in plans:
        print("  %-3s %-16s %4d → %5d  %s" % (p["iso"], p["cap"][:16], p["old_w"],
                                              p["new_w"], p["new"][:56]))
    print("\n不换的 %d 张：" % len(skipped))
    for iso, cap, w, why in skipped:
        print("  %-3s %-16s %4d  %s" % (iso, cap[:16], w, why))

    if not args.apply:
        print("\n（默认只出方案。确认后加 --apply 落盘；落盘前建议先看 contact sheet。）")
        with io.open(os.path.join(ROOT, "review", "lowres_plan.json"), "w",
                     encoding="utf-8", newline="\n") as f:
            json.dump([{k: p[k] for k in ("iso", "cap", "old", "old_w", "new", "new_w", "why")}
                       for p in plans], f, ensure_ascii=False, indent=1)
        print("方案已存 review/lowres_plan.json")
        return 0

    done = 0
    for p in plans:
        path, rec, err = load_rec(p["iso"])
        if err:
            print("  x %-3s %s" % (p["iso"], err))
            continue
        target, note, err = find_target(rec, p["cap"], "__never__")
        if not target:
            print("  x %-3s %s" % (p["iso"], err or note))
            continue
        if (target.get("title") or "").replace("File:", "") != p["old"]:
            print("  · %-3s 条目已变（%s），跳过" % (p["iso"], (target.get("title") or "")[:30]))
            continue
        ok, msg = do_swap(p["iso"], path, rec, target, p["new"], p["meta"], p["cap"], p["why"])
        if ok:
            print("  ✓ %-3s %-16s %4d→%-5d %s" % (p["iso"], p["cap"][:16], p["old_w"],
                                                  p["new_w"], msg))
            done += 1
        else:
            print("  x %-3s %s" % (p["iso"], msg))
        time.sleep(0.2)
    print("\n换掉 %d 张" % done)
    if done:
        print("下一步：python scripts/build_cards.py → upload_obs --only data → 传新增 assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
