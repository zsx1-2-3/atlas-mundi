# -*- coding: utf-8 -*-
"""定点替换某一张地标图（原位替换，不动同国其他图）。

和 refill_gap.py 的区别
-----------------------
refill_gap 是「往列表末尾**追加**一张」，用来补缺口。
这里是「把某一位置上的图**换掉**」——用于图注与画面都对不上、但位置本身合理的情况
（例如「科隆大教堂窗户细节」占着科隆大教堂这一格）。

为什么需要它
------------
老评分器只看宽高比和宽度（score() 里没有"细节 vs 全景"的概念），
所以维基正文配图池里的窗户特写、天顶画、楼梯、门把手都会被当成好图。
2026-11-04 全库扫出 35 处这类图，其中 21 处换成了代表图。

替换后：
- 新图占用一个**新的 Lnn 编号**（不复用旧编号，避免浏览器缓存旧图）
- 旧的三份文件（.jpg/.webp/.thumb.webp）一并删除
- 条目在列表中的**位置不变**（第一张仍然是主图）
- title / caption_zh / 授权 / 来源字段全部按新图重写，并记 replace_reason
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_images as F                     # noqa: E402
from refill_gap import next_index, to_webp   # noqa: E402  复用同一套转码规格

ROOT = F.ROOT

# (iso, 旧题注（用来定位条目）, 新的 Commons 文件名, 新中文题注, 替换原因)
JOBS = [
    # —— 2026-11-04 第三批：把「局部/内部」换成代表图 ——
    ("AF", "蓝色清真寺细节",
     "Rowza Shrine Hazrat Ali Mazar-e Sharif Afghanistan.. shrine.jpg", "蓝色清真寺",
     "原图是蓝色瓷砖墙面特写"),
    ("BI", "布琼布拉大教堂彩绘玻璃窗局部",
     "Cathédrale Regina Mundi de Bujumbura, 2024-3.jpg", "布琼布拉大教堂",
     "原图是彩绘玻璃窗局部"),
    ("BN", "杰米清真寺内部",
     "Jame Asr Hassanil Bolkiah Mosque.jpg", "杰米清真寺",
     "原图是清真寺内部"),
    ("DE", "科隆大教堂窗户细节",
     "Kölner Dom von Osten.jpg", "科隆大教堂",
     "原图是窗户特写；换成维基 P18（从东面看双塔），这张当年被 must=cologne 误杀"),
    ("DE", "柏林电视塔十字架局部",
     "Tv-turm - panoramio.jpg", "柏林电视塔",
     "原图是塔顶十字架局部"),
    ("ES", "阿尔罕布拉宫局部细节",
     "Granada - View from Mirador de San Nicolás - 01.jpg", "阿尔罕布拉宫",
     "原图是墙面雕饰细节"),
    ("FI", "图尔库城堡内部",
     "Turun linna maaliskuu 2013 2.jpg", "图尔库城堡",
     "原图是城堡内部"),
    ("FR", "凡尔赛宫镜厅穹顶",
     "Chateau Versailles Galerie des Glaces.jpg", "凡尔赛宫镜厅",
     "原图是天顶画；原本更好的法语命名图 Galerie des Glaces 被 must=mirror 误杀"),
    ("GT", "蒂卡尔古城四号神庙转角局部",
     "Ruins of Structure 5D-96 Mayan pyramid in Tikal, Guatemala.jpg", "蒂卡尔古城",
     "原图是神庙转角局部"),
    ("KR", "南山首尔塔公交站局部",
     "N Seoul Tower (13952097192).jpg", "南山首尔塔",
     "原图拍的其实是山下的公交站"),
    ("MC", "摩纳哥大教堂内部",
     "Cathedrale-Notre-Dame-Immaculee-de-Monaco-byBelleSoeur2Rundvald-2.jpg", "摩纳哥大教堂",
     "原图是大教堂内部"),
    ("MM", "蒲甘佛塔群壁画佛塔内部",
     "Bagan Temples (10325948433).jpg", "蒲甘佛塔群",
     "原图是佛塔内部壁画"),
    ("MT", "瓦莱塔圣约翰大教堂法国分团礼拜堂穹顶",
     "Concatedral de San Juan, La Valeta, isla de Malta, Malta, 2021-08-25, DD 246-248 HDR.jpg",
     "瓦莱塔圣约翰大教堂",
     "原图是分团礼拜堂穹顶"),
    ("MY", "双子塔内部视角",
     "Kuala Lumpur - panoramio (18).jpg", "双子塔",
     "原图是从塔内往外看"),
    ("NE", "阿加德兹大清真寺局部",
     "Niger, Agadez (42), Grand Mosque, minaret.jpg", "阿加德兹大清真寺",
     "原图是清真寺墙脚局部；换成那座著名的泥砖方锥宣礼塔"),
    ("NI", "莱昂大教堂内部",
     "Nicaragua 2017-03-13 (32976253324).jpg", "莱昂大教堂",
     "原图是大教堂内部"),
    ("RO", "喀尔巴阡山热舒夫市政厅局部",
     "Muntii Bucegi vazuti de pe Postavaru.jpg", "喀尔巴阡山",
     "原图其实是波兰热舒夫市政厅（地标名与画面完全不符）"),
    ("SK", "圣伊丽莎白教堂内部",
     "Blue Church, Bratislava 01.jpg", "圣伊丽莎白教堂",
     "原图是教堂内部"),
    ("SY", "大马士革倭马亚清真寺内部",
     "The Umayyad Mosque, the Courtyard, Damascus, Syria.jpg", "大马士革倭马亚清真寺",
     "原图是礼拜殿内部"),
    ("UA", "基辅圣索菲亚大教堂局部",
     "2019-07-21 Saint Sophia Cathedral in Kyiv.jpg", "圣索菲亚大教堂",
     "原图是钟楼局部裁剪"),
    ("VN", "美山圣地寺庙局部细节",
     "2024 - Mỹ Sơn Sanctuary Temple E7 - img 01.jpg", "美山圣地",
     "原图是寺庙雕刻局部"),
]


def main():
    only = {x.upper() for x in sys.argv[1:] if not x.startswith("-")}
    dry = "--dry-run" in sys.argv
    done, failed = 0, []

    for iso, old_cap, name, new_cap, why in JOBS:
        if only and iso not in only:
            continue
        slug = iso.lower()
        path = os.path.join(ROOT, "data", "%s.json" % slug)
        if not os.path.exists(path):
            failed.append((iso, "data/%s.json 不存在" % slug))
            continue
        with io.open(path, encoding="utf-8") as f:
            rec = json.load(f)

        imgs = rec.get("landmark_images") or []
        target = None
        for it in imgs:
            if (it.get("caption_zh") or "").strip() == old_cap:
                target = it
                break
        if target is None:
            # 已经换过了（可重跑）：看新题注在不在
            if any((it.get("caption_zh") or "").strip() == new_cap for it in imgs):
                print("  · %-3s %-20s 已是「%s」，跳过" % (iso, old_cap[:20], new_cap))
            else:
                failed.append((iso, "找不到题注「%s」" % old_cap))
            continue

        if dry:
            print("  · %-3s %-20s → %-20s  %s" % (iso, old_cap[:20], new_cap, name[:44]))
            continue

        info = F.C.commons_imageinfo([name], width=1400)
        meta = F.C.lookup_meta(info, name)
        if not meta or not meta.get("remote_url"):
            failed.append((iso, "取不到元数据：%s" % name))
            continue

        adir = os.path.join(ROOT, "assets", slug)
        os.makedirs(adir, exist_ok=True)
        idx = next_index(adir)
        ext = F.C.ext_of(meta["remote_url"])
        jpg_rel = "assets/%s/L%02d%s" % (slug, idx, ext)
        jpg_abs = os.path.join(ROOT, jpg_rel)
        try:
            size = F.C.download(meta["remote_url"], jpg_abs)
            detail_rel, thumb_rel, dsize, tsize = to_webp(jpg_abs)
        except Exception as e:
            failed.append((iso, "下载/转码失败 %s" % str(e)[:40]))
            continue

        # 删旧文件（三份），编号不复用
        old_url = target.get("url") or ""
        if old_url:
            base = os.path.splitext(os.path.join(ROOT, old_url))[0]
            for suf in (".jpg", ".jpeg", ".png", ".JPG", ".webp", ".thumb.webp"):
                p = base + suf if not suf.startswith(".thumb") else base + suf
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        lic = meta.get("license")
        target.update({
            "title": "File:" + name,
            "caption_zh": new_cap,
            "caption": new_cap,
            "caption_commons": (meta.get("description") or "")[:80],
            "url": detail_rel,
            "thumb_url": thumb_rel,
            "remote_url": meta["remote_url"],
            "source": "manual_repick",
            "pick_tier": "handpicked",
            "author": meta.get("artist"),
            "license": lic,
            "license_url": meta.get("license_url"),
            "license_status": "ok" if lic else "unknown",
            "load_verified": True,
            "bytes": size,
            "size": {"detail": "%dx%d" % dsize, "thumb": "%dx%d" % tsize},
            "replace_reason": why,
        })
        target.pop("caption_zh_source", None)

        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
        print("  ✓ %-3s %-20s → %-18s %s" % (iso, old_cap[:20], new_cap, detail_rel))
        done += 1

    print("\n替换 %d 张，失败 %d 张" % (done, len(failed)))
    for iso, why in failed:
        print("  x %-3s %s" % (iso, why))
    if done:
        print("\n下一步：python scripts/build_cards.py → upload_obs --only data → 传新增 assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
