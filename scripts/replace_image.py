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
import re
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
    # —— 2026-11-04 晚间第二批：全库体检扫出来的（audit_images.py）——
    ("IT", "威尼斯大运河入口",
     "Grand Canal Santa Maria della Salute Vaporetto Gondola Venice 01.jpg", "威尼斯大运河",
     "原图是卡纳莱托的油画《大运河入口》"),
    ("IT", "佛罗伦萨圣母百花大教堂",
     "Florence Duomo from Michelangelo hill.jpg", "佛罗伦萨圣母百花大教堂",
     "原图是米兰大教堂（地标名与画面不符）"),
    ("QA", "伊斯兰艺术博物馆瓷砖亭",
     "IslamicArtMuseumDohaSkyline.jpg", "伊斯兰艺术博物馆",
     "原图是伊斯坦布尔的瓷砖亭，不是多哈那座"),
    ("SH", "圣保罗大教堂西侧视角",
     "St Paul's Anglican Cathedral (16468521732).jpg", "圣保罗大教堂",
     "原图是古登堡计划的版画（PD-Art）"),
    ("KN", "蒂莫西山",
     "Timothy Hill (49950330897).jpg", "蒂莫西山",
     "原图是一个叫 Timothy Harris 的人的照片"),

    # —— 2026-11-04 晚间：用户点名的三张（都不是"照片"）——
    ("MX", "图卢姆玛雅遗址的卡瑟伍德1844年图",
     "El Castillo, Tulum Ruinas, 2023.jpg", "图卢姆玛雅遗址",
     "原图是 Catherwood 1844 年的石版画，不是遗址实景"),
    ("PG", "博根维尔岛上的海军陆战队员",
     "Bagana.jpg", "布干维尔岛巴加纳火山",
     "原图是二战黑白海军陆战队照片"),
    ("ES", "圣地亚哥朝圣之路荣耀之门",
     "Catedral de Santiago de Compostela, Portico de la Gloria.jpg",
     "圣地亚哥大教堂（朝圣之路终点）",
     "原图是绘有荣耀之门的油画"),
    ("VN", "美山圣地寺庙局部细节",
     "2024 - Mỹ Sơn Sanctuary Temple E7 - img 01.jpg", "美山圣地",
     "原图是寺庙雕刻局部"),
]


# ---------------------------------------------------------------------------
# 2026-11-04 第四批：用户「一并换掉」的 12 张非代表作。
#
# 这批图很特殊 —— 它们**文件名里就带着地标专名**：
#     Visitor Center, Arikok Park, San Fuego…      → 有 arikok
#     Gorongosa Park Gate.JPG                      → 有 gorongosa
#     NSGA Midway Sign.jpg                         → 有 midway
#     Ithaa inside.JPG                             → 有 ithaa
# 所以 must 闸门拦不住、严格文件名规则也拦不住（全库 20 张非代表作里 19 张能过）。
# 唯一拦得住的是「画面主体是什么」，落地上就是第二道文件名黑名单 NOT_REP。
#
# 这里**不写死 Commons 文件名**：写死等于我凭记忆猜文件名，猜错就整批换不成。
# 改成检索 + 两道闸（专名 must + 非代表作 NOT_REP），并且默认只预演。
NOT_REP = re.compile(
    r"visitor|\binformation\b|\bcentre\b|\bcenter\b|entrance|\bentry\b|"
    r"\bgates?\b|plaque|signboard|signpost|\bsigns?\b|admission|ticket|"
    r"kiosk|\bstage\b|\binside\b|interior|mosaic|pediment|ceiling|window|"
    r"detail|stair|boiling|police|officer|marine|soldier|\b19[0-5]\d\b|"
    r"black and white|portrait|logo|billboard|monument sign", re.I)

AUTO_JOBS = [
    dict(iso="AW", old="阿勒科克国家公园游客中心", new="阿勒科克国家公园",
         kw="Arikok National Park Aruba", must="arikok",
         pick="Cactus Scrub Cereus Northern Loop Arikok National Park Santa Cruz Aruba.jpg",
         why="原图是公园游客中心门面，画面里没有公园；换成园内仙人掌灌丛"),
    dict(iso="EE", old="亚历山大·涅夫斯基主教座堂马赛克", new="亚历山大·涅夫斯基主教座堂",
         kw="Alexander Nevsky Cathedral Tallinn", must="alexander nevsky",
         pick="Alexander Nevsky Cathedral, Tallinn.jpg",
         why="原图是教堂墙面的马赛克拼贴局部；换成五穹顶正面全景"),
    dict(iso="ES", old="塞维利亚大教堂山花入口", new="塞维利亚大教堂",
         kw="Seville Cathedral Giralda", must="sevilla",
         pick="Sevilla Cathedral And Giralda Tower (25573987).jpeg",
         why="原图是大教堂侧门山花，认不出是哪座建筑；换成大教堂与吉拉尔达塔"),
    dict(iso="FM", old="1931年特鲁克泻湖沉船旁警官", new="特鲁克泻湖沉船",
         kw="Chuuk Lagoon shipwreck", must="truk",
         pick="Bow gun of the Fujikawa Maru wreck, Truk Lagoon, Micronesia.jpg",
         why="原图是 1931 年日本托管时期的警察黑白照；换成富士川丸的舰炮"),
    dict(iso="GI", old="圣米迦勒岩洞内的舞台", new="圣米迦勒岩洞",
         kw="St Michael's Cave Gibraltar", must="michael",
         pick="Lower St. Michael Cave, Gibraltar.jpg",
         why="原图是岩洞里搭的演出舞台与座椅；换成下洞的钟乳石厅"),
    dict(iso="IM", old="马恩岛山峰斯奈费尔山顶牌匾", new="斯奈费尔山",
         kw="Snaefell Isle of Man", must="snaefell",
         pick="Isle of Man Sunrise from Snaefell.jpg",
         why="原图是山顶一块纪念牌匾特写；换成山顶日出全景"),
    dict(iso="MZ", old="戈龙戈萨国家公园大门", new="戈龙戈萨国家公园",
         kw="Gorongosa National Park Mozambique", must="gorongosa",
         pick="Mount Gorongosa, Gorongosa National Park, Mozambique (46316615492).jpg",
         why="原图是公园收费大门；换成果龙戈萨山主峰"),
    dict(iso="NG", old="扬卡里野生动物保护区入口大门", new="扬卡里国家公园",
         kw="Yankari National Park Nigeria", must="yankari",
         pick="Wikki Warm Spring in Yankari Game Reserve 01.jpg",
         why="原图是保护区入口铁门；换成园内 Wikki 温泉"),
    dict(iso="PR", old="荧光海湾建筑入口废墟", new="蚊子湾荧光海湾",
         kw="Mosquito Bay Vieques bioluminescent", must="vieques",
         pick="Mosquito bay, Bioluminescent bay, Vieques - panoramio (1).jpg",
         why="原图是海湾边一栋废墟建筑的门洞；换成夜里的荧光海湾"),
    dict(iso="SM", old="圣马力诺公共宫自由牌匾", new="圣马力诺公共宫",
         kw="Palazzo Pubblico San Marino", must="palazzo pubblico",
         pick="Palazzo Pubblico (San Marino).jpg",
         why="原图是门楣上刻着 LIBERTAS 的牌匾；换成公共宫正立面"),
    dict(iso="UM", old="中途岛环礁NSGA标志牌", new="中途岛环礁",
         kw="Midway Atoll", must="midway",
         pick="Midway Atoll - Aerial photo on Sept. 1, 2016.jpg",
         why="原图是 NSGA 基地的指示牌；换成环礁航拍"),
]

# 查过候选池，Commons 上确实没有能替换的（保持原样，不再折腾）
AUTO_SKIPPED = [
    ("MV", "马尔代夫水下餐厅",
     "伊塔餐厅在 Commons 只有两张：一张线稿剖面图、一张就是现在的内部照"),
]


def load_rec(iso):
    path = os.path.join(ROOT, "data", "%s.json" % iso.lower())
    if not os.path.exists(path):
        return None, None, "data/%s.json 不存在" % iso.lower()
    with io.open(path, encoding="utf-8") as f:
        return path, json.load(f), None


def find_target(rec, old_cap, new_cap):
    """按旧题注定位条目；返回 (target, 已换过的提示, 错误)。"""
    imgs = rec.get("landmark_images") or []
    for it in imgs:
        if (it.get("caption_zh") or "").strip() == old_cap:
            return it, None, None
    if any((it.get("caption_zh") or "").strip() == new_cap for it in imgs):
        return None, "已是「%s」，跳过" % new_cap, None
    return None, None, "找不到题注「%s」" % old_cap


def do_swap(iso, path, rec, target, name, meta, new_cap, why, tier="handpicked"):
    """下载 + 转码 + 删旧图 + 改写条目。返回 (成功?, 说明)。"""
    slug = iso.lower()
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
        return False, "下载/转码失败 %s" % str(e)[:40]

    # 删旧文件（三份），编号不复用：旧编号留着会让浏览器继续吃缓存
    old_url = target.get("url") or ""
    if old_url:
        base = os.path.splitext(os.path.join(ROOT, old_url))[0]
        for suf in (".jpg", ".jpeg", ".png", ".JPG", ".webp", ".thumb.webp"):
            p = base + suf
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
        "pick_tier": tier,
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
    return True, detail_rel


def auto_pick(job):
    """为这处地标找一张替换图，返回 [(name, meta, tier)]。

    有 pick（人工从候选里挑定的文件名）就直取 —— 这样能选「岩洞内景」这种
    文件名命中 NOT_REP、但画面恰好正确的图；没 pick 才走检索 + 两道闸：
      闸①  must：文件名里必须有这处地标的专名 —— 宁可换不到，也不换错；
      闸②  NOT_REP：命中 游客中心/入口/牌匾/内部/马赛克/山花/舞台 一律淘汰。
    第二档（不含专名的候选）只在闸①全军覆没时兜底，标 tier=relaxed 提醒复核。
    """
    if job.get("pick"):
        name = job["pick"]
        info = F.C.commons_imageinfo([name], width=1400)
        meta = F.C.lookup_meta(info, name)
        if not meta or not meta.get("remote_url"):
            return [], ["指定文件取不到元数据：%s" % name]
        return [(name, meta, "handpicked")], []

    entry = {"zh": job["new"], "kw": job["kw"], "must": job["must"]}
    if job.get("avoid"):
        entry["avoid"] = job["avoid"]
    picks, errs = F.ranked_picks(entry)
    if not picks:
        return [], errs

    strict, relaxed = [], []
    for p in picks:
        name = p["name"]
        low = name.replace("_", " ").lower()
        if NOT_REP.search(low):
            continue
        if (p["meta"].get("width") or 0) < 900:
            continue
        if F.must_ok(name, job["must"]):
            strict.append((name, p["meta"], "strict"))
        else:
            relaxed.append((name, p["meta"], "relaxed"))
    return (strict or relaxed)[:3], errs


def run_auto(dry=True):
    print("非代表作替换：%d 处%s\n" % (len(AUTO_JOBS), "  [预演，不落盘]" if dry else ""))
    done, failed = 0, []
    for job in AUTO_JOBS:
        head = "%s %s" % (job["iso"], job["old"])
        path, rec, err = load_rec(job["iso"])
        if err:
            failed.append((head, err))
            continue
        target, note, err = find_target(rec, job["old"], job["new"])
        if note:
            print("  · %-30s %s" % (head, note))
            continue
        if err:
            failed.append((head, err))
            continue

        picks, errs = auto_pick(job)
        if not picks:
            failed.append((head, "没有通过双闸的候选（%s）" % ("；".join(errs[:2]) or "候选 0 个")))
            continue
        name, meta, tier = picks[0]
        tag = "  ⚠ 文件名不含专名，需人工复核" if tier == "relaxed" else ""
        if dry:
            print("  · %-30s → %s  [%s]%s" % (head, name[:52], tier, tag))
            for n2, _m, t2 in picks[1:]:
                print("        备选 %s" % n2[:60])
            continue
        ok, msg = do_swap(job["iso"], path, rec, target, name, meta,
                          job["new"], job["why"], tier=tier)
        if ok:
            print("  ✓ %-30s → %-52s %s" % (head, msg, tag))
            done += 1
        else:
            failed.append((head, msg))

    if not dry:
        print("\n替换 %d 张，失败 %d 张" % (done, len(failed)))
    for h, w in failed:
        print("  x %-30s %s" % (h, w))
    if AUTO_SKIPPED:
        print("\n查过候选池、没有可换的（保持原样）：")
        for iso, zh, why in AUTO_SKIPPED:
            print("  - [%s] %-14s %s" % (iso, zh, why))
    return len(failed)


def main():
    only = {x.upper() for x in sys.argv[1:] if not x.startswith("-")}
    dry = "--dry-run" in sys.argv
    do_auto = "--auto" in sys.argv

    if do_auto:
        # 自动模式默认预演，必须显式 --apply 才落盘
        return 0 if run_auto(dry=not ("--apply" in sys.argv)) == 0 else 1

    done, failed = 0, []
    for iso, old_cap, name, new_cap, why in JOBS:
        if only and iso not in only:
            continue
        path, rec, err = load_rec(iso)
        if err:
            failed.append((iso, err))
            continue
        target, note, err = find_target(rec, old_cap, new_cap)
        if note:
            print("  · %-3s %-20s %s" % (iso, old_cap[:20], note))
            continue
        if err:
            failed.append((iso, err))
            continue

        if dry:
            print("  · %-3s %-20s → %-20s  %s" % (iso, old_cap[:20], new_cap, name[:44]))
            continue

        info = F.C.commons_imageinfo([name], width=1400)
        meta = F.C.lookup_meta(info, name)
        if not meta or not meta.get("remote_url"):
            failed.append((iso, "取不到元数据：%s" % name))
            continue
        ok, msg = do_swap(iso, path, rec, target, name, meta, new_cap, why)
        if ok:
            print("  ✓ %-3s %-20s → %-18s %s" % (iso, old_cap[:20], new_cap, msg))
            done += 1
        else:
            failed.append((iso, msg))

    print("\n替换 %d 张，失败 %d 张" % (done, len(failed)))
    for iso, why in failed:
        print("  x %-3s %s" % (iso, why))
    if done:
        print("\n下一步：python scripts/build_cards.py → upload_obs --only data → 传新增 assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
