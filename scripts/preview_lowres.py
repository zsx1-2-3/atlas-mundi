# -*- coding: utf-8 -*-
"""换图前的核对表：把 review/lowres_plan.json 里每一组「旧 / 新」并排拼出来。

约定（2026-11-04 立的规矩）：任何自动选图，落盘前都必须过一遍这张表。
第一轮 12 张里肉眼就砍掉 5 张（线稿图、奥巴马合影、动物、木棉树），
光看文件名和分数是看不出来的。

用法
----
    python scripts/preview_lowres.py              # 第 1 页
    python scripts/preview_lowres.py --page 2     # 第 2 页
    python scripts/preview_lowres.py --per 9      # 每页 9 组
产物：review/lowres_sheet_1.jpg
"""

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F                        # noqa: E402

ROOT = F.ROOT
PLAN = os.path.join(ROOT, "review", "lowres_plan.json")
TMP = os.path.join(ROOT, "review", "lowres_tmp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--per", type=int, default=8)
    args = ap.parse_args()

    from PIL import Image, ImageDraw, ImageFont

    plan = json.load(io.open(PLAN, encoding="utf-8"))
    chunk = plan[(args.page - 1) * args.per: args.page * args.per]
    if not chunk:
        print("没有这一页（共 %d 组）" % len(plan))
        return 1
    os.makedirs(TMP, exist_ok=True)

    info = F.C.commons_imageinfo([p["new"] for p in chunk], width=640)
    CW, CH, LH = 460, 330, 40
    sheet = Image.new("RGB", (CW * 2, len(chunk) * (CH + LH)), "white")
    f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 14)
    s = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 11)
    d = ImageDraw.Draw(sheet)

    for i, p in enumerate(chunk):
        y = i * (CH + LH)
        # 旧图：本地缩略图
        old_path = None
        try:
            rec = json.load(io.open(os.path.join(ROOT, "data", p["iso"].lower() + ".json"),
                                    encoding="utf-8"))
            for it in rec.get("landmark_images") or []:
                if (it.get("title") or "").replace("File:", "") == p["old"]:
                    old_path = os.path.join(ROOT, it["url"])
                    break
        except Exception:
            pass
        # 新图：Commons 缩略图
        m = F.C.lookup_meta(info, p["new"]) or {}
        new_path = os.path.join(TMP, "%02d_new.jpg" % i)
        try:
            F.C.download(m.get("thumb_url") or m.get("remote_url"), new_path)
        except Exception as e:
            print("x 下载失败", p["new"], e)
            new_path = None

        for k, (path, tag, wpx) in enumerate((
                (old_path, "旧", p["old_w"]), (new_path, "新", p["new_w"]))):
            x = k * CW
            if path and os.path.exists(path):
                im = Image.open(path).convert("RGB")
                im.thumbnail((CW - 10, CH - 10), Image.LANCZOS)
                sheet.paste(im, (x + 5, y + 5))
            d.text((x + 6, y + CH + 2), "%s  %s %dpx" % (tag, p["iso"], wpx),
                   fill="#0a5" if tag == "新" else "#666", font=f)
            nm = p["new"] if tag == "新" else p["old"]
            d.text((x + 6, y + CH + 20), p["cap"][:18] + "  " + nm[:44],
                   fill="#888", font=s)

    out = os.path.join(ROOT, "review", "lowres_sheet_%d.jpg" % args.page)
    sheet.save(out, quality=88)
    print("ok %s  第 %d 页（共 %d 组，每页 %d）" % (out, args.page, len(plan), args.per))
    return 0


if __name__ == "__main__":
    sys.exit(main())
