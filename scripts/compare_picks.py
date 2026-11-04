# -*- coding: utf-8 -*-
"""只读对比：新评分规则会把多少张图换掉？（不下载、不写盘）

用途
----
决定"要不要全量重采"之前，先看清楚代价：
- 新规则下每处地标的第一名是什么；
- 和现在库里那张（title）是不是同一张；
- 当前那张是不是 `manual_repick`（人工换过的，重采会被覆盖掉）。

用法
----
    python scripts/compare_picks.py DE FR JP IN NG     # 指定国家
    python scripts/compare_picks.py --limit 20         # 随便挑 20 处地标试水
"""

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F            # noqa: E402

ROOT = F.ROOT
LANDMARKS = os.path.join(ROOT, "data", "landmarks.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("isos", nargs="*", help="ISO 码，如 DE FR JP")
    ap.add_argument("--limit", type=int, default=0, help="最多看多少处地标")
    args = ap.parse_args()

    conf = json.load(io.open(LANDMARKS, encoding="utf-8"))["countries"]
    isos = [x.upper() for x in args.isos] or sorted(conf.keys())
    rows = []
    for iso in isos:
        if iso not in conf:
            continue
        rec_path = os.path.join(ROOT, "data", "%s.json" % iso.lower())
        rec = {}
        if os.path.exists(rec_path):
            try:
                rec = json.load(io.open(rec_path, encoding="utf-8"))
            except Exception:
                rec = {}
        cur = {}
        for it in (rec.get("landmark_images") or []):
            cap = (it.get("caption_zh") or "").strip()
            cur[cap] = it
        for e in conf[iso]:
            picks, _err = F.ranked_picks(e)
            top = picks[0] if picks else None
            old = cur.get(e["zh"]) or {}
            old_title = (old.get("title") or "").replace("File:", "")
            same = bool(top) and old_title and top["name"] == old_title
            rows.append(dict(
                iso=iso, zh=e["zh"],
                new=top["name"] if top else "(无)",
                new_kind=top["kind"] if top else "",
                new_score="%.2f" % top["score"] if top else "",
                old=old_title, old_src=old.get("source") or "",
                same=same,
            ))
            if args.limit and len(rows) >= args.limit:
                break
        if args.limit and len(rows) >= args.limit:
            break

    changed = [r for r in rows if not r["same"]]
    protect = [r for r in changed if r["old_src"] == "manual_repick"]
    print("%-4s %-16s %-6s %-34s %s" % ("ISO", "地标", "变化", "新规则第一名", "备注"))
    print("-" * 100)
    for r in rows:
        flag = "  同" if r["same"] else "→ 换"
        note = "人工换过的会被覆盖" if r["old_src"] == "manual_repick" and not r["same"] else ""
        print("%-4s %-16s %-6s %-34s %s" % (r["iso"], r["zh"][:16], flag, r["new"][:34], note))
    print()
    print("共 %d 处；新规则会改 %d 处（%.0f%%），其中人工换过的 %d 处" % (
        len(rows), len(changed), 100.0 * len(changed) / max(len(rows), 1), len(protect)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
