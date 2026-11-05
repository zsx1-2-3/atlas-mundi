# -*- coding: utf-8 -*-
"""把 CLIP 接进生产：对已入库图片算相似度，只重排可疑的那批。

为什么不重新采集全库：
  已经入库的 1074 张里，98.8% 是对的。全部重爬要下几万张候选，性价比极低。
  实测 CLIP 重排能基准上把首图错误率从 24% 降到 7%，那就只治"高分位可疑"的那批。

两段式（这是关键设计）：
  ① 免费段：对本地已有的 webp 算 CLIP 相似度（0 元、0.1s/张），按分排序
  ② 花钱段：只对最低分的那批去查候选（下载 top-K 缩略图 → CLIP 重排 → 更好就换）

用法：
    python scripts/clip_rerank.py                      # 只算分、出报告，不动数据
    python scripts/clip_rerank.py --flag 150           # 指定要重排多少张最可疑的
    python scripts/clip_rerank.py --flag 150 --apply   # 真换图
"""
import argparse
import io
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from envfile import load        # noqa: E402
load()
import fill_images as F         # noqa: E402
import replace_image as R                            # noqa: E402
import verify_images as V                             # noqa: E402
from replace_image import do_swap, load_rec, find_target     # noqa: E402

# 换图会删掉旧的 webp/jpg。沙箱每轮删满 50 个文件就掐断进程（已经踩了三次），
# 这里直接把删除关掉，留下的孤儿用 scripts/prune_orphans.py 分批清。
R.os.remove = lambda *a, **k: None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "openai/clip-vit-base-patch32"
_MODEL = None
_PROC = None


def judge_bytes(text, raw, size=384):
    """原始图片字节 → (是否合格, 理由)。复用 verify_images.judge 的提示词。"""
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    res, _a, _b = V.judge(text, [(text, buf.getvalue())])
    ok, why = res[0]
    return bool(ok), (why or "")


def clip():
    """懒加载，只加载一次（151M 参数，CPU 约 3 秒）。"""
    global _MODEL, _PROC
    if _MODEL is None:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        _MODEL = CLIPModel.from_pretrained(MODEL)
        _PROC = CLIPProcessor.from_pretrained(MODEL)
        _MODEL.eval()
        torch.set_grad_enabled(False)
    return _MODEL, _PROC


def encode(images, texts, batch=12):
    """一批 (PIL 图, 文本) → 余弦相似度列表。"""
    import torch
    from PIL import Image
    m, p = clip()
    out = []
    for i in range(0, len(images), batch):
        ims = []
        for im in images[i:i + batch]:
            ims.append(im if im is not None else Image.new("RGB", (224, 224)))
        inp = p(text=texts[i:i + batch], images=ims, return_tensors="pt",
                padding=True, truncation=True)
        o = m(**inp)
        ie = o.image_embeds / o.image_embeds.norm(dim=-1, keepdim=True)
        te = o.text_embeds / o.text_embeds.norm(dim=-1, keepdim=True)
        out.extend((ie * te).sum(dim=-1).tolist())
    return out


_KW = {}


def kw_map():
    """iso+题注 → 英文检索词。库里不少条目 kw 是空的，
    直接拿中文题注去喂 CLIP 的英文文本编码器会得到虚低分
    （实测尼亚加拉瀑布、自由女神、哈利法塔都因此掉到 0.16）。"""
    if _KW:
        return _KW
    conf = json.load(io.open(os.path.join(ROOT, "data", "landmarks.json"), encoding="utf-8"))
    for iso, ents in (conf.get("countries") or {}).items():
        for e in ents:
            if e.get("zh"):
                _KW[(iso, e["zh"])] = (e.get("kw") or "").strip()
    return _KW


def text_for(it, iso=""):
    kw = (it.get("kw") or "").strip()
    if not kw:
        kw = kw_map().get((iso, it.get("caption_zh") or ""), "")
    if not kw:
        kw = it.get("caption_zh") or ""
    return "a photo of " + kw


def stage1():
    """给库里每张图算 CLIP，返回 [(iso, 图条目, 分数)]，按分数升序。"""
    from PIL import Image
    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if len(fn) != 7 or not fn.endswith(".json"):
            continue
        iso = fn[:2].upper()
        rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        for it in rec.get("landmark_images") or []:
            p = os.path.join(ROOT, it.get("url") or "")
            if not os.path.exists(p):
                continue
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((448, 448), Image.LANCZOS)
            except Exception:
                continue
            rows.append((iso, it, im))
    print("本地图 %d 张，开始算 CLIP…" % len(rows))
    sims = []
    B = 24
    for i in range(0, len(rows), B):
        chunk = rows[i:i + B]
        sims.extend(encode([c[2] for c in chunk], [text_for(c[1], c[0]) for c in chunk]))
        if (i // B) % 10 == 0:
            print("   %d/%d" % (min(i + B, len(rows)), len(rows)))
    out = [(iso, it, float(s)) for (iso, it, _im), s in zip(rows, sims)]
    out.sort(key=lambda x: x[2])
    json.dump([{"iso": a, "cap": b.get("caption_zh"), "title": b.get("title"),
                "kw": b.get("kw"), "clip": c} for a, b, c in out],
              io.open(os.path.join(ROOT, "review", "clip_scores.json"), "w",
                      encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flag", type=int, default=0, help="重排分数最低的多少张（0=只出报告）")
    ap.add_argument("--per", type=int, default=8, help="每个地标查几个候选")
    ap.add_argument("--apply", action="store_true", help="真换图（默认只预演）")
    ap.add_argument("--reuse", action="store_true", help="复用上次算好的分数，不重算")
    ap.add_argument("--margin", type=float, default=0.02,
                    help="新图分数要比现图高多少才换（防止噪声翻来翻去）")
    args = ap.parse_args()

    if args.reuse and os.path.exists(os.path.join(ROOT, "review", "clip_scores.json")):
        # 复用：把分数读回来，再按 iso+标题对齐到 data 里的条目
        cached = json.load(io.open(os.path.join(ROOT, "review", "clip_scores.json"), encoding="utf-8"))
        idx = {}
        for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
            if len(fn) != 7 or not fn.endswith(".json"):
                continue
            iso = fn[:2].upper()
            rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
            for it in rec.get("landmark_images") or []:
                idx[(iso, it.get("title"))] = it
        rows = []
        for c in cached:
            it = idx.get((c["iso"], c["title"]))
            if it:
                rows.append((c["iso"], it, float(c["clip"])))
        rows.sort(key=lambda x: x[2])
        print("复用 %d 条分数" % len(rows))
    else:
        rows = stage1()

    # 文本里没有拉丁字母的（检索词缺失、只剩中文题注）分数不可信，不参与重排
    def text_ok(it, iso):
        t = text_for(it, iso)
        return any(ch.isascii() and ch.isalpha() for ch in t)

    skipped = [r for r in rows if not text_ok(r[1], r[0])]
    rows = [r for r in rows if text_ok(r[1], r[0])]
    print("排除 %d 条（检索词缺失，CLIP 分不可信）" % len(skipped))
    sims = np.array([r[2] for r in rows])
    print("\nCLIP 分数分布：min %.3f  p5 %.3f  p10 %.3f  中位 %.3f  max %.3f"
          % (sims.min(), np.percentile(sims, 5), np.percentile(sims, 10),
             np.median(sims), sims.max()))
    print("最低 10 张：")
    for iso, it, s in rows[:10]:
        print("   %.3f  %-3s %-24s %s" % (s, iso, (it.get("caption_zh") or "")[:24],
                                          (it.get("title") or "")[:34]))
    if not args.flag:
        print("\n只出报告。要重排加 --flag N（建议 120~200），再加 --apply 才会改数据。")
        return

    targets = rows[:args.flag]
    print("\n开始重排最低分的 %d 张…" % len(targets))
    changed = same = 0
    for n, (iso, it, cur) in enumerate(targets, 1):
        kw = it.get("kw") or ""
        if not kw:
            continue
        entry = {"zh": it.get("caption_zh") or "", "kw": kw,
                 "must": (it.get("kw") or "").split()[0].lower() if kw else ""}
        try:
            picks, _e = F.ranked_picks(entry)
        except Exception as ex:
            print("  x %-3s %s" % (iso, str(ex)[:40]))
            continue
        cands = picks[:args.per]
        if not cands:
            continue
        # 下载缩略图算 CLIP（内存解码，不落临时文件）
        from PIL import Image
        ims, keep = [], []
        for c in cands:
            uri = (c.get("meta") or {}).get("remote_url")
            if not uri:
                continue
            try:
                raw = F.C.http_get(uri, timeout=90, retries=1)
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((448, 448), Image.LANCZOS)
                ims.append(im)
                keep.append(c)
            except Exception:
                continue
        if not ims:
            continue
        sims2 = encode(ims, [text_for(it, iso)] * len(ims))
        order = np.argsort(sims2)[::-1]
        best = best_s = None
        # CLIP 只看"内容像不像"，不看"能不能用"：实测它会把黑白老照片、
        # 抱海星的人物照、地质示意图排到第一。所以第一名必须过视觉复核，
        # 不合格就顺延到第二名，最多试 3 个。
        for j in order[:3]:
            c = keep[int(j)]
            uri = (c.get("meta") or {}).get("remote_url")
            try:
                raw = F.C.http_get(uri, timeout=90, retries=1)
                ok, why = judge_bytes(it.get("caption_zh") or entry["zh"], raw)
            except Exception as ex:
                print("   复核失败", c["name"][:28], str(ex)[:26])
                continue
            if ok:
                best, best_s = c, float(sims2[int(j)])
                break
            print("   CLIP 首选不合格（%s），顺延：%s"
                  % (why or "视觉否", c["name"][:34]))
        if best is None:
            print("%3d/%d %-3s 候选全被视觉否掉，保持" % (n, len(targets), iso))
            continue
        if best_s <= cur + args.margin:
            same += 1
            print("%3d/%d %-3s 保持（现 %.3f，最好候选 %.3f）"
                  % (n, len(targets), iso, cur, best_s))
            continue
        print("%3d/%d %-3s 换图：%.3f → %.3f  %s"
              % (n, len(targets), iso, cur, best_s, best["name"][:44]))
        if args.apply:
            path, rec, err = load_rec(iso)
            if err:
                print("    x", err)
                continue
            target, note, e2 = find_target(rec, it.get("caption_zh") or "",
                                           it.get("caption_zh") or "")
            if not target:
                print("    x 找不到条目", note or e2)
                continue
            ok, msg = do_swap(iso, path, rec, target, best["name"], best["meta"],
                              it.get("caption_zh") or "",
                              "CLIP 重排：相似度 %.3f → %.3f" % (cur, best_s))
            if ok:
                changed += 1
            else:
                print("    x", msg)
    print("\n完成：换 %d 张，保持 %d 张%s"
          % (changed, same, "" if args.apply else "（预演，未落盘）"))


if __name__ == "__main__":
    main()
