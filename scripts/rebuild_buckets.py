# -*- coding: utf-8 -*-
"""把 geo/c*.json 重新分桶：从 8 个桶（325~833KB）改成 16 个均衡桶（~250KB）。

为什么重新分桶
--------------
点一个国家时前端只拉它所在的那一个桶。原来桶大且极不均衡：
俄罗斯+加拿大有时候落在一起，单个桶 833KB，弱网下就是「卡片出来了、
金线还在路上」——用户明确反馈过这个。

哈希分桶没法保证均衡（FNV 死算了一遍，16 桶最大还是 947KB，因为
RU/CA/AQ 这种巨无霸随机撞在一起）。所以改成**按体积贪心**：
国家按字节数从大到小排，每个丢进当前最轻的那个桶。这样 16 个桶
最大 ~260KB，点一下大约 1/3 的等待时间。

前端怎么知道每个国家在哪个桶
----------------------------
不能再用 `hash % N`（贪心分配不是哈希）。这里额外产出一张分组表，
按桶排列、桶内用逗号分隔，桶之间用 `|` 隔开，直接内联进 index.html：
    UPGRADE|KE,FR,GY|...
整张表 700 多字符，比一次 HTTP 往返便宜得多。

用法
----
    python scripts/rebuild_buckets.py            # 只打印，不写文件
    python scripts/rebuild_buckets.py --write    # 写 geo/c0..c15.json + 打印表
"""
import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_BUCKETS = 16


def load_all():
    """把现有分片合回 {ISO: 国家对象}，顺便记下每个国家的字节数。"""
    countries, sizes = {}, {}
    for path in sorted(glob.glob(os.path.join(ROOT, "geo", "c*.json"))):
        with open(path, encoding="utf-8") as f:
            chunk = json.load(f)
        for iso, obj in chunk.items():
            key = str(iso).upper()
            countries[key] = obj
            sizes[key] = len(json.dumps(obj, ensure_ascii=False,
                                        separators=(",", ":")).encode())
    return countries, sizes


def balance(sizes, n):
    """按体积贪心：大的先进，每次进当前最轻的桶。"""
    buckets = [{"iso": [], "bytes": 0} for _ in range(n)]
    for iso in sorted(sizes, key=lambda k: -sizes[k]):
        tgt = min(buckets, key=lambda b: b["bytes"])
        tgt["iso"].append(iso)
        tgt["bytes"] += sizes[iso]
    return buckets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真的写文件")
    a = ap.parse_args()

    countries, sizes = load_all()
    total = sum(sizes.values())
    print("国家 %d 个，共 %.1f MB" % (len(countries), total / 1048576))

    buckets = balance(sizes, N_BUCKETS)
    for i, b in enumerate(buckets):
        print("  c%-2d %4d 国  %5.0f KB" % (i, len(b["iso"]), b["bytes"] / 1024))

    table = "|".join(",".join(sorted(b["iso"])) for b in buckets)
    print("\n内联表（贴进 index.html 的 GEO_GROUPS）：")
    print(table)
    print("\n长度 %d 字符" % len(table))

    if not a.write:
        return

    for i, b in enumerate(buckets):
        # 桶内按 ISO 排序，diff 时好读
        chunk = {iso: countries[iso] for iso in sorted(b["iso"])}
        path = os.path.join(ROOT, "geo", "c%d.json" % i)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(chunk, f, ensure_ascii=False, separators=(",", ":"))
        print("写了 %s（%.0f KB）" % (os.path.relpath(path, ROOT),
                                    os.path.getsize(path) / 1024))
    # 旧的多余分片清掉
    for path in sorted(glob.glob(os.path.join(ROOT, "geo", "c*.json"))):
        idx = int(os.path.basename(path)[1:].split(".")[0])
        if idx >= N_BUCKETS:
            os.remove(path)
            print("删掉旧分片", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()
