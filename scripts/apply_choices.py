# -*- coding: utf-8 -*-
"""把选图台上的点选结果落进数据。

输入是 `AW:4 NE:5 ... EH:del LA:del` 这样一行。
两种动作：
  · 数字 = 选第 N 个候选（1 起）→ 有同名条目就替换，没有就新增（已删待补的格子）
  · del  = 删掉这个格子

候选清单来自 review/choose.json（make_chooser.py 生成的），
图片从 Commons 重新取一遍元数据（授权、作者要落库，不能丢）。

用法：
    python scripts/apply_choices.py "AW:4 NE:5 ... EH:del"
    python scripts/apply_choices.py "..." --apply
"""
import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from envfile import load       # noqa: E402
load()
import fill_images as F        # noqa: E402
from refill_gap import to_webp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 选图台上 "MK2" 是斯科普里石桥那一行（同一个国家两行），落库要用真实 iso
ISO_ALIAS = {"MK2": "MK"}
CHOOSE = os.path.join(ROOT, "review", "choose.json")


def next_index(iso):
    """该国下一个空着的 L 编号。"""
    d = os.path.join(ROOT, "assets", iso.lower())
    os.makedirs(d, exist_ok=True)
    used = set()
    for f in os.listdir(d):
        m = re.match(r"^L(\d+)\.(jpg|webp)$", f)
        if m:
            used.add(int(m.group(1)))
    i = 1
    while i in used:
        i += 1
    return i


def fetch_item(iso, name, cap, kw):
    """从 Commons 取图 → 存 jpg → 转 webp → 返回可直接入 data 的条目。"""
    info = F.C.commons_imageinfo([name], width=1400)
    meta = F.C.lookup_meta(info, name)
    if not meta or not meta.get("remote_url"):
        raise RuntimeError("取不到元数据")
    if (meta.get("orig_w") or 0) < 900:
        raise RuntimeError("原图只有 %dpx，太窄" % (meta.get("orig_w") or 0))
    idx = next_index(iso)
    jpg = os.path.join(ROOT, "assets", iso.lower(), "L%02d.jpg" % idx)
    raw = F.C.http_get(meta["remote_url"], timeout=120, retries=2)
    if len(raw) < 1024:
        raise RuntimeError("下载到的文件过小")
    with open(jpg, "wb") as f:
        f.write(raw)
    url, thumb, ds, ts = to_webp(jpg)
    return {
        "title": name if name.startswith("File:") else "File:" + name,
        "caption_zh": cap, "caption": cap, "caption_commons": "",
        "kw": kw, "url": url, "thumb_url": thumb,
        "remote_url": meta.get("remote_url"),
        "source": "manual_pick", "pick_tier": "strict",
        "author": meta.get("artist") or None,
        "license": meta.get("license"),
        "license_url": meta.get("license_url"),
        "license_status": "ok" if meta.get("license") else "unknown",
        "bytes": os.path.getsize(os.path.join(ROOT, url)),
        "size": {"detail": "%dx%d" % ds, "thumb": "%dx%d" % ts},
        "caption_zh_source": "manual_pick",
        "verified": "manual",
    }


def resolve_caption(imgs, cap):
    """选图台上的题注 → 数据里的实际题注。改过名的用包含关系兜一下。"""
    caps = [(x.get("caption_zh") or "") for x in imgs]
    if cap in caps:
        return cap
    cand = [c for c in caps if c and (c in cap or cap in c)]
    if cand:
        return max(cand, key=len)
    return None


def apply_one(iso, cap, action, cards, dry, card=None):
    """返回 (结果字符串, 是否改动)。

    card 由调用方传入：同一个国家在选图台上可能有两行（MK 奥赫里德 / MK2 石桥），
    按 iso 查会永远命中第一行，所以不能在这里按 iso 找。
    """
    if card is None:
        card = next((c for c in cards if c["iso"] == iso), None)
    if not card:
        return "找不到 %s 这一行" % iso, False
    p = os.path.join(ROOT, "data", "%s.json" % iso.lower())
    if not os.path.exists(p):
        return "没有 data/%s.json" % iso.lower(), False
    rec = json.load(io.open(p, encoding="utf-8"))
    imgs = rec.get("landmark_images") or []
    real = resolve_caption(imgs, cap)
    if real is None and action != "del":
        real = cap            # 已删待补的格子：数据里本来就没有，用页面题注新增
    elif real is not None:
        cap = real

    if action == "del":
        keep = [x for x in imgs if (x.get("caption_zh") or "") != cap]
        if len(keep) == len(imgs):
            return "没有这条题注，跳过：%s" % cap, False
        if dry:
            return "将删除 %s（%d → %d 张）" % (cap, len(imgs), len(keep)), True
        rec["landmark_images"] = keep
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
        return "已删 %s（%d → %d 张）" % (cap, len(imgs), len(keep)), True

    n = int(action)
    if n < 1 or n > len(card["items"]):
        return "%s 没有第 %d 个候选" % (iso, n), False
    cand = card["items"][n - 1]
    if dry:
        return "将换成 %s" % cand["name"][:52], True
    try:
        item = fetch_item(iso, cand["name"], cap, card.get("kw") or "")
    except Exception as e:
        return "取图失败：%s（%s）" % (str(e)[:40], cand["name"][:26]), False

    hit = None
    for it in imgs:
        if (it.get("caption_zh") or "") == cap:
            hit = it
            break
    if hit is not None:
        old_title = hit.get("title")
        hit.update(item)
        msg = "已替换 %s ← %s" % (cap, cand["name"][:44])
    else:
        imgs.append(item)
        rec["landmark_images"] = imgs
        msg = "已新增 %s ← %s" % (cap, cand["name"][:44])
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
    return msg, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("picks", help='例如 "AW:4 NE:5 EH:del"')
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    cards = json.load(io.open(CHOOSE, encoding="utf-8"))
    cap_of = {c["iso"]: c["cap"] for c in cards}
    toks = [t for t in args.picks.replace(",", " ").split() if ":" in t]
    print("解析到 %d 条指令%s\n" % (len(toks), "" if args.apply else "（预演）"))
    done = 0
    for t in toks:
        iso, action = t.split(":", 1)
        iso, action = iso.strip().upper(), action.strip().lower()
        iso = ISO_ALIAS.get(iso, iso)
        tag = t.split(":", 1)[0].strip().upper()
        card = next((c for c in cards if c["iso"] == tag), None)
        if not card:
            print("  x %-4s 选图台里没有这一行" % tag)
            continue
        cap = card["cap"]
        msg, ch = apply_one(iso, cap, action, cards, not args.apply, card=card)
        done += 1 if ch else 0
        print("  %-4s %-22s %s" % (iso, cap[:22], msg))
    print("\n完成 %d 条%s" % (done, "" if args.apply else "（未落盘，加 --apply 才写）"))


if __name__ == "__main__":
    main()
