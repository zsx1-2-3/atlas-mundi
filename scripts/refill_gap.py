# -*- coding: utf-8 -*-
"""精准补图：只补缺口，已有的图一张都不动。

和 fill_images.py 的区别
------------------------
fill_images 是**整国重选**（`rec["landmark_images"] = picked`），跑一次会把
好图也一起换掉 —— 之前"补第五张"结果把凡尔赛镜厅换成某酒吧的镜子就是这么来的。

这个脚本只做一件事：往某个国家的 `landmark_images` **末尾追加**一张，
已有条目、已有文件、已有编号全部原样保留。

用法
----
    python scripts/refill_gap.py                # 补全部缺口
    python scripts/refill_gap.py ZA MY          # 只补这两个国家
    python scripts/refill_gap.py --dry-run      # 只搜索、列出候选，不下载不写盘

依赖 Commons API（commons.wikimedia.org）。当前网络若访问不了维基，
脚本会在第一步直接报错退出，不会留下半截数据。
"""

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F              # 复用它的检索、评分、黑名单
from PIL import Image

ROOT = F.ROOT

# 图片规格，与 scripts/optimize_images.py 保持一致
DETAIL_W, DETAIL_Q = 1080, 75
THUMB_W, THUMB_Q, THUMB_H = 240, 78, 180

# ---------------------------------------------------------------------------
# 缺口清单。每一条对应 2026-11-04 被删掉的一张错图。
#   must  —— 文件名里必须出现的专名（去空格、忽略大小写后做包含判断）
#   avoid —— 命中即淘汰的排除词，用来挡住"同名不同地"
#
# 为什么不用 is_junk 统一挡：JUNK_WORDS 里有 prison/office/school 这些词，
# 而罗本岛本身就是监狱博物馆、帕拉马里博那处就是市场建筑 —— 一刀切会误伤，
# 这里按缺口逐条精确指定。
GAPS = [
    dict(iso="ZA", zh="罗本岛", pick="Robben Island-113362.jpg",
         why="原本与花园大道共用一张图（L04 的 webp 转换失败后回退错了）"),

    dict(iso="MY", zh="黑风洞", pick="Batu Caves stairs 2022-05.jpg",
         why="原本与下一张重复"),

    dict(iso="AE", zh="哈利法塔", pick="Dubai skyline 2015 (crop).jpg",
         why="原图是 2008 年施工中，换成含塔的迪拜天际线"),

    dict(iso="AE", zh="迪拜棕榈岛", pick="Palm Jumeirah Beach.jpg",
         why="原图是美国南卡罗来纳州的 Isle of Palms（同名不同地）"),

    dict(iso="CA", zh="尼亚加拉大瀑布",
         pick="Canadian Horseshoe Falls with city of Niagara Falls, Ontario in background.jpg",
         why="原图是布鲁克林博物馆藏的油画"),

    dict(iso="CV", zh="圣菲利佩皇家要塞", pick="CidadeVelha-SaoFelipe1.jpg",
         why="原图是要塞平面图"),

    dict(iso="KI", zh="贝蒂奥岛", pick="Kiribati Bairiki Betio Causeway.jpg",
         why="原图是塔拉瓦战役黑白照"),

    dict(iso="SR", zh="帕拉马里博中央市场",
         pick="Paramaribo market (33557504165).jpg",
         why="原图是城市地图"),

    dict(iso="XK", zh="普里什蒂纳新生纪念碑", pick="NEWBORN Monument.jpg",
         why="原图是纪念碑彩绘施工过程"),

    dict(iso="YE", zh="亚丁港", pick="Green Lighthouse.jpg",
         why="原图是 1903 年明信片；Aden harbour.jpg 是黑白老照片，换 2010 年的彩色港口全景"),
]

# 这 7 处查过候选池，Commons 上确实没有能用的素材（全是动物/古地图/别的城市的同名地标），
# 保持删除状态 —— 宁可不显示，也不放一张错的。
SKIPPED = [
    ("BD", "孟加拉国国会大厦", "候选只有「巴基斯坦邮票上的国会大厦」和实景照都对不上专名"),
    ("CA", "多伦多天际线", "候选全是一栋住宅楼的施工记录照（88 Scott / 252 Church / 19 Duncan）"),
    ("CG", "奥扎拉-科库阿国家公园", "候选 0 个，条目配图也打不开"),
    ("IO", "佩罗斯班霍斯环礁", "候选全是 18-19 世纪法文古地图（Plan des isles / PIan des isles）"),
    ("LR", "布坎南港", "候选 0 个，关键词池 12 张全被判为设施照"),
    ("PW", "洛克群岛", "唯一候选是帕劳科罗尔州的一条街，不是洛克群岛"),
    ("TZ", "桑给巴尔海滩", "候选全是动物（白鹭/蝴蝶/海豚/织雀），没有一个海岸场景"),
]


def check_api():
    """先确认检索接口活着。不通就别往下跑，免得写一半。"""
    try:
        F.C.api_json(F.COMMONS_API, {"action": "query", "meta": "siteinfo",
                                     "format": "json"}, timeout=15)
        return True, ""
    except Exception as e:
        return False, str(e)[:100]


def next_index(adir):
    """下一个可用编号：现有 L 号最大值 +1（编号有洞不复用，免得踩到旧文件）。"""
    mx = 0
    if os.path.isdir(adir):
        for n in os.listdir(adir):
            m = re.match(r"L(\d+)\.", n)
            if m:
                mx = max(mx, int(m.group(1)))
    return mx + 1


def to_webp(src_abs):
    """按 optimize_images.py 的规格转详情图 + 缩略图，返回相对仓库根的路径。"""
    base, _ext = os.path.splitext(src_abs)
    detail_abs = base + ".webp"
    thumb_abs = base + ".thumb.webp"

    im = Image.open(src_abs).convert("RGB")
    w, h = im.size

    w2 = min(DETAIL_W, w)
    h2 = max(1, round(h * w2 / float(w)))
    im.resize((w2, h2), Image.LANCZOS).save(detail_abs, "WEBP",
                                            quality=DETAIL_Q, method=6)

    w3 = min(THUMB_W, w)
    h3 = max(1, round(h * w3 / float(w)))
    if h3 > THUMB_H:                       # 竖图按宽缩完还太高，再按高度兜一次
        h3 = THUMB_H
        w3 = max(1, round(w * h3 / float(h)))
    im.resize((w3, h3), Image.LANCZOS).save(thumb_abs, "WEBP",
                                            quality=THUMB_Q, method=6)

    rel = lambda p: os.path.relpath(p, ROOT).replace("\\", "/")
    return rel(detail_abs), rel(thumb_abs), (w2, h2), (w3, h3)


def refill_one(gap, dry_run=False):
    """给一个国家补一张。成功返回 (entry, None)，失败返回 (None, 原因)。"""
    iso = gap["iso"].upper()
    slug = iso.lower()
    path = os.path.join(ROOT, "data", "%s.json" % slug)
    if not os.path.exists(path):
        return None, "data/%s.json 不存在" % slug

    with io.open(path, encoding="utf-8") as f:
        rec = json.load(f)

    # 已经有同题注的图就别补第二遍（脚本可以放心重跑）
    for it in (rec.get("landmark_images") or []):
        if (it.get("caption_zh") or "").strip() == gap["zh"]:
            return None, "已有「%s」，跳过" % gap["zh"]

    # 指定了文件名的，直接拿它的 imageinfo 下载 —— 这些是我一个个从候选里
    # 挑出来的（自动评分挑不准：尼亚加拉会挑到 1820 年版画、桑给巴尔会挑到白鹭）。
    if gap.get("pick"):
        info = F.C.commons_imageinfo([gap["pick"]], width=1400)
        meta = F.C.lookup_meta(info, gap["pick"])
        if not meta or not meta.get("remote_url"):
            return None, "指定文件取不到元数据：%s" % gap["pick"]
        picks = [dict(name=gap["pick"], meta=meta,
                      ext=F.C.ext_of(meta["remote_url"]),
                      kind="handpicked", tier="strict")]
        must = None
    else:
        entry = {"zh": gap["zh"], "kw": gap.get("kw") or gap["zh"]}
        if gap.get("must"):
            entry["must"] = gap["must"]
        if gap.get("avoid"):
            entry["avoid"] = gap["avoid"]
        picks, errs = F.ranked_picks(entry)
        if not picks:
            return None, ("检索失败：%s" % "；".join(errs[:2])) if errs else "没有可用候选"
        must = (entry.get("must") or "").strip() or None
    tried = 0
    reasons = []
    for p in picks:
        # 编辑图（P18/首图）对不上专名的，和 pick_for 一样不给优先权
        if p["kind"] in F.CURATED and must and not F.must_ok(p["name"], must):
            continue
        if (p["meta"].get("width") or 0) < 900:
            reasons.append("%s 尺寸太小" % p["name"][:30])
            continue
        tried += 1
        if tried > 5:
            break

        if dry_run:
            return dict(name=p["name"], kind=p["kind"], tier=p["tier"],
                        meta=p["meta"], dry=True), None

        adir = os.path.join(ROOT, "assets", slug)
        os.makedirs(adir, exist_ok=True)
        idx = next_index(adir)
        ext = p["ext"] or ".jpg"
        jpg_rel = "assets/%s/L%02d%s" % (slug, idx, ext)
        jpg_abs = os.path.join(ROOT, jpg_rel)
        try:
            size = F.C.download(p["meta"]["remote_url"], jpg_abs)
        except Exception as e:
            reasons.append("下载失败 %s" % str(e)[:40])
            continue

        try:
            detail_rel, thumb_rel, dsize, tsize = to_webp(jpg_abs)
        except Exception as e:
            reasons.append("转 webp 失败 %s" % str(e)[:40])
            continue

        meta = p["meta"]
        lic = meta.get("license")
        item = {
            "title": "File:" + p["name"],
            "caption_zh": gap["zh"],
            "caption": gap["zh"],
            "caption_commons": (meta.get("description") or "")[:80],
            "kw": gap.get("kw") or "",
            "url": detail_rel,
            "thumb_url": thumb_rel,
            "remote_url": meta["remote_url"],
            "source": p["kind"],
            "pick_tier": p["tier"],
            "author": meta.get("artist"),
            "license": lic,
            "license_url": meta.get("license_url"),
            "license_status": "ok" if lic else "unknown",
            "load_verified": True,
            "bytes": size,
            "size": {"detail": "%dx%d" % dsize, "thumb": "%dx%d" % tsize},
            "refill_reason": gap.get("why"),
        }
        rec.setdefault("landmark_images", []).append(item)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
        return item, None

    return None, "候选都不合适（%s）" % ("；".join(reasons[:2]) or "无")


def main():
    ap = argparse.ArgumentParser(description="只补缺失的地标图，不动已有")
    ap.add_argument("isos", nargs="*", help="限定 ISO，如 ZA MY；不写就是全部缺口")
    ap.add_argument("--dry-run", action="store_true", help="只搜索并列候选，不下载")
    args = ap.parse_args()

    ok, err = check_api()
    if not ok:
        print("✗ Commons API 不可达：%s" % err)
        print("  当前网络访问不了维基媒体（commons.wikimedia.org）。")
        print("  这和 2026-11-04 排查时的情况一样：代理能出国，但维基整套被拦。")
        print("  换一个能访问维基的网络，再重跑这条命令即可；数据没有被动过。")
        return 2

    gaps = GAPS
    if args.isos:
        want = {x.upper() for x in args.isos}
        gaps = [g for g in gaps if g["iso"].upper() in want]
        if not gaps:
            print("没有这些国家的缺口：%s" % " ".join(sorted(want)))
            return 1

    print("待补 %d 张（%d 个国家）%s\n" % (
        len(gaps), len({g["iso"] for g in gaps}), "  [预演模式]" if args.dry_run else ""))

    done, failed = 0, []
    for g in gaps:
        head = "[%s] %s" % (g["iso"], g["zh"])
        item, why = refill_one(g, dry_run=args.dry_run)
        if not item:
            print("  ✗ %-28s %s" % (head, why))
            failed.append((head, why))
            continue
        if args.dry_run:
            print("  · %-28s 候选：%s（%s/%s）  %dx%d" % (
                head, item["name"][:44], item["kind"], item["tier"],
                item["meta"].get("width") or 0, item["meta"].get("height") or 0))
        else:
            print("  ✓ %-28s → %s  %s" % (
                head, item["url"], (item["title"] or "")[:44]))
            done += 1

    print()
    if not args.isos and SKIPPED:
        print("以下 %d 处 Commons 上没有可用素材，保持不显示（宁缺勿错）：" % len(SKIPPED))
        for iso, zh, why in SKIPPED:
            print("  - [%s] %-16s %s" % (iso, zh, why))
        print()

    if args.dry_run:
        print("预演结束，未下载任何文件。去掉 --dry-run 才真正补图。")
        return 0

    print("补上 %d 张，失败 %d 张。" % (done, len(failed)))
    if done:
        print("\n下一步（把新图同步到线上）：")
        print("  python scripts/build_cards.py")
        print("  python scripts/upload_obs.py --bucket atlas-mundi-1419 "
              "--region cn-east-3 --only data")
    if failed:
        print("\n失败的多半是候选不合规，可以改 GAPS 里的 kw/must 再试：")
        for h, w in failed:
            print("  %-28s %s" % (h, w))
    return 0


if __name__ == "__main__":
    sys.exit(main())
