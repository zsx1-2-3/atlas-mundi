#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成球面地图标注数据 geo/labels.json。

数据来源：Natural Earth 1:50m Admin 0 Countries（公有领域）。
关键是用它自带的**制图字段**，而不是自己猜位置：

  LABEL_X / LABEL_Y  制图师放标签的锚点（不是几何中心 —— 挪威的几何中心在海上）
  MIN_LABEL          该标签从第几级缩放开始出现（Natural Earth 的 zoom 级别）
  LABELRANK          重要度，1 最高。碰撞时先保小的

这正好是「像谷歌地图一样按缩放显示地名」该有的输入，不用自己编规则。
大洲与大洋没有现成数据，用手写锚点（放在比较空的海面/大陆中部，避开国名）。

用法：
    python scripts/build_labels.py
输出：geo/labels.json（约 12KB）
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "geo", "_ne_50m_countries.geojson")
OUT = os.path.join(ROOT, "geo", "labels.json")

# 站内已收录、可点开档案的国家：不参与缩放过滤，永远显示且优先级最高
FEATURED = {"CN", "FR", "US"}

# 地图上不写全称。Natural Earth 的中文名是官方全称，世界缩放下太长
SHORT = {
    "中华人民共和国": "中国",
    "大韩民国": "韩国",
    "朝鲜民主主义人民共和国": "朝鲜",
    "伊朗伊斯兰共和国": "伊朗",
    "阿拉伯联合酋长国": "阿联酋",
    "阿拉伯叙利亚共和国": "叙利亚",
    "老挝人民民主共和国": "老挝",
    "委内瑞拉玻利瓦尔共和国": "委内瑞拉",
    "玻利维亚多民族国": "玻利维亚",
    "坦桑尼亚联合共和国": "坦桑尼亚",
    "刚果民主共和国": "刚果（金）",
    "多米尼加共和国": "多米尼加",
    "捷克共和国": "捷克",
    "中非共和国": "中非",
    "大不列颠及北爱尔兰联合王国": "英国",
    "俄罗斯联邦": "俄罗斯",
    "美利坚合众国": "美国",
    # 台湾、香港、澳门是中国的一部分，标注按「中国XX」书写；
    # Natural Earth 把它们当成独立要素（台湾的 NAME_ZH 甚至还写着旧政权名），
    # 这里必须改正 —— 地球上的国界数据已经把它们并进中国了，标注不能自相矛盾。
    "中华民国": "中国台湾",
    "台湾": "中国台湾",
    "香港": "中国香港",
    "澳门": "中国澳门",
}

# 大洲与大洋：锚点选在大陆中部或空海面，避开国名密集区
CONTINENTS = [
    ("亚洲", 88, 44, 0.0, 0),
    ("欧洲", 16, 53, 0.0, 1),
    ("非洲", 20, 3, 0.0, 2),
    ("北美洲", -101, 46, 0.0, 3),
    ("南美洲", -60, -14, 0.0, 4),
    ("大洋洲", 134, -25, 0.0, 5),
    ("南极洲", 0, -78, 0.0, 6),
]

# 四个大字 + 一片"更细的海"。z=0 的永远在，z>0 的放大后才浮出来。
OCEANS = [
    ("太平洋", -152, 8, 0.0, 10),
    ("大西洋", -32, 22, 0.0, 11),
    ("印度洋", 78, -26, 0.0, 12),
    ("北冰洋", -20, 84, 0.0, 13),
    ("南大洋", 20, -58, 0.0, 14),
    # 边缘海 / 海湾：放大后才出现
    ("南海", 114.0, 15.0, 3.0, 20),
    ("地中海", 18.0, 35.0, 3.0, 21),
    ("阿拉伯海", 63.0, 15.0, 3.2, 22),
    ("加勒比海", -75.0, 15.0, 3.2, 23),
    ("东海", 125.0, 29.0, 3.4, 24),
    ("日本海", 134.5, 40.0, 3.4, 25),
    ("鄂霍次克海", 150.0, 55.0, 3.4, 26),
    ("白令海", -178.0, 57.0, 3.4, 27),
    ("红海", 38.5, 20.0, 3.4, 28),
    ("孟加拉湾", 88.0, 15.0, 3.4, 29),
    ("墨西哥湾", -90.0, 25.0, 3.4, 30),
    ("珊瑚海", 155.0, -18.0, 3.4, 31),
    ("黄海", 123.0, 35.5, 3.6, 32),
    ("黑海", 34.0, 43.0, 3.6, 33),
    ("波斯湾", 51.5, 27.0, 3.6, 34),
    ("北海", 3.0, 56.0, 3.8, 35),
    ("波罗的海", 19.0, 57.0, 3.8, 36),
    ("巴伦支海", 40.0, 73.0, 3.6, 37),
    ("挪威海", 2.0, 66.0, 3.8, 38),
    ("塔斯曼海", 160.0, -38.0, 3.6, 39),
    ("几内亚湾", 3.0, 0.0, 3.6, 40),
    ("阿拉斯加湾", -145.0, 56.0, 3.6, 41),
    ("安达曼海", 96.0, 10.0, 3.8, 42),
    ("帝汶海", 128.0, -11.0, 3.8, 43),
    ("爱琴海", 25.0, 38.0, 4.2, 44),
    ("亚得里亚海", 15.5, 43.0, 4.2, 45),
]



# ---------------------------------------------------------------------------
# 岛屿 / 湖泊 / 海峡：Natural Earth 的国家图层里没有这些，逐条手写。
# 只要"重要的、缩放拉近之后才需要看的"—— 几十条足矣，多了必然糊。
# 第四个数字是起始缩放级（NE zoom，越大越晚出现）。
# 远离本土的岛在后面用括号标国属：关岛（美国）、新喀里多尼亚（法国）。
# 有主权争议的一律不标国属（福克兰/马尔维纳斯这类直接不收）。
# ---------------------------------------------------------------------------
ISLANDS = [
    ("夏威夷（美国）", -155.5, 19.6, 3.2),
    ("纽芬兰（加拿大）", -56.1, 48.6, 3.4),
    ("巴芬岛（加拿大）", -70.0, 68.0, 3.6),
    ("斯瓦尔巴（挪威）", 15.6, 78.2, 3.6),
    ("法罗群岛（丹麦）", -6.9, 62.0, 3.8),
    ("百慕大（英国）", -64.8, 32.3, 3.8),
    ("亚速尔群岛（葡萄牙）", -25.5, 37.8, 3.8),
    ("加那利群岛（西班牙）", -16.0, 28.2, 3.8),
    ("留尼汪（法国）", 55.5, -21.1, 3.8),
    ("新喀里多尼亚（法国）", 165.6, -21.3, 3.8),
    ("复活节岛（智利）", -109.3, -27.1, 4.2),
    ("迪戈加西亚（英属）", 72.4, -7.3, 4.4),
    ("关岛（美国）", 144.8, 13.4, 3.6),
    ("塞班岛（美国）", 145.75, 15.2, 4.0),
    ("中途岛（美国）", -177.4, 28.2, 4.4),
    ("冲绳岛（日本）", 127.9, 26.4, 4.0),
    ("库页岛（俄罗斯）", 143.0, 51.0, 3.8),
    ("海南岛", 109.8, 19.2, 3.6),
    ("加里曼丹岛", 113.0, 0.5, 3.4),
    ("苏门答腊岛（印尼）", 102.0, -1.0, 3.4),
    ("爪哇岛（印尼）", 110.5, -7.3, 3.6),
    ("新几内亚岛", 141.0, -5.0, 3.4),
    ("吕宋岛（菲律宾）", 121.0, 16.0, 3.8),
    ("棉兰老岛（菲律宾）", 122.5, 7.5, 4.0),
    ("火地岛（阿根廷·智利）", -68.0, -54.0, 3.6),
]

LAKES = [
    ("里海", 50.5, 42.0, 2.8),
    ("贝加尔湖", 108.0, 53.5, 3.2),
    ("苏必利尔湖", -87.8, 47.6, 3.2),
    ("维多利亚湖", 33.0, -1.0, 3.4),
    ("拉多加湖", 31.5, 61.0, 3.6),
    ("坦噶尼喀湖", 29.6, -6.0, 3.6),
    ("密歇根湖", -87.0, 44.0, 3.6),
    ("的的喀喀湖", -69.3, -15.8, 3.8),
    ("青海湖", 100.2, 36.9, 3.8),
    ("马拉维湖", 34.5, -12.0, 3.8),
    ("死海", 35.5, 31.5, 4.0),
    ("休伦湖", -82.4, 44.8, 3.6),
    ("伊利湖", -81.2, 42.2, 3.8),
    ("安大略湖", -77.8, 43.6, 4.0),
    ("大熊湖", -121.0, 65.0, 3.8),
    ("大奴湖", -116.0, 61.5, 3.8),
    ("温尼伯湖", -98.0, 53.0, 3.8),
    ("巴尔喀什湖", 74.5, 46.3, 3.8),
    ("咸海", 59.5, 45.0, 3.8),
    ("奥涅加湖", 35.5, 61.7, 4.0),
    ("卡里巴湖", 28.8, -17.0, 4.2),
    ("尼加拉瓜湖", -85.4, 11.6, 4.2),
    ("鄱阳湖", 116.3, 29.1, 4.2),
    ("洞庭湖", 112.9, 29.3, 4.4),
    ("图尔卡纳湖", 36.1, 3.6, 4.4),
]

STRAITS = [
    ("马六甲海峡", 100.5, 2.5, 3.4),
    ("白令海峡", -169.0, 65.8, 3.6),
    ("德雷克海峡", -63.0, -58.0, 3.6),
    ("英吉利海峡", 0.5, 50.2, 3.8),
    ("莫桑比克海峡", 41.0, -18.0, 3.8),
    ("直布罗陀海峡", -5.6, 35.95, 3.8),
    ("台湾海峡", 119.5, 24.5, 4.0),
    ("霍尔木兹海峡", 56.4, 26.6, 4.0),
    ("曼德海峡", 43.4, 12.6, 4.2),
    ("朝鲜海峡", 129.0, 34.0, 4.2),
    ("麦哲伦海峡", -70.5, -53.5, 4.2),
    ("苏伊士运河", 32.3, 30.5, 4.2),
    ("巴拿马运河", -79.6, 9.1, 4.2),
    ("巴士海峡", 121.0, 21.0, 4.4),
    ("博斯普鲁斯海峡", 29.0, 41.1, 4.4),
]

def main() -> int:
    with open(SRC, encoding="utf-8") as f:
        data = json.load(f)

    countries = []
    pops = {}

    # 同 ISO 多要素（AU = Australia / Indian Ocean Ter. / Ashmore）只留人口最多的，
    # 否则球上会同时冒出"澳大利亚"和"澳屬印度洋領地"两个名字，位置还都在澳洲附近。
    # 主权国索引：谁管着谁。
    # NE 没有"属地"这种字段，只有 SOVEREIGNT。麻烦在于 SOVEREIGNT 有时填的是
    # 同一个国家的全称（Tanzania 的 SOVEREIGNT 是 "United Republic of Tanzania"，
    # Micronesia 是 "Federated States of Micronesia"），直接比字符串会把它们
    # 全判成属地。所以改用反查：拿 SOVEREIGNT 去查它自己的 ISO，查得到、
    # 且和自己的 ISO 不同，才算"这块归别人管"。
    sov_iso = {}
    for feat in data["features"]:
        q = feat.get("properties") or {}
        nm = (q.get("NAME") or "").strip()
        c2 = (q.get("ISO_A2") or "").upper()
        if len(c2) != 2:
            c2 = (q.get("ISO_A2_EH") or "").upper()
        if nm and len(c2) == 2 and nm not in sov_iso:
            sov_iso[nm] = c2

    # 反查不出来的手工钉：索马里兰在 NE 里是独立要素、没有 ISO 码，
    # SOVEREIGNT 写的是它自己。没人为它单独立档，点了只会得到一个空壳，
    # 所以归到索马里。政治上不该替用户做归属判断的（巴勒斯坦），一律不给。
    PARENT_FIX = {"Somaliland": "SO"}
    PARENT_SKIP = {"PS", "EH"}

    best = {}
    for idx, feat in enumerate(data["features"]):
        q = feat.get("properties") or {}
        code = (q.get("ISO_A2") or "").upper()
        if len(code) != 2:
            code = (q.get("ISO_A2_EH") or "").upper()
        if len(code) != 2:
            continue
        try:
            pop = int(q.get("POP_EST") or 0)
        except (TypeError, ValueError):
            pop = 0
        if code not in best or pop > best[code][0]:
            best[code] = (pop, idx)

    for idx, feat in enumerate(data["features"]):
        p = feat["properties"]
        lon, lat = p.get("LABEL_X"), p.get("LABEL_Y")
        if lon is None or lat is None:
            continue
        name = p.get("NAME_ZH") or p.get("NAME")
        if not name:
            continue
        # France / Norway / Kosovo 的 ISO_A2 是 -99，要用 ISO_A2_EH
        iso = (p.get("ISO_A2") or "").upper()
        if len(iso) != 2:
            iso = (p.get("ISO_A2_EH") or "").upper()
        if best.get(iso) and best[iso][1] != idx:
            continue                     # 同 ISO 的非主要素，跳过
        name = SHORT.get(name, name)
        # 人口（NE 的 POP_EST）：只用于卡片决定"要不要给地区语言"——
        # 小国只列官方语言，地区语言纯属噪音。
        try:
            pop = int(p.get("POP_EST") or 0)
        except (TypeError, ValueError):
            pop = 0
        if len(iso) == 2 and pop > 0:
            pops[iso] = pop
        # 落在谁名下：有 iso 且和主权国不同，或者压根没 iso（索马里兰这类）。
        parent = ""
        if iso not in PARENT_SKIP:
            parent = PARENT_FIX.get((p.get("NAME") or "").strip(), "")
            # 台湾按"中国的一部分"处理：点它直接开中国的档案。
            # Natural Earth 把它当独立要素、SOVEREIGNT 也不指向中国，只能单列。
            if not parent and iso == "TW":
                parent = "CN"
            if not parent:
                cand = sov_iso.get((p.get("SOVEREIGNT") or "").strip(), "")
                if cand and cand != iso:
                    parent = cand

        countries.append({
            "n": name,
            "lon": round(float(lon), 2),
            "lat": round(float(lat), 2),
            # MIN_LABEL 是 NE 的 zoom 级；LABELRANK 越小越重要
            "z": float(p.get("MIN_LABEL") or 5),
            "r": int(p.get("LABELRANK") or 6),
            # 所有国家都给 iso：站点的定位是"所有国家"，地名要能点开基础档案。
            # d 表示"已有完整多模态档案"（目前三家），只影响样式与优先级。
            "iso": iso if len(iso) == 2 else "",
            "d": 1 if iso in FEATURED else 0,
            "parent": parent,
        })

    countries.sort(key=lambda c: (c["r"], c["z"], -len(c["n"])))
    print("国家标注 %d 条（其中站内可点 %d 条，带国属 %d 条）"
          % (len(countries), sum(1 for c in countries if c["iso"]),
             sum(1 for c in countries if c.get("parent"))))
    print("  带国属的：", "、".join("%s→%s" % (c["n"], c["parent"])
                                for c in countries if c.get("parent"))[:160])

    # 策展的岛屿如果和 NE 的国家条目重名（关岛、新喀里多尼亚…），
    # 用策展名替换国家名 —— 否则会出现"关岛"和"关岛（美国）"两条，
    # 而屏幕上留哪条取决于碰撞顺序，用户看到的往往是没国属的那条。
    suffixed = {}
    for name, lon, lat, z in ISLANDS:
        base = name.split("（")[0]
        if base != name:
            suffixed[base] = name
    have = set()
    for c in countries:
        if c["n"] in suffixed:
            c["n"] = suffixed[c["n"]]
        have.add(c["n"])

    feats = []
    for name, lon, lat, z in ISLANDS:
        if name in have:
            continue          # 已经并进国家条目里了（带国属的那条）
        feats.append({"n": name, "lon": lon, "lat": lat, "z": z, "k": "i"})
    for name, lon, lat, z in LAKES:
        feats.append({"n": name, "lon": lon, "lat": lat, "z": z, "k": "l"})
    for name, lon, lat, z in STRAITS:
        feats.append({"n": name, "lon": lon, "lat": lat, "z": z, "k": "s"})
    feats.sort(key=lambda f: f["z"])
    print("岛屿/湖泊/海峡 %d 条（岛 %d · 湖 %d · 海峡 %d）"
          % (len(feats), len(ISLANDS), len(LAKES), len(STRAITS)))

    out = {
        "note": "锚点/缩放级别取自 Natural Earth LABEL_X/LABEL_Y/MIN_LABEL/LABELRANK",
        "features": feats,
        "countries": countries,
        "pops": pops,
        "continents": [{"n": n, "lon": x, "lat": y, "z": z, "r": r}
                       for (n, x, y, z, r) in CONTINENTS],
        "oceans": [{"n": n, "lon": x, "lat": y, "z": z, "r": r}
                   for (n, x, y, z, r) in OCEANS],
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("→ %s  %.1f KB" % (os.path.basename(OUT), os.path.getsize(OUT) / 1024))

    # 抽查几个：世界级缩放该看到的大国
    print("  最小 MIN_LABEL：", sorted({c["z"] for c in countries})[:6])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
