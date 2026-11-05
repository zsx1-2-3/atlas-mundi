# -*- coding: utf-8 -*-
"""构建"候选级"标注集：训练和评测都需要它，而我们之前把它删没了。

流程（每个地标一次）：
  1. 走真实候选池 ranked_picks → 取前 K 个候选（不预筛，保留真实噪声）
  2. 下载到 review/ds/img/（长边压到 512，够 CLIP 也够视觉判定，省磁盘）
  3. 视觉模型批量判定 → 这是标签（代理真值）
  4. 落盘 review/ds/labels.json：图片路径 + 元数据 + 标签

为什么不在"已入库图片"上做：入库的图是选完的，负样本早被清掉了（实测只剩 1.2%）。
只有候选级数据里才有真实的错图比例，才有得训、有得评。

用法：
    python scripts/build_gate_dataset.py --entries 150 --per 8
"""
import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from envfile import load        # noqa: E402
load()
import fill_images as F         # noqa: E402
import verify_images as V       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = os.path.join(ROOT, "review", "ds")
IMG = os.path.join(DS, "img")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=150)
    ap.add_argument("--per", type=int, default=8)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--long", type=int, default=512, help="落盘长边，给 CLIP 和判定够用")
    ap.add_argument("--resume", action="store_true", help="沿用已有 labels.json，只补没判过的")
    args = ap.parse_args()

    os.makedirs(IMG, exist_ok=True)
    out_json = os.path.join(DS, "labels.json")
    done = {}
    if args.resume and os.path.exists(out_json):
        try:
            for it in json.load(io.open(out_json, encoding="utf-8")):
                done[(it["iso"], it["name"])] = it
        except Exception:
            done = {}
        print("续跑：已有 %d 条" % len(done))

    conf = json.load(io.open(os.path.join(ROOT, "data", "landmarks.json"), encoding="utf-8"))
    pool = [(iso, e) for iso, ents in (conf.get("countries") or {}).items()
            for e in ents if e.get("kw")]
    random.seed(args.seed)
    random.shuffle(pool)
    sample = pool[:args.entries]
    print("抽 %d 个地标，每个取 %d 个候选\n" % (len(sample), args.per))

    from PIL import Image
    rows = list(done.values())
    have = set(done)
    def prepare(item):
        """取候选 + 下载转码，纯 IO，适合并行。"""
        iso, e = item
        zh = e.get("zh") or ""
        try:
            picks, _err = F.ranked_picks(e)
        except Exception as ex:
            return iso, zh, [], "picks: " + str(ex)[:30]
        got = []
        for c in picks[:args.per]:
            if (iso, c["name"]) in have:
                continue
            meta = c.get("meta") or {}
            uri = meta.get("remote_url")
            if not uri:
                continue
            h = hashlib.md5((iso + c["name"]).encode("utf-8")).hexdigest()[:16]
            dst = os.path.join(IMG, h + ".jpg")
            try:
                raw = F.C.http_get(uri, timeout=120, retries=2)
                if len(raw) < 1024:
                    raise RuntimeError("文件过小 %d 字节" % len(raw))
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((args.long, args.long), Image.LANCZOS)
                im.save(dst, "JPEG", quality=88)
            except Exception as ex:
                print("   下载/转码失败", c["name"][:32], str(ex)[:26])
                continue
            got.append({
                "iso": iso, "zh": zh, "kw": e.get("kw") or "",
                "name": c["name"], "tier": c.get("tier"),
                "w": meta.get("orig_w") or 0, "h": meta.get("orig_h") or 0,
                "file": os.path.relpath(dst, ROOT).replace("\\", "/"),
            })
        return iso, zh, got, ""

    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        prepared = list(pool.map(prepare, sample))
    print("并行取候选完成，开始判定\n")

    for n, (iso, zh, got, err) in enumerate(prepared, 1):
        if err or not got:
            if err:
                print("  x", iso, zh[:12], err)
            continue
        batch = got
        # 一次请求判完这一批
        pairs = [(zh, io.open(os.path.join(ROOT, g["file"]), "rb").read()) for g in got]
        try:
            res, in_tok, out_tok = V.judge(zh, pairs)
        except Exception as ex:
            print("  判定失败", iso, str(ex)[:36])
            continue
        for g, (ok, why) in zip(got, res):
            g["label"] = 1 if ok else 0        # 1 = 对，0 = 错
            g["why"] = (why or "")[:48]
            rows.append(g)
        neg = sum(1 for g in got if g["label"] == 0)
        print("%3d/%d %-3s %-16s 候选 %d，判错 %d" % (n, len(sample), iso, zh[:16], len(got), neg))
        json.dump(rows, io.open(out_json, "w", encoding="utf-8", newline="\n"),
                  ensure_ascii=False, indent=1)

    n = len(rows)
    neg = sum(1 for r in rows if r["label"] == 0)
    print("\n共 %d 张候选，判错 %d 张（%.1f%%）→ %s" % (n, neg, 100.0 * neg / max(1, n), out_json))


if __name__ == "__main__":
    main()
