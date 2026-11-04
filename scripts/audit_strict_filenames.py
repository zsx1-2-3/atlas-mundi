# -*- coding: utf-8 -*-
"""只读统计：如果「文件名里必须出现地标专名」这条规则真的开起来，会怎样。

不改数据、不联网 —— 只用 data/*.json 里已有的 title / kw 做字符串比对，
所以任何机器上都能秒出结果。

三种口径
--------
strict   文件名里必须出现**完整**关键词短语（Table Mountain Cape Town → 要 table mountain）
loose    文件名里出现关键词里**任意一个实词**（≥4 字母、非停用词）
nonrep   文件名命中「局部/入口/招牌/内部」这类词 —— 这些图就算含专名也不是代表作

用法
----
    python scripts/audit_strict_filenames.py
    python scripts/audit_strict_filenames.py --show-miss    # 附带列出 strict 没命中的
"""

import argparse
import io
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STOP = set("""the of and in at on de la le les du des el der die das van von
national park city island river lake mount mountain falls bay beach coast
region area museum palace tower bridge cathedral church garden square""".split())

# 就算文件名里带着地标专名，这类画面也不是「这处地标长什么样」
NONREP = re.compile(
    r"visitor|information centre|information center|entrance|entry gate|park gate|"
    r"plaque|signboard|\bsign\b|admission|ticket|kiosk|stage|inside|interior|"
    r"mosaic|pediment|ceiling|window|detail|stairs|staircase|boiling room|"
    r"police officer|marines|soldiers|194\d|black and white", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^0-9a-z]+", " ", s.lower())
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show-miss", action="store_true")
    args = ap.parse_args()

    total = 0
    hit_strict = hit_loose = 0
    bad_pass_strict = 0          # 非代表作里，strict 也放行的张数
    bad_total = 0
    per_country = {}             # iso -> [总张数, strict 命中数]
    miss_rows, bad_rows = [], []

    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if not re.match(r"^[a-z]{2}\.json$", fn):
            continue
        iso = fn[:-5].upper()
        try:
            rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        except Exception:
            continue
        imgs = rec.get("landmark_images") or []
        stat = per_country.setdefault(iso, [0, 0])
        for it in imgs:
            title = norm((it.get("title") or "").replace("File:", ""))
            kw = norm(it.get("kw") or it.get("caption_zh") or "")
            if not title or not kw.strip():
                continue
            total += 1
            stat[0] += 1
            kw_phrase = kw.strip()
            toks = [t for t in kw_phrase.split() if len(t) >= 4 and t not in STOP]
            ok_strict = (" " + kw_phrase + " ") in title if kw_phrase else False
            ok_loose = any(t in title for t in toks) if toks else False
            if ok_strict:
                hit_strict += 1
                stat[1] += 1
            elif args.show_miss:
                miss_rows.append((iso, (it.get("caption_zh") or "")[:18],
                                  kw_phrase[:34], (it.get("title") or "")[:58]))
            if ok_loose:
                hit_loose += 1
            if NONREP.search(it.get("title") or ""):
                bad_total += 1
                if ok_strict or ok_loose:
                    bad_pass_strict += 1
                    bad_rows.append((iso, (it.get("caption_zh") or "")[:18],
                                     (it.get("title") or "")[:60]))

    print("样本：%d 张图，%d 个国家/地区\n" % (total, len(per_country)))
    pct = lambda n: (100.0 * n / total) if total else 0
    print("① 完整关键词出现在文件名里（strict）：%d 张  %.1f%%" % (hit_strict, pct(hit_strict)))
    print("② 关键词任一实词出现在文件名里（loose）：%d 张  %.1f%%" % (hit_loose, pct(hit_loose)))
    print("③ 按 strict 卡，会被挡掉的：%d 张  %.1f%%" % (total - hit_strict, 100 - pct(hit_strict)))

    zero = [iso for iso, (n, h) in per_country.items() if n and h == 0]
    lt3 = [iso for iso, (n, h) in per_country.items() if n and h < min(3, n)]
    print("\n④ 按 strict 卡，一国一张都不剩：%d 国  %s" % (len(zero), " ".join(sorted(zero))[:180]))
    print("⑤ 掉到 3 张以下：%d 国" % len(lt3))

    print("\n⑥ 反向验证：文件名「含专名但不是代表作」的图 %d 张，strict 会放行 %d 张（%.0f%%）"
          % (bad_total, bad_pass_strict, 100.0 * bad_pass_strict / bad_total if bad_total else 0))
    for iso, cap, t in bad_rows:
        print("     %-3s %-18s %s" % (iso, cap, t))

    if miss_rows:
        print("\n⑦ strict 判为「没命中」的前 40 张：")
        for iso, cap, kw, t in miss_rows[:40]:
            print("     %-3s %-18s kw=%-34s %s" % (iso, cap, kw, t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
