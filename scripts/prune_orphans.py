# -*- coding: utf-8 -*-
"""分批清理换图留下的孤儿文件（沙箱单轮批量删除有上限，所以默认一次只删 30 个）。

verify_images.py 换图时不再当场删旧图，只登记到 review/orphan_assets.txt；
这个脚本负责安全地清。

用法
----
    python scripts/prune_orphans.py            # 删前 30 个
    python scripts/prune_orphans.py --dry-run  # 只看不删
    python scripts/prune_orphans.py --n 20
"""

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "review", "orphan_assets.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(LIST):
        print("没有待清理清单（%s）" % os.path.relpath(LIST, ROOT))
        return 0
    rows = [x.strip() for x in io.open(LIST, encoding="utf-8") if x.strip()]
    rows = list(dict.fromkeys(rows))          # 去重，保序
    print("清单 %d 个（去重后）" % len(rows))

    batch, rest, gone = rows[:args.n], rows[args.n:], 0
    for rel in batch:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if args.dry_run:
            print("  would rm", rel)
            continue
        try:
            if os.path.exists(p):
                os.remove(p)
                gone += 1
        except OSError as e:
            print("  x", rel, e)

    if not args.dry_run:
        with io.open(LIST, "w", encoding="utf-8", newline="\n") as f:
            for rel in rest:
                f.write(rel + "\n")
        print("删了 %d 个，剩 %d 个待清" % (gone, len(rest)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
