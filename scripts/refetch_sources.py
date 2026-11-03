#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「没有本地原图」的地标图从维基媒体重新抓回来。

为什么需要这一步
----------------
压图脚本的原则是 `min(目标宽, 原图宽)` —— 绝不放大。所以想把 760 提到 1080，
手上必须有原图。207 个国家的原图还在本地，剩下 28 个（CN/FR/JP/US/DE/IN…）
早期跑过 --prune 被删掉了，只能回维基媒体重下。

为什么不直接用 requests/curl
----------------------------
当前环境的 HTTP 代理对 upload.wikimedia.org / thumb.wikimedia.org 是拦死的
（curl 一律 code=000 超时），但 Chromium 走的是直连，实测 200 能拿到图。
所以这里用 Playwright 驱动浏览器去下，而不是 urllib。

用法
----
    python scripts/refetch_sources.py            # 只补缺失的
    python scripts/refetch_sources.py --dry-run  # 只列清单
"""

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_W = 1600          # 下这个宽度，压到 1080 有余量


def iter_missing():
    """遍历所有档案，产出 (iso, im_meta, 目标文件路径)。"""
    for name in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if not name.endswith(".json") or name in ("index.json", "search.json"):
            continue
        path = os.path.join(ROOT, "data", name)
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            continue
        if not rec.get("iso_a2"):
            continue
        for im in rec.get("landmark_images") or []:
            rel = im.get("url") or ""
            if not rel:
                continue
            stem = os.path.splitext(os.path.join(ROOT, rel))[0]
            if any(os.path.exists(stem + e) for e in (".jpg", ".jpeg", ".png")):
                continue                      # 已有原图，不归这一步管
            yield rec["iso_a2"].upper(), im, stem


def widen(url):
    """把缩略图 URL 的宽度提到 TARGET_W；原图 URL 原样返回。"""
    url = url.split("?")[0]
    m = re.search(r"/(\d+)px-", url)
    if m and int(m.group(1)) < TARGET_W:
        return url[:m.start(1)] + str(TARGET_W) + url[m.end(1):]
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只下前 N 张，试跑用")
    a = ap.parse_args()

    todo = list(iter_missing())
    if a.limit:
        todo = todo[:a.limit]
    print("待补原图 %d 张" % len(todo))
    if a.dry_run:
        for iso, im, stem in todo[:15]:
            print("  %-3s %s" % (iso, widen(im.get("remote_url") or "")[:96]))
        return 0

    from playwright.sync_api import sync_playwright

    ok = bad = 0
    failed = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        for i, (iso, im, stem) in enumerate(todo, 1):
            url = widen(im.get("remote_url") or "")
            if not url.startswith("http"):
                bad += 1
                continue
            ext = ".png" if url.lower().endswith(".png") else ".jpg"
            dest = stem + ext
            try:
                r = pg.goto(url, wait_until="load", timeout=25000)
                if not r or r.status >= 400:
                    raise RuntimeError("HTTP %s" % (r.status if r else "?"))
                data = r.body()
                if len(data) < 4000:
                    raise RuntimeError("太小 %d B" % len(data))
                with open(dest, "wb") as f:
                    f.write(data)
                ok += 1
                print("  [%d/%d] %-3s %s  %.0f KB" % (i, len(todo), iso, os.path.basename(dest), len(data) / 1024))
            except Exception as e:
                bad += 1
                failed.append((iso, os.path.basename(dest), str(e)[:60]))
                print("  [%d/%d] %-3s 失败 %s" % (i, len(todo), iso, str(e)[:60]))
        b.close()

    print("\n成功 %d，失败 %d" % (ok, bad))
    for row in failed[:10]:
        print("  %s %s %s" % row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
