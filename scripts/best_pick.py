# -*- coding: utf-8 -*-
"""从候选里挑「最好看」的那张，而不是「第一张能用」的。

为什么需要它
------------
`verify_images` 的判定是二值的：是不是这处地标。它放行的图里会出现
风力发电机冒充哈利法塔、疣猪冒充国家公园、舷窗挡掉三分之一个环礁 ——
技术上"对"，画册上不能用。

这里换一个判据：**打分**。一次把同一处地标的 5~6 个候选一起发过去，
按「主体明确 / 构图 / 无人无关杂物 / 可作代表图」打 1~5 分，取最高分，
4 分以下不换。

用法
----
    python scripts/best_pick.py --list review/todo_best.json --dry-run
    python scripts/best_pick.py --list review/todo_best.json --apply
"""

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F                        # noqa: E402
from envfile import load as load_env           # noqa: E402
from replace_image import NOT_REP, do_swap, load_rec, find_target   # noqa: E402

load_env()      # 读 .env 里的 DEEPSEEK_API_KEY（不加载就是 401）

ROOT = F.ROOT
MODEL = "deepseek-v4-flash-vision-exp"
PRICE = {"peak": (3.0, 9.0), "idle": (1.5, 4.5)}
TMP = os.path.join(ROOT, "review", "bestpick")
MIN_SCORE = 4

SYSTEM = (
    "你是旅行画册的图片编辑，手里有同一处地标的多张候选照片，要选一张能上画册的。\n"
    "逐张按 1~5 打分：\n"
    "5 = 可以直接当封面：主体明确且占画面主要位置、构图舒展、光线好、画面干净；\n"
    "4 = 很适合做这处地标的代表图；\n"
    "3 = 能认出是这处地标，但构图/光线一般，或画面里有明显人物、杂物、局部裁切；\n"
    "2 = 勉强能认出，主体被遮挡、太小，或只是某个局部；\n"
    "1 = 认不出，或主体根本不是这处地标（地图、绘画、书页、卫星图、动物特写、别人家建筑）。\n"
    "扣分项：主体只占角落；有人物特写或合影；有舷窗/栏杆/树枝遮挡；\n"
    "画面是施工、垃圾、停车场、码头装卸；是博物馆展厅或室内展陈。\n"
    "自然类地标（岛、环礁、山、湖、瀑布、保护区）允许出现生态细节，但主体必须是地貌本身。\n"
    "只输出 json：{\"items\":[{\"i\":1,\"score\":5,\"why\":\"不超过12字\"}]}"
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


def score_batch(country, cap, blobs):
    """blobs = [jpeg bytes]，返回 ([(score, why)], tin, tout)。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    parts = [{"type": "text", "text": "国家/地区：%s。地标：%s。共 %d 张候选：" % (country, cap, len(blobs))}]
    for i, raw in enumerate(blobs, 1):
        parts.append({"type": "text", "text": "第 %d 张" % i})
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(raw).decode()}})
    payload = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": parts}],
               "temperature": 0, "max_tokens": 900, "stream": False,
               "thinking": {"type": "disabled"},
               "response_format": {"type": "json_object"}}
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
            got[int(it.get("i") or 0)] = (int(it.get("score") or 0), str(it.get("why") or "")[:14])
    except Exception:
        pass
    return [got.get(i, (0, "无判定")) for i in range(1, len(blobs) + 1)], \
           u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", default="review/todo_best.json")
    ap.add_argument("--per", type=int, default=6, help="每处地标一次看几张")
    ap.add_argument("--max", type=int, default=10, help="每处地标最多试几个候选")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--budget", type=float, default=2.0)
    args = ap.parse_args()

    todo = json.load(io.open(args.list, encoding="utf-8"))
    os.makedirs(TMP, exist_ok=True)
    used = 0.0
    peak = 10 <= time.localtime().tm_hour < 12
    log("待办 %d 处，预算 %.1f 元\n" % (len(todo), args.budget))

    for job in todo:
        iso, cap = job["iso"].upper(), job["cap"]
        path, rec, err = load_rec(iso)
        if err:
            log("  x %-3s %s" % (iso, err)); continue
        target, note, err = find_target(rec, cap, "__never__")
        if not target:
            log("  x %-3s %s" % (iso, err or note)); continue
        kw = job.get("kw") or target.get("kw") or cap
        entry = {"zh": cap, "kw": kw}
        if job.get("must"):
            entry["must"] = job["must"]

        picks, _e = F.ranked_picks(entry)
        cands = []
        for p in picks:
            nm = p["name"]
            if NOT_REP.search(nm.replace("_", " ").lower()):
                continue
            if (p["meta"].get("orig_w") or 0) < 1200:
                continue
            if nm == (target.get("title") or "").replace("File:", ""):
                continue
            cands.append(p)
            if len(cands) >= args.max:
                break
        if not cands:
            log("  x %-3s %-14s 没有 >=1200px 的候选" % (iso, cap[:14])); continue

        best, best_sc, tried = None, 0, 0
        for i in range(0, len(cands), args.per):
            chunk = cands[i:i + args.per]
            blobs, names = [], []
            for p in chunk:
                dst = os.path.join(TMP, "%s_%02d.jpg" % (iso.lower(), tried))
                try:
                    F.C.download(p["meta"]["remote_url"], dst)
                    blobs.append(to_jpeg(dst))
                    names.append(p)
                    tried += 1
                except Exception as e:
                    log("      ! 下载失败 %s" % str(e)[:40])
            if not blobs:
                continue
            res, tin, tout = score_batch(rec.get("name_zh") or iso, cap, blobs)
            used += money(tin, tout, peak)
            for p, (sc, why) in zip(names, res):
                if sc > best_sc:
                    best, best_sc = p, sc
                log("      %d 分  %-50s %s" % (sc, p["name"][:50], why))
            if best_sc >= 5 or used >= args.budget:
                break
        if not best or best_sc < MIN_SCORE:
            log("  x %-3s %-14s 最高分 %d，不够 %d，不换" % (iso, cap[:14], best_sc, MIN_SCORE))
            continue
        log("  → %-3s %-14s 选 %d 分：%s" % (iso, cap[:14], best_sc, best["name"][:56]))
        if args.apply:
            # newcap：换图时顺手改题注（例如"1741年海战油画"这格改成"卡塔赫纳老城"，
            # 或者美国那格把"夏威夷钻石头山"整个换成"黄石国家公园大棱镜泉"）
            ok, msg = do_swap(iso, path, rec, target, best["name"], best["meta"],
                              job.get("newcap") or cap,
                              job.get("why") or "原图不合格，按画质评分重选（%d 分）" % best_sc)
            log("     %s %s" % ("✓" if ok else "x", msg))
        time.sleep(0.2)

    log("\n花费约 %.3f 元" % used)
    if not args.apply:
        log("（预演，未落盘。确认后加 --apply）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
