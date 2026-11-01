#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 geo/c*.json 里的每个国家拆成单独文件 geo/n/{iso}.json。

为什么还要单国文件（分片不是已经能用了吗）
--------------------------------------------
分片最小也有 230KB。点一个国家时前端只要它那 5KB，却得下 230KB 再解 JSON：
限速 1.6Mbps 下实测"点下去 6.2 秒才出金线"，用户反馈过好几次。
单国文件的中位数只有 5KB，弱网下基本是即时的；分片仍保留作为兜底
（单国文件缺失时前端会退回分片）。

用法
----
    python scripts/split_n.py            # 从现有 geo/c*.json 拆
    python scripts/split_n.py --check    # 只统计，不写
"""
import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "geo", "n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    seen = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "geo", "c*.json"))):
        with open(path, encoding="utf-8") as f:
            chunk = json.load(f)
        for iso, obj in chunk.items():
            seen[str(iso).upper()] = obj

    if a.check:
        sizes = []
        for iso, obj in seen.items():
            sizes.append(len(json.dumps(obj, ensure_ascii=False,
                                        separators=(",", ":")).encode()))
        sizes.sort()
        print("可拆 %d 国  中位 %d B  最大 %d B"
              % (len(sizes), sizes[len(sizes) // 2], sizes[-1]))
        return 0

    total = 0
    for iso, obj in sorted(seen.items()):
        p = os.path.join(OUT, iso.lower() + ".json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        total += os.path.getsize(p)

    # 清掉已经不存在的国家（合并进父级之后留下的孤立文件）
    for p in glob.glob(os.path.join(OUT, "*.json")):
        iso = os.path.basename(p)[:-5].upper()
        if iso not in seen:
            os.remove(p)
            print("删掉 %s（已不在国家集合里）" % os.path.basename(p))

    print("写了 %d 个单国文件，合计 %.1f MB，目录 %s"
          % (len(seen), total / 1048576.0, os.path.relpath(OUT, ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
