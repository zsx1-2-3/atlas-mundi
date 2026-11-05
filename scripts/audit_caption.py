# -*- coding: utf-8 -*-
"""只读体检：逐张判断「题注是否描述了画面」，并给出建议题注。

为什么需要这一步
----------------
题注是 `llm_from_commons_filename` 生成的（占 85%）—— 模型看着 Commons 文件名
写中文说明。文件名乐观时它是对的，文件名含糊或本身就错时，它就**编**：
卡塔尔那张工地照，文件名里带 "Burj Dubai"，题注就成了「多哈天际线中的迪拜塔」。

已有的视觉复核只问"这是不是那处地标"，没问"这句话配不配这张图"。
这里补上：拿图和题注一起送模型，判断是否相符，不符时给一句建议题注。

用法
----
    python scripts/audit_caption.py --sample 60          # 抽样体检
    python scripts/audit_caption.py --all --budget 2     # 全量（约 1 元）
    python scripts/audit_caption.py --all --fix         # 顺手改掉（需人工复核后）
"""

import argparse
import base64
import io
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F                       # noqa: E402
from envfile import load as load_env          # noqa: E402

load_env()
ROOT = F.ROOT
MODEL = "deepseek-v4-flash-vision-exp"
PRICE = {"peak": (3.0, 9.0), "idle": (1.5, 4.5)}
OUT = os.path.join(ROOT, "review", "caption_bad.json")

SYSTEM = (
    "你是图库的文字校对。用户给你一组「照片 + 它的中文题注」，逐张判断题注准不准确。\n"
    "准确的标准：题注描述的主体，就是画面里最主要的那个东西；允许概括，允许略去细节。\n"
    "不准确的情形：\n"
    "  · 画面里根本没有题注说的东西（说的是一栋楼，画面是一群人）；\n"
    "  · 题注把地点说错了（画面是别国的建筑）；\n"
    "  · 题注指向细节，而画面是全景，或反过来；\n"
    "  · 题注出现画面里没有的具体事物（画面没有塔，题注写塔）。\n"
    "不要因为「拍得不好看」「天气一般」「有人物点缀」判为不准确。\n"
    '只输出 json：{"items":[{"i":1,"ok":true,"why":"不超过14字","suggest":"不超过10字的题注"}]}'
)


def log(m=""):
    print(m, flush=True)


def money(tin, tout, peak):
    pin, pout = PRICE["peak" if peak else "idle"]
    return tin * pin / 1e6 + tout * pout / 1e6


def to_jpeg(path, size=384):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def judge(pairs):
    """pairs = [(caption, jpeg_bytes)]"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    parts = [{"type": "text", "text": "共 %d 组，逐组判断：" % len(pairs)}]
    for i, (cap, raw) in enumerate(pairs, 1):
        parts.append({"type": "text", "text": "第 %d 组，题注：「%s」" % (i, cap)})
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(raw).decode()}})
    payload = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": parts}],
               "temperature": 0, "max_tokens": 1200, "stream": False,
               "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"}}
    import urllib.request
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    u = d.get("usage") or {}
    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    got = {}
    try:
        for it in (json.loads(txt).get("items") or []):
            got[int(it.get("i") or 0)] = (bool(it.get("ok")),
                                          str(it.get("why") or "")[:20],
                                          str(it.get("suggest") or "")[:12])
    except Exception:
        pass
    return [got.get(i, (True, "无判定", "")) for i in range(1, len(pairs) + 1)], \
        u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--budget", type=float, default=2.0)
    ap.add_argument("--per", type=int, default=6)
    ap.add_argument("--fix", action="store_true", help="把不一致的题注改成建议值")
    args = ap.parse_args()

    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if len(fn) != 7 or not fn.endswith(".json"):
            continue
        iso = fn[:2].upper()
        try:
            rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        except Exception:
            continue
        for it in rec.get("landmark_images") or []:
            cap = it.get("caption_zh") or ""
            p = os.path.join(ROOT, (it.get("thumb_url") or "").replace("/", os.sep))
            if cap and os.path.exists(p):
                rows.append((iso, fn, cap, p, it))
    if args.sample:
        random.seed(5)
        rows = random.sample(rows, min(args.sample, len(rows)))
    log("待检 %d 张（per=%d，预算 %.1f 元）" % (len(rows), args.per, args.budget))

    peak = 10 <= time.localtime().tm_hour < 18
    used, calls, bad = 0.0, 0, []
    for i in range(0, len(rows), args.per):
        chunk = rows[i:i + args.per]
        try:
            res, tin, tout = judge([(c, to_jpeg(p)) for _iso, _fn, c, p, _it in chunk])
        except Exception as e:
            log("  [!] 请求失败 %s" % str(e)[:60])
            continue
        used += money(tin, tout, peak)
        calls += 1
        for (iso, fn, cap, p, it), (ok, why, sug) in zip(chunk, res):
            if not ok:
                bad.append(dict(iso=iso, file=fn, cap=cap, why=why, suggest=sug,
                                url=it.get("url"), title=it.get("title")))
        if args.fix:
            touched = False
            for (iso, fn, cap, p, it), (ok, why, sug) in zip(chunk, res):
                if not ok and sug:
                    it["caption_zh"] = sug
                    it["caption_zh_source"] = "vision_suggest"
                    touched = True
            if touched:
                isos = {c[0] for c, r in zip(chunk, res) if not r[0]}
                for iso in isos:
                    path = os.path.join(ROOT, "data", iso.lower() + ".json")
                    rec = json.load(io.open(path, encoding="utf-8"))
                    for it in rec.get("landmark_images") or []:
                        for (i2, _f, c2, _p, it2) in chunk:
                            if i2 == iso and it2.get("title") == it.get("title"):
                                it["caption_zh"] = it2["caption_zh"]
                    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                        json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
        if used >= args.budget:
            log("  预算到，停在第 %d 张" % min(i + args.per, len(rows)))
            break
        if calls % 5 == 0:
            log("  … %d/%d  已花 %.3f 元  不一致 %d" % (min(i + args.per, len(rows)), len(rows), used, len(bad)))

    log("\n检查 %d 张，**文图不符 %d 张（%.1f%%）**，花费 %.3f 元"
        % (min(len(rows), calls * args.per), len(bad), 100.0 * len(bad) / max(1, min(len(rows), calls * args.per)), used))
    for b in bad[:40]:
        log("  %-3s %-20s %s  →  建议：%s" % (b["iso"], b["cap"][:20], b["why"], b["suggest"]))
    json.dump(bad, io.open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    log("清单：review/caption_bad.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
