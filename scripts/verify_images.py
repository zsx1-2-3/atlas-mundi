#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""视觉复核：让多模态模型逐张看缩略图，收掉"合规但不对"的图，并从候选池补齐。

为什么需要独立这一步
--------------------
规则筛得掉尺寸不对、比例不对、文件名像地图的，筛不掉"画面上根本不是这处地标"。
实测基里巴斯的 P18 给的是 Sentinel-2 卫星照、NASA 地球观测照、NGDC 三维海底地形图
—— 尺寸、比例、文件名全合规。冰岛没事，小国全中。

**这一步真的在看图**，所以能判"这张主体是不是这处地标"，规则做不到。

收敛与预算（这是重点）
----------------------
四重保险，保证不会无限烧钱：

1. **候选池有限**。一个地标最多 26 个候选，过了尺寸/分类闸门通常只剩 2~5 个。
   换完就没了 —— 天然的循环下界，不可能"一直换下去"。
2. **结果缓存** `data/_vision_cache.json`，以文件名做 key。同一张图**永不重复验**，
   第二轮只花"新换进来那几张"的钱。
3. **金额硬闸门** `--budget`（默认 3 元）。累计花费一超就停，打印剩余缺口。
4. **轮数 + 收敛** `--max-rounds`（默认 2），本轮没有任何改动就提前退出。

于是总成本有上界：223 国 × 5 处 × 平均 3 个可用候选 ≈ 3300 张 × 0.00093 元 ≈ 3.1 元。

用法
----
    python scripts/verify_images.py --all                  # 全量，默认 2 轮
    python scripts/verify_images.py --only IS KI BT        # 先看几个
    python scripts/verify_images.py --all --budget 1.5     # 只肯花 1.5 元
    python scripts/verify_images.py --oracle               # 只判定不替换（体检）

之后：python scripts/optimize_images.py --all && python scripts/sync_index.py
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from envfile import load as load_env                      # noqa: E402
import fill_images as F                                   # noqa: E402

CACHE = os.path.join(ROOT, "data", "_vision_cache.json")
TRIED = os.path.join(ROOT, "data", "_img_tried.json")
LEDGER = os.path.join(ROOT, "logs", "vision_calls.jsonl")

MODEL = "deepseek-v4-flash-vision-exp"
# 官方价，元 / 百万 tokens（高峰 9:00-12:00、14:00-18:00，其余半价）
PRICE = {"peak": (3.0, 9.0), "idle": (1.5, 4.5)}
THUMB = 384          # 传进去的最长边。官方按尺寸折算 token，单张封顶 384
MAX_TRIES = 3        # 补齐时一个地标最多连试几个候选（试完就记"没图"，不再花钱）
NATURAL = ("保护区", "国家公园", "群岛", "环礁", "岛", "湖", "山", "瀑布", "冰川",
           "峡湾", "沙漠", "海岸", "海湾", "火山", "峰", "河", "谷", "草原", "雨林")

SYSTEM = (
    "你是旅行百科的图片审核员。用户给出一个国家、一组按顺序编号的地标名和照片，"
    "逐张判断：这张照片的主体，是不是对应该国家的那处地标？\n"
    "合格：主体清晰可辨，确实是那处自然景观 / 古迹 / 城市地标。"
    "角度不好、天气一般、拍得不好看都不算不合格。\n"
    "不合格：卫星影像、地图、定位图、示意图、图表、书页扫描件、封面、历史档案图版；"
    "油画、版画、水彩、素描，以及壁画/浮雕的复制品（除非这处地标本身就是壁画或岩画）；"
    "普通办公楼 / 机场 / 仓库 / 学校 / 医院 / 码头装卸；人物合影、工艺品、招牌；"
    "警示牌 / 解说牌 / 纪念碑铭牌（牌面上的文字不是地标本身）；"
    "主体明显是别的地方。\n"
    "自然类地标（岛、环礁、保护区、山、湖、瀑布、冰川）：其生态细节"
    "（珊瑚礁、野生动物、植被）算合格。\n"
    "只输出 json：{\"items\":[{\"i\":1,\"ok\":true,\"why\":\"不超过15字\"}]}"
)


def log(msg=""):
    print(msg, flush=True)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def is_peak():
    h = datetime.now().hour
    return 9 <= h < 12 or 14 <= h < 18


def money(tokens_in, tokens_out):
    pin, pout = PRICE["peak" if is_peak() else "idle"]
    return tokens_in * pin / 1e6 + tokens_out * pout / 1e6


def to_jpeg(path, size=THUMB):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


class Budget:
    def __init__(self, cap):
        self.cap = cap
        self.used = 0.0
        self.tin = 0
        self.tout = 0
        self.calls = 0

    def add(self, tin, tout):
        self.tin += tin
        self.tout += tout
        self.calls += 1
        self.used += money(tin, tout)

    def left(self):
        return max(0.0, self.cap - self.used)

    def dry(self):
        return self.cap > 0 and self.used >= self.cap


def judge(country, pairs):
    """pairs = [(caption, jpg_bytes)]。返回 [(ok, why)]。
    一次请求判完一批，比逐张调用省下每次的 prompt 开销。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    parts = [{"type": "text", "text": "国家：%s。共 %d 张照片：" % (country, len(pairs))}]
    for i, (cap, raw) in enumerate(pairs, 1):
        parts.append({"type": "text", "text": "第 %d 张，地标：%s" % (i, cap)})
        parts.append({"type": "image_url",
                      "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(raw).decode()}})
    payload = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": parts}],
        "temperature": 0, "max_tokens": 900, "stream": False,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    u = d.get("usage") or {}
    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    items = {}
    try:
        for it in (json.loads(txt).get("items") or []):
            items[int(it.get("i") or 0)] = (bool(it.get("ok")), str(it.get("why") or "")[:40])
    except Exception:
        pass
    out = []
    for i in range(1, len(pairs) + 1):
        out.append(items.get(i, (True, "模型未给出判定，按合格处理")))
    return out, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def entry_of(conf, iso, kw):
    for e in (conf.get("countries") or {}).get(iso) or []:
        if (e.get("kw") or "").strip().lower() == (kw or "").strip().lower():
            return e
    return None


def next_slot(imgs):
    """下一个没被占用的 Lnn 编号 —— 中间可能有空位，不能用 len(imgs)+1。"""
    used = set()
    for x in imgs:
        b = os.path.splitext(os.path.basename(x.get("url") or ""))[0]
        if b[:1].upper() == "L" and b[1:].isdigit():
            used.add(int(b[1:]))
    n = 1
    while n in used:
        n += 1
    return n


def rel_of(p):
    return os.path.join(ROOT, p.replace("/", os.sep))


def check_items(iso, rec, cache, budget, oracle):
    """验一遍这个国家现有的图。返回 [(idx, ok, why)]，并把结果写进缓存。"""
    imgs = rec.get("landmark_images") or []
    todo, todo_idx = [], []
    out = {}
    for i, it in enumerate(imgs):
        key = it.get("title") or it.get("url") or ""
        hit = cache.get(key)
        if hit and "ok" in hit:
            out[i] = (hit["ok"], hit.get("why") or "缓存")
            continue
        p = rel_of(it.get("url") or "")
        if not os.path.exists(p):
            continue
        todo.append((it.get("caption_zh") or rec.get("name_zh"), to_jpeg(p)))
        todo_idx.append(i)

    if todo and budget.dry():
        return out, 0, 0, True
    if todo:
        try:
            res, tin, tout = judge(rec.get("name_zh") or iso, todo)
        except Exception as e:
            log("      [!] 复核请求失败：%s" % str(e)[:70])
            return out, 0, 0, False
        budget.add(tin, tout)
        for k, i in enumerate(todo_idx):
            ok, why = res[k]
            out[i] = (ok, why)
            cache[imgs[i].get("title") or imgs[i].get("url")] = {
                "ok": ok, "why": why, "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"),
                                "iso": iso, "n": len(todo), "in": tin, "out": tout},
                               ensure_ascii=False) + "\n")
        return out, tin, tout, False
    return out, 0, 0, budget.dry()


def refill(iso, rec, conf, tried, cache, budget, cap):
    """把不合格的图换掉，并把数量不足的补齐。返回改动条数。"""
    changed = 0
    imgs = rec.get("landmark_images") or []
    # 同一个 Commons 文件绝不能同时挂在两处地标下 —— 实测基里巴斯的
    # Battle Tarawa.jpg 先被换给贝蒂奥岛，又被"补齐"给了塔拉瓦环礁，
    # 一个国家里出现两张一模一样的照片。tried 是按地标记的，所以要在国家层面再拦一道。
    used_names = {(x.get("title") or "").replace("File:", "") for x in imgs}
    for it in list(imgs):
        key = it.get("title") or it.get("url") or ""
        v = cache.get(key) or {}
        if v.get("ok", True):
            continue
        kw = it.get("kw") or ""
        e = entry_of(conf, iso, kw)
        if not e:
            continue
        log("      - %s 判定不合格（%s），换下一张" % (it.get("caption_zh"), v.get("why", "")))
        picks, _err = F.ranked_picks(e)
        done = set(tried.setdefault(iso, {}).setdefault(kw, []))
        done.add(it.get("title", "").replace("File:", ""))
        nxt = next((p for p in picks if p["name"] not in done
                    and p["name"] not in used_names), None)
        if not nxt:
            tried[iso][kw] = sorted(done)
            log("        [x] %s 候选已全部试过，保留空缺" % it.get("caption_zh"))
            imgs.remove(it)
            changed += 1
            continue
        done.add(nxt["name"])
        used_names.add(nxt["name"])
        tried[iso][kw] = sorted(done)
        if budget.dry():
            return changed
        base = os.path.splitext(os.path.basename(it.get("url") or "L01"))[0]
        rel = "assets/%s/%s%s" % (iso.lower(), base, nxt["ext"])
        try:
            F.C.download(nxt["meta"]["remote_url"], rel_of(rel))
        except Exception as e:
            log("        [x] 下载失败 %s" % str(e)[:50])
            continue
        # 老图（webp / 缩略图）留着会被 optimize --prune 清掉，这里不用管
        raw = to_jpeg(rel_of(rel))
        try:
            res, tin, tout = judge(rec.get("name_zh") or iso,
                                   [(it.get("caption_zh"), raw)])
            budget.add(tin, tout)
            ok, why = res[0]
        except Exception as e:
            log("        [!] 复核失败 %s" % str(e)[:50])
            ok, why = True, "复核失败，先收下"
        cache["File:" + nxt["name"]] = {"ok": ok, "why": why,
                                        "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        if not ok:
            log("        [x] 新候选也不合格（%s），继续换" % why)
            try:
                os.remove(rel_of(rel))
            except OSError:
                pass
            continue
        for old in (it.get("url"), it.get("thumb_url")):
            pth = rel_of(old or "")
            if pth and pth != rel_of(rel) and os.path.exists(pth):
                try:
                    os.remove(pth)          # 换掉的旧 webp 直接删，别留孤儿
                except OSError:
                    pass
        # 换图时图注回到清单里的规范名。实测踩到：肯尼亚的"东非大裂谷"早期
        # 被写成"坦桑尼亚东非大裂谷"（图确实是坦桑尼亚那段），复核换成了
        # 肯尼亚境内那张，但图注没跟着改 —— 图和字各说各的，比错图更糟。
        it.update({"title": "File:" + nxt["name"], "url": rel, "thumb_url": rel,
                   "caption_zh": zh, "caption": zh,
                   "source": nxt["kind"], "pick_tier": nxt["tier"],
                   "remote_url": nxt["meta"]["remote_url"],
                   "author": nxt["meta"].get("artist"),
                   "license": nxt["meta"].get("license"),
                   "license_url": nxt["meta"].get("license_url"),
                   "bytes": os.path.getsize(rel_of(rel)), "verified": "vision"})
        log("        [ok] 换成 %s（%s）" % (nxt["name"][:42], why))
        changed += 1

    # 数量不足：把缺的位子逐个补。一个地标最多连试 MAX_TRIES 个候选 ——
    # 候选池本身有限，加上这个上限，补齐不可能无限跑。
    entries = (conf.get("countries") or {}).get(iso) or []
    while len(imgs) < cap and not budget.dry():
        progress = False
        for e in entries:
            if len(imgs) >= cap or budget.dry():
                break
            if (e.get("kw") or "").strip().lower() in {(x.get("kw") or "").strip().lower() for x in imgs}:
                continue
            picks, _err = F.ranked_picks(e)
            done = set(tried.setdefault(iso, {}).setdefault(e.get("kw"), []))
            got = False
            for nxt in [p for p in picks
                        if p["name"] not in done and p["name"] not in used_names][:MAX_TRIES]:
                done.add(nxt["name"])
                used_names.add(nxt["name"])
                tried[iso][e.get("kw")] = sorted(done)
                rel = "assets/%s/L%02d%s" % (iso.lower(), next_slot(imgs), nxt["ext"])
                try:
                    F.C.download(nxt["meta"]["remote_url"], rel_of(rel))
                except Exception:
                    continue
                try:
                    res, tin, tout = judge(rec.get("name_zh") or iso,
                                           [(e.get("zh"), to_jpeg(rel_of(rel)))])
                    budget.add(tin, tout)
                    ok, why = res[0]
                except Exception:
                    ok, why = True, "复核失败，先收下"
                cache["File:" + nxt["name"]] = {
                    "ok": ok, "why": why,
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
                if not ok:
                    try:
                        os.remove(rel_of(rel))
                    except OSError:
                        pass
                    log("        · %s 的 %s 也不合格（%s）" % (e.get("zh"), nxt["name"][:30], why))
                    continue
                imgs.append({"title": "File:" + nxt["name"], "caption_zh": e.get("zh"),
                             "caption": e.get("zh"), "kw": e.get("kw"),
                             "url": rel, "thumb_url": rel,
                             "remote_url": nxt["meta"]["remote_url"],
                             "source": nxt["kind"], "pick_tier": nxt["tier"],
                             "author": nxt["meta"].get("artist"),
                             "license": nxt["meta"].get("license"),
                             "license_url": nxt["meta"].get("license_url"),
                             "bytes": os.path.getsize(rel_of(rel)), "verified": "vision"})
                log("      + %s 补入 %s（%s）" % (e.get("zh"), nxt["name"][:42], why))
                changed += 1
                got = True
                break
            if got:
                progress = True
        if not progress:
            break
    return changed


def main():
    ap = argparse.ArgumentParser(description="视觉复核：收错图 + 补不足")
    ap.add_argument("isos", nargs="*", help="ISO 码，如 IS KI")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", nargs="*", help="同 isos，便于阅读")
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--budget", type=float, default=3.0, help="花到多少元就停（0=不限）")
    ap.add_argument("--oracle", action="store_true", help="只判定不替换不下载")
    a = ap.parse_args()

    load_env()
    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        log("[STOP] 缺 DEEPSEEK_API_KEY")
        return 1

    conf = read_json(os.path.join(ROOT, "data", "landmarks.json"), {"countries": {}})
    meta = read_json(os.path.join(ROOT, "geo", "meta.json"), {})
    cache = read_json(CACHE, {})
    tried = read_json(TRIED, {})

    isos = [x.upper() for x in (a.isos + (a.only or []))]
    if a.all:
        isos = sorted(conf.get("countries", {}).keys())
    if not isos:
        ap.error("给个 ISO 码，或用 --all")

    budget = Budget(a.budget)
    log("模型 %s · 目标 %d 国 · 最多 %d 轮 · 预算 %.1f 元（%s时段）"
        % (MODEL, len(isos), a.max_rounds, a.budget,
           "高峰" if is_peak() else "空闲"))

    for rnd in range(1, a.max_rounds + 1):
        t_rnd = time.time()
        log("\n=== 第 %d 轮 ===" % rnd)
        total_changed = 0
        for n, iso in enumerate(isos, 1):
            path = os.path.join(ROOT, "data", "%s.json" % iso.lower())
            rec = read_json(path, {})
            if not rec.get("landmark_images"):
                continue
            cap = F.country_cap(iso, conf, len((conf.get("countries") or {}).get(iso) or []), meta)
            verdict, tin, tout, stopped = check_items(iso, rec, cache, budget, a.oracle)
            bad = [i for i, (ok, _w) in verdict.items() if not ok]
            log("[%d/%d] %-4s %-8s %d 张，不合格 %d"
                % (n, len(isos), iso, rec.get("name_zh", ""), len(verdict), len(bad)))
            for i in bad:
                log("      - %s：%s" % ((rec["landmark_images"][i].get("caption_zh")),
                                       verdict[i][1]))
            ch = 0 if a.oracle else refill(iso, rec, conf, tried, cache, budget, cap)
            if ch:
                total_changed += ch
                write_json(path, rec)
            if stopped or budget.dry():
                log("\n[预算] 已花 %.3f 元，停下。" % budget.used)
                break

        write_json(CACHE, cache)
        write_json(TRIED, tried)
        log("\n第 %d 轮：改动 %d 处，累计 %.2f 元 / %d 次调用 / %.0f 秒"
            % (rnd, total_changed, budget.used, budget.calls, time.time() - t_rnd))
        if total_changed == 0:
            log("本轮没有任何改动 → 收敛，提前退出。")
            break

    log("\n===== 汇总 =====")
    log("  调用 %d 次，输入 %s tokens，输出 %s tokens"
        % (budget.calls, "{:,}".format(budget.tin), "{:,}".format(budget.tout)))
    log("  花费 %.3f 元（预算 %.1f 元）" % (budget.used, a.budget))
    log("  缓存 %d 条 → data/_vision_cache.json（同一张图不会再花钱）" % len(cache))
    log("\n下一步：python scripts/optimize_images.py --all && python scripts/sync_index.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
