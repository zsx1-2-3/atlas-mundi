#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寰宇图志 · 按人工清单补齐地标图

用法：
    python scripts/fill_images.py CN FR US
    python scripts/fill_images.py --all

为什么需要这一步：
    Wikidata P18 每个国家只给 1 张「代表图」，而且不保证是地标
    （美国给的是 UNOCHA 的定位地图）。自动挑选也无法保证挑中"最著名的那一张"。

    所以把「要哪几处地标」写进 data/landmarks.json，由人工指定，
    脚本按关键词去 Commons 搜图，取回后写死中文图注。
    这样图注是中文的、顺序是可控的、封面是选出来的。

流程：
    data/landmarks.json (关键词 + 中文图注)
      -> Commons list=search 搜 File 命名空间
      -> 过滤地图/旗帜/徽章 + 要求有授权 + 要求宽度 >= 800
      -> 下载到 assets/{iso}/L01.jpg ... L04.jpg
      -> 覆写 data/{iso}.json 的 landmark_images，caption_zh 直接写中文
      -> 之后跑 optimize_images.py 压成 WebP
"""

import argparse
import importlib.util
import json
import re
import os
import sys

# 控制台可能是 cp936（Windows cmd 默认），打不出的字符用 ? 代替，别让整段崩掉。
# 之前 collect.py 在连不上某个站时打印一个 ✗ 就 UnicodeEncodeError 退出，
# 看起来像"采集失败"，其实连抓取都还没开始。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAPS = os.path.join(ROOT, "data", "_image_gaps.json")

# 复用 collect.py 的请求工具与图片过滤，避免两套实现漂移
_spec = importlib.util.spec_from_file_location(
    "atlas_collect", os.path.join(ROOT, "scripts", "collect.py"))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MIN_WIDTH = 900          # 低于这个宽度在详情面板里会糊
MAX_ASPECT = 2.2         # 超过这个宽高比是全景长条（5:1 的故宫全景），主体被压成一条
TARGET_ASPECT = 1.45     # 面板是 4:3，越接近越好
MAX_PER_COUNTRY = 5
MAX_CANDIDATES = 26      # 单个地标最多取这么多个候选去做 imageinfo，再多就慢了

# 上限不是"每国必须凑满 5 张"。小国本来就没几处名胜，硬凑出来的第 4、5 张
# 往往是市政厅、街景、或者一张跟地标无关的游客照。
#
# 但**不能用人口去猜**。一开始写的是"人口不到 100 万封顶 3 张"，被冰岛当场打脸：
# 冰岛 37 万人，蓝湖 / 黄金瀑布 / 间歇泉 / 辛格维利尔 / 杰古沙龙 / 大教堂，
# 六处都是真名胜，凭什么只给 3 张？反过来卢森堡 65 万人确实只有两处可写。
# 人口和文化资源根本不相关。
#
# 现在改成不设国家级上限：能过质量闸门几张就是几张。"小国少"是筛出来的结果，
# 不是预设的规则 —— 基里巴斯就算硬凑 6 条清单，卫星图和海底地形图会被下面的
# 分类闸门挡掉，最后能剩几张就几张。
MAX_PER_COUNTRY = 5
# 放宽档（relaxed / loose）拿到的图最多留几张。放宽才拿到的，往往是"能用但一般"，
# 一国的画廊里有两张兜底就够了，再多就是在教用户看烂图。
LOW_TIER_CAP = 2

# 三档筛选。第一档是"能进画廊"的标准；后两档只在前一档全军覆没时启用。
# 起因：吉萨金字塔群只有一张 "Pyramids of the Giza Necropolis.jpg"，宽高比 2.4，
# 被 2.2 的硬线判成"全景"直接淘汰 —— 结果埃及那一栏没有金字塔。
# 宁可给一张略宽的全景，也比给用户一个空框强。
TIERS = [
    ("strict", {"min_w": MIN_WIDTH, "max_ar": MAX_ASPECT}),
    ("relaxed", {"min_w": 800, "max_ar": 3.2}),
    ("loose", {"min_w": 640, "max_ar": 4.5}),
]
# 编辑挑过的图（P18 / 维基首图）优先于关键词命中：加个权重，分数打平时它赢
SOURCE_BONUS = {"wikidata_p18": 2.5, "wikipedia_lead": 2.0, "article_img": 1.4,
                "en_wiki": 1.0, "commons_search": 0.0}
CURATED = ("wikidata_p18", "wikipedia_lead")

# 关键词命中里混进来的"合影照"：主体是雕塑/展陈/复原模型，地标只是背景。
# 实测搜 "Great Pyramids of Giza" 的第一名是 Forever is Now 艺术展那件巨型金属手，
# 金字塔在远处小得像三颗牙。这类文件名直接毙掉，不给它进候选的机会。
JUNK_WORDS = (
    "exhibition", "installation", "artwork", "sculpture", "replica",
    "model of", "miniature", "painting", "drawing", "engraving",
    "stamp", "banknote", "coin", "logo", "coat of arms", "poster",
    "panorama of", "collage", "montage",
    # 人物照：搜 "Abaiang" 会返回 "Mayor of the Abaiang Island Council (10702...).jpg"
    # —— 一个村长在开会，和海岛风景没有关系
    "mayor", "governor", "minister", "president of", "prime minister",
    "politician", "portrait of", "signing", "ceremony",
    # 普通办公/公共设施：小国的地标清单本来就是"环礁、小岛"这种非建筑对象，
    # 全文搜索能给到的只有办公楼 —— 实测阿拜昂环礁配到 "Abaiang Council Office.jpg"，
    # 巴纳巴岛配到 "Pacific Phosphate Company Offices"。这些不是地标。
    "office", "council", "headquarters", "company", "factory", "warehouse",
    "town hall", "city hall", "municipal", "government", "clinic", "hospital",
    "post office", "school building", "prison", "barracks",
    # 2026-11 补：用户报"多伦多是施工图""亚丁港是明信片""基里巴斯是黑白军队照"，
    # 这三类在旧名单里一个都没有。
    "construction", "under construction", "groundbreaking", "scaffolding",
    "postcard", "post card", "greeting card",
    "battle", "battlefield", "military", "army", "navy", "soldier", "troops",
    "regiment", "air force", "war ", "wwii", "world war",
    # 蝴蝶这类特写：坦桑尼亚"桑给巴尔海滩"配到一只柑橘凤蝶，名字里带 Zanzibar
    # 所以 must 校验通过了，画面上却是一只虫。
    "butterfl", "lepidopter", "moth", "dragonfl", "beetle",
    "papilio", "swallowtail", "birdwing", "danainae",   # 蝴蝶的属名/科名
    # 博物馆藏画：加拿大"尼亚加拉大瀑布"配到布鲁克林博物馆的油画
    "museum -", "oil on canvas", "watercolou?r", "gouache", "lithograph",
)

# 卫星影像 / 地图 / 海底地形 / 科学示意图。尺寸、比例、文件名全都合规，
# 画面上却没有一处地标 —— 只能靠 Commons 的分类来认。
#
# 基里巴斯是最典型的反面教材，P18 给的三张分别是：
#   Tarawa, Kiribati ESA22224800.jpeg  → Category: Satellite pictures of Kiribati
#   Kiritimati-EO.jpg                  → Category: Satellite pictures of Kiribati
#   3dPerspective wSeamounts NoRefs.jpg→ Category: National Geophysical Data Center
# 三张全是废图，而旧的 is_bad_image 只查文件名里的 map/flag/logo，一张都拦不住。
BAD_CAT_RE = re.compile(
    r"satellite|sentinel-?2|landsat|copernicus|earth observation|earth observ|"
    r"expedition \d+ crew|topographic|bathymetr|seamount|geophysical data|"
    r"diagrams? of|charts? of|graphs? of|3d ?perspective|"
    r"nasa|esa files|astronaut|space station|taken from the iss|"
    # 档案照/历史图版：基里巴斯的"圣诞岛"配到 1967 年 Smithsonian 的黑白鸟类图版
    r"historical image|archival|photographs$|illustrations$", re.I)

# 说明里出现这些，基本可以断定画面主体是普通设施而不是景观。
# 不直接用 building —— 宗堡、教堂、宫殿本身就是地标，会误伤。
FACILITY_RE = re.compile(
    r"airport|airstrip|terminal building|office building|council office|"
    r"warehouse|factory|power station|power plant|post office|police station|"
    r"fire station|residential|housing estate|shop|supermarket|petrol station", re.I)

# 活动照 / 事件照：这些画面里有地标所在的地名，但主体是人和事，不是景。
# 菲律宾「长滩岛白沙滩」就配到一张 "Boracay Cleanup EMB DENR R6.jpg" ——
# 名字里确实有 Boracay，must 校验通过了，可那是一群人在捡垃圾。
# 地标卡片放这种图，用户第一反应是"这也算地标？"
EVENT_RE = re.compile(
    r"clean ?up|clean-up|coastal clean|beach clean|"
    r"trash|rubbish|litter|garbage|waste collection|"
    r"tree planting|planting (?:activity|drive)|reforestation drive|"
    r"protest|demonstration|strike|riot|"
    r"ribbon cutting|groundbreaking|inaugurat|turnover ceremony|"
    r"conference|forum|workshop|seminar|"  # 不收 summit：山峰照片文件名里到处都是它
    r"awareness|observance|celebration of|earth day|environment day|"
    r"volunteers|volunteering|donation|relief operation|"
    # 人物主体照：地名对得上，但画面里全是人 ——
    # 长滩岛就抓到一张 "Ati community in Boracay.jpg"（原住民社区合影）
    r"community|villagers|residents|portrait|children|students|"
    r"crowd|people|workers|fishermen|farmers", re.I)

# 不管来自哪个池（Wikidata 编辑图 / 条目配图 / 关键词搜索）都不该出现的东西。
# 2026-11-04 的教训：这些词之前只写在 JUNK_WORDS 里，而 is_junk() 只作用于
# 「关键词搜索」这一个池 —— 于是 article_img 池里的施工照、邮票、博物馆藏油画、
# 蝴蝶特写一张都没被拦住，全部进了正式档案。
# 注意不要放 prison/office/school/army：罗本岛本身就是监狱博物馆、
# 兵马俑的英文名就叫 Terracotta Army，一刀切会误伤真地标。
HARD_BAD_RE = re.compile(
    r"construct|groundbreak|scaffold|postcard|post card|greeting card|"
    r"battle|battlefield|military|soldier|troops|regiment|"
    r"butterfl|lepidopter|papilio|swallowtail|moth|dragonfl|beetle|"
    r"museum -|oil on canvas|watercolou?r|gouache|lithograph|woodcut|"
    # 只排「painting process」这种施工彩绘，不排 painting 本身 ——
    # 措迪洛山岩画（Tsodilo Hills rock paintings）、巴米扬洞窟壁画
    # （GROTTO PAINTINGS）都是真遗产，一刀切会把它们也砍掉。
    r"painting process|painting work in progress|"
    r"postage stamp|stamp of|admiralty chart|nautical chart", re.I)

# 文件名层面的同一件事，成本更低，先用它挡一道
BAD_NAME_RE = re.compile(
    r"(?<![a-z])(esa|nasa|landsat|sentinel|copernicus|bathymetry|seamount|"
    r"topographic|geological|iss)(?![a-z])", re.I)

# "LocationBanaba.PNG" 这种不加空格的定位图命名，靠词边界抓不到，只能看前缀
BAD_PREFIX_RE = re.compile(r"(?:^|[_\s(])(?:location|locations?|maps?|karte|carte|planta|opl|osm)\b|\bmap\b|map\d|map\.\w+$|kaart|\bOSM-", re.I)

# 尝试顺序：先让编辑挑过的图走严格档，不过就放宽一档；关键词命中只接受严格档
# （放宽它会立刻引来上面那种合影照）；英文维基首图排在关键词之后 —— 它更准，
# 但"解析条目名"这一步会跑偏，只能当兜底；最后才拿编辑图的最宽档兜底。
ATTEMPTS = [
    ("curated", "strict"),
    ("curated", "relaxed"),
    ("article", "strict"),     # 条目正文配图：编辑挑过，比全文搜索准一个量级
    ("commons", "strict"),
    ("late", "strict"),
    ("curated", "loose"),
]


def log(msg=""):
    print(msg, flush=True)


def search_files(kw, limit=20):
    """在 Commons 的 File 命名空间里搜位图，返回候选文件名"""
    q = C.api_json(COMMONS_API, {
        "action": "query", "list": "search", "srsearch": kw + " filetype:bitmap",
        "srnamespace": "6", "srlimit": str(limit), "format": "json",
    })
    out = []
    for item in (q.get("query", {}).get("search", []) or []):
        name = (item.get("title") or "").replace("File:", "")
        if name and not C.is_bad_image(name):
            out.append(name)
    return out


def score(cand, meta, must, tier):
    """给候选打分。硬性要求（must/宽/长宽比）按档位放宽，不满足直接淘汰。
    踩过的坑：搜 "Eiffel Tower from Champ de Mars" 拿到一张鸟瞰战神广场、
    铁塔在画面外的照片；搜"故宫太和殿"拿到 5:1 的全景长条。光靠关键词不够。"""
    name = (cand or "").replace("_", " ").lower()
    if must and must.lower() not in name:
        return None, "文件名不含「%s」" % must
    w = meta.get("width") or 0
    if w < tier["min_w"]:
        return None, "宽度仅 %d px" % w
    try:
        ar = float(meta.get("aspect") or 0)
    except Exception:
        ar = 0
    if not ar:
        hh = meta.get("height") or 0
        if w and hh:
            ar = float(w) / hh
    if ar > tier["max_ar"]:
        return None, "宽高比 %.1f 过宽（全景）" % ar
    s = 0.0
    if 1.15 <= ar <= 1.85:
        s += 3
    s += max(0.0, 2 - abs(ar - TARGET_ASPECT) * 2)
    if w >= 1400:
        s += 2
    elif w >= 1100:
        s += 1
    return s, "score=%.1f ar=%.2f w=%d" % (s, ar, w)


def wikidata_lead_images(zh_name, limit=4):
    """用中文名在 Wikidata 搜条目，取 P18（代表图）文件名。

    为什么换这条路：Commons 关键词搜索靠运气 —— 搜 Mont Saint-Michel 会返回
    "Cistern of the Chaplaincy of the Mont Saint-Michel Abbey"（修道院蓄水池），
    搜 Eiffel Tower 会返回 "Sous la Tour Eiffel"（塔底仰拍）。地标名只是文件名里
    的上下文，不代表它是主体。

    P18 是维基百科词条首图用的那一张，由编辑挑过，是"最能代表该条目"的照片，
    比关键词命中要可靠得多。
    """
    out = []
    try:
        q = C.api_json("https://www.wikidata.org/w/api.php", {
            "action": "wbsearchentities", "search": zh_name, "language": "zh",
            "uselang": "zh", "type": "item", "limit": str(limit), "format": "json",
        })
    except Exception as e:
        return out, "Wikidata 搜索失败：%s" % str(e)[:60]

    for hit in (q.get("search") or []):
        qid = hit.get("id")
        if not qid:
            continue
        try:
            ent = json.loads(C.http_get(C.ENTITY_DATA % qid,
                                        accept="application/json").decode("utf-8"))
        except Exception:
            continue
        e = (ent.get("entities") or {}).get(qid)
        if not e:
            continue
        label = ((e.get("labels") or {}).get("zh", {}) or {}).get("value") or hit.get("label")
        for v in C.claim_values(e, "P18"):
            if isinstance(v, str) and not C.is_bad_image(v):
                out.append({"file": v, "qid": qid, "label": label})
        if out:
            break
    return out, None


def wikipedia_lead_image(name, lang="zh"):
    """维基词条首图。比 P18 更贴近命名（"凡尔赛宫镜厅"能直接命中镜厅词条）。"""
    try:
        d = C.api_json("https://%s.wikipedia.org/w/api.php" % lang, {
            "action": "query", "prop": "pageimages", "piprop": "name|original",
            "redirects": "1", "titles": name, "format": "json", "formatversion": "2",
        })
    except Exception:
        return None, None
    pages = (d.get("query") or {}).get("pages") or []
    if not pages:
        return None, None
    pg = pages[0]
    if pg.get("missing"):
        return None, None
    name = pg.get("pageimage")
    title = pg.get("title")
    if not name or C.is_bad_image(name):
        return None, None
    return name, title


def en_title(entry):
    """把英文检索短语解析成真正的英文条目标题。带两个守卫：
    标题必须含 must（否则斐济的 Garden of the Sleeping Giant 会解析到美国那座山），
    且不能带括号消歧义（带括号说明这个词的主话题不是它）。"""
    q = entry.get("en") or entry.get("kw") or ""
    must = (entry.get("must") or "").lower()
    try:
        d = C.api_json("https://en.wikipedia.org/w/api.php", {
            "action": "query", "list": "search", "srsearch": q,
            "srlimit": "3", "format": "json",
        })
    except Exception:
        return None
    for hit in (d.get("query", {}).get("search") or []):
        title = (hit.get("title") or "").strip()
        if not title or "(" in title:
            continue
        if must and must not in title.lower():
            continue
        return title
    return None


ICON_WORDS = ("icon", "logo", "button", "edit-", "ambox", "question book",
              "commons-", "wiktionary", "wikisource", "symbol", "emblem",
              "flag of", "coat of arms", "locator", "red pog", "blue pog")


def article_images(entry, limit=12):
    """条目正文里的配图 —— 小国唯一稳定的"像样照片"来源。

    为什么换这条：Commons 全文搜索对基里巴斯只有办公楼、机场、黑白鸟类图版，
    我连着加了三轮黑名单词，每加一条就冒出下一个词（office → council → medical center），
    这条路是无底洞。根子不在规则不够，而在**图源不对**：全文搜索不区分
    "这图是不是在讲这个地方"。
    维基条目正文里的图是编辑为了讲清这个条目才放进去的，相关性和"是照片"这两件事
    已经被人筛过一遍了。"""
    for lang, name in (("zh", (entry.get("zh") or "").strip()), ("en", en_title(entry))):
        if not name:
            continue
        try:
            d = C.api_json("https://%s.wikipedia.org/w/api.php" % lang, {
                "action": "query", "prop": "images", "titles": name,
                "imlimit": "40", "redirects": "1", "format": "json",
                "formatversion": "2"})
        except Exception:
            continue
        pages = (d.get("query") or {}).get("pages") or []
        if not pages or pages[0].get("missing"):
            continue
        out = []
        for x in (pages[0].get("images") or []):
            t = (x.get("title") or "").replace("File:", "")
            low = t.lower()
            if not t or C.is_bad_image(t) or any(k in low for k in ICON_WORDS):
                continue
            out.append(t)
        if out:
            return out[:limit], lang
    return [], None


def en_wiki_lead(entry):
    """英文维基条目首图 —— 中文维基没有对应条目时的最后一路。

    小国的宗堡、环礁、村落基本只有英文条目，中文维基一片空白，
    原来这些国家只能掉到 Commons 全文搜索，然后拿回地图、书页扫描件。"""
    title = en_title(entry)
    if not title:
        return None, "英文维基无合适条目"
    img, t = wikipedia_lead_image(title, "en")
    if img:
        return img, t
    return None, "英文维基条目没有首图"


def query_variants(entry):
    """一个地标给几条检索词。第一条原样，后面逐步砍掉修饰词。
    Commons 的 File 检索是全文匹配，多写一个词就少一半结果 ——
    "Great Pyramids of Giza" 命中数远少于 "Pyramids Giza"。"""
    out = []
    for key in ("kw", "en"):
        v = (entry.get(key) or "").strip()
        if v and v not in out:
            out.append(v)
    base = (entry.get("kw") or entry.get("en") or "").strip()
    words = [w for w in base.split() if w.lower() not in ("the", "of", "great", "national")]
    if len(words) >= 2:
        v = " ".join(words[-2:])
        if v and v not in out:
            out.append(v)
    return out[:3]


def gather_candidates(entry):
    """按可信度排队收集候选，几路都试（不是命中即停）。
    以前是 if/elif：拿到 P18 就不搜 Commons 了，结果 P18 那张被宽高比筛掉时，
    整处地标就没了 —— 埃及金字塔就是这么丢的。

    返回 (候选, 失败原因列表)。第二个值专门用来区分"确实没有图"和"接口挂了"——
    以前超时被 try/except 吞成空列表，报告里写成"搜不到"，补图时就不会重试它。"""
    out, seen, errs = [], set(), []

    def add(name, kind):
        if name and name not in seen and not C.is_bad_image(name):
            seen.add(name)
            out.append((name, kind))

    lead, err = wikidata_lead_images(entry.get("zh"))
    for x in lead:
        add(x["file"], "wikidata_p18")
    if lead:
        log("      · %s P18 → %s（%s）" % (entry.get("zh"), lead[0]["file"][:42], lead[0]["label"]))
    else:
        wname, wtitle = wikipedia_lead_image(entry.get("zh"))
        if wname:
            add(wname, "wikipedia_lead")
            log("      · %s 中文维基首图 → %s（%s）" % (entry.get("zh"), wname[:42], wtitle))

    if len(out) < 3:
        arts, alang = article_images(entry)
        for n in arts:
            add(n, "article_img")
        if arts:
            log("      · %s 条目配图（%s）→ %d 张" % (entry.get("zh"), alang, len(arts)))

    if len(out) < 3:
        for q in query_variants(entry):
            try:
                got = search_files(q, limit=12)
            except Exception as ex:
                errs.append("Commons「%s」：%s" % (q, str(ex)[:50]))
                log("      [!] Commons「%s」请求失败，换下一条" % q)
                continue
            for n in got:
                add(n, "commons_search")
            log("      · %s Commons「%s」→ %d 个候选" % (entry.get("zh"), q, len(got)))
            if len(out) >= MAX_CANDIDATES:
                break

    # 前面几路都没凑出候选，才去英文维基碰运气（会多两次请求，不值得每处都跑）
    late = None
    if len(out) < 2:
        img, note = en_wiki_lead(entry)
        if img:
            add(img, "en_wiki")
            late = img
            log("      · %s 英文维基首图 → %s（%s）" % (entry.get("zh"), img[:42], note))
        elif note:
            errs.append("英文维基：%s" % note)
    return out, errs


def is_junk(name):
    low = (name or "").replace("_", " ").lower()
    return any(w in low for w in JUNK_WORDS)


def fetch_cats(names):
    """批量取 Commons 分类。imageinfo 的 iiprop 不返回分类，得单独 prop=categories。"""
    out = {}
    for i in range(0, len(names), 40):
        try:
            d = C.api_json(COMMONS_API, {
                "action": "query", "format": "json", "prop": "categories",
                "cllimit": "60",
                "titles": "|".join("File:" + n for n in names[i:i + 40]),
            })
        except Exception:
            continue
        for p in ((d.get("query") or {}).get("pages") or {}).values():
            key = C.norm_title(p.get("title") or "")
            out[key] = [c["title"].replace("Category:", "")
                        for c in (p.get("categories") or [])]
    return out


def bad_kind(name, cats, meta=None):
    """这张图是"卫星影像/地图/示意图/人物照"吗？返回原因或 None。"""
    low = (name or "").replace("_", " ")
    hard = HARD_BAD_RE.search(low)
    if hard:
        return "文件名含禁用词「%s」" % hard.group(0)
    if EVENT_RE.search(low):
        return "文件名像活动照/会议照"
    if BAD_NAME_RE.search(low):
        return "文件名像卫星/科研数据"
    if BAD_PREFIX_RE.search(low):
        return "文件名是定位图/地图"
    for c in cats or ():
        if BAD_CAT_RE.search(c):
            return "分类是「%s」" % c[:40]
        if EVENT_RE.search(c):
            return "分类是活动照「%s」" % c[:40]
    # 原图不到 25 KB，1400px 宽的照片不可能这么小 —— 基本是纯色块、定位图、示意图
    if meta and 0 < (meta.get("bytes") or 0) < 25000:
        return "原图仅 %d KB，疑似示意图" % ((meta.get("bytes") or 0) // 1024)
    if meta and FACILITY_RE.search(meta.get("description") or ""):
        return "说明是建筑设施（%s）" % (meta.get("description") or "")[:26]
    return None


def must_ok(name, must):
    """文件名里有没有这处地标的专名。忽略空格和大小写，
    因为 Commons 文件名习惯写 "Rinpung_Dzong_Paro_2007" 这种下划线形式。"""
    if not must:
        return True
    a = (name or "").lower().replace("_", " ").replace(" ", "")
    b = must.lower().replace("_", " ").replace(" ", "")
    return b in a


def series_bonus(name):
    """同一组套图的序号偏好。

    Commons 上常见 "Punakha Dzong, Bhutan 01.jpg ... 26.jpg" 这种一次传几十张的系列：
    01 通常是正面全景，越往后越可能是细部特写。实测第 26 张是屋檐一角的金饰 ——
    尺寸、比例、专名全合规，但画面上根本没有宗堡。
    尺寸筛不掉这个，只能靠序号：小号加分，大号减分。"""
    m = re.search(r"(?<!\d)(\d{1,3})(?!\d)\s*(?:\.\w+)?$", name or "")
    if not m:
        return 0.0
    n = int(m.group(1))
    if n <= 3:
        return 0.9
    if n <= 8:
        return 0.3
    if n >= 20:
        return -0.7
    return 0.0


def rank_pool(pool, info, cats, must, tier, avoid=None):
    """按档位给一组候选打分，返回 (通过的, 被淘汰的原因)。"""
    scored, rejects = [], []
    for name, kind in pool:
        meta = C.lookup_meta(info, name)
        if not meta or not meta.get("remote_url"):
            continue
        if (meta.get("mime") or "").startswith("image/svg"):
            continue
        ext = C.ext_of(meta["remote_url"])
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        # 排除词：同一个名字在别的国家也有（里约的基督像、基辅的圣索菲亚），
        # 只靠 must 拦不住 —— "cristo" 照样命中里约那张。这里按文件名直接排除。
        low = name.replace("_", " ").lower()
        hit = [a for a in (avoid or []) if a and a.lower() in low]
        if hit:
            rejects.append("%s（命中排除词「%s」）" % (name[:38], hit[0]))
            continue
        # 卫星图/地图/示意图一律不要，不管它多"合规"
        bad = bad_kind(name, cats.get(C.norm_title(name)), meta)
        if bad:
            rejects.append("%s（%s）" % (name[:38], bad))
            continue
        # P18 / 首图是编辑挑过的，不再用 must 卡它；must 只约束关键词搜索的结果
        sc, why = score(name, meta, None if kind in CURATED else must, tier)
        if sc is None:
            rejects.append("%s（%s）" % (name[:38], why))
            continue
        total = sc + SOURCE_BONUS.get(kind, 0) + series_bonus(name)
        # 编辑图也可能配错条目：Wikidata"帕罗宗"条目的 P18 是 Drugyel Dzong（另一座宗堡）。
        # 文件名里连这处地标的专名都没有，就没有证据说明它画的是这处地方 ——
        # 让它退到和关键词命中同一起跑线，让真正命中专名的实搜图赢。
        if kind in CURATED and must and not must_ok(name, must):
            total -= 3.0
        scored.append((total, name, meta, ext, kind))
    scored.sort(key=lambda x: -x[0])
    return scored, rejects



def ranked_picks(entry):
    """把这个地标所有能用的候选按优先级排好序返回（不只是第一名）。

    为什么需要整个列表：视觉复核阶段要"这张不行，换下一张再验"，
    只给第一名就没法往下走了。排序键是 (尝试序, -分数) —— 尝试序就是
    ATTEMPTS 里池子/档位的先后，所以第一项永远等于原来 pick_for 选的那张。"""
    cands, errs = gather_candidates(entry)
    if not cands:
        return [], errs

    cands = cands[:MAX_CANDIDATES]
    info = C.commons_imageinfo([n for n, _ in cands], width=1400) or {}
    cats = fetch_cats([n for n, _ in cands])
    must = (entry.get("must") or "").strip() or None
    curated = [c for c in cands if c[1] in CURATED]
    late = [c for c in cands if c[1] == "en_wiki"]
    article = [c for c in cands if c[1] == "article_img"]
    rest = [c for c in cands if c[1] not in CURATED and c[1] not in ("en_wiki", "article_img")]
    searched = [c for c in rest if not is_junk(c[0])]
    if len(searched) < len(rest):
        log("      · %s 关键词命中里滤掉 %d 张（雕塑/展陈/建筑设施）"
            % (entry.get("zh"), len(rest) - len(searched)))

    pools = {"curated": curated, "article": article,
             "commons": searched, "late": late}
    tiers = dict(TIERS)
    out = []
    for idx, (pool_name, tier_name) in enumerate(ATTEMPTS):
        pool = pools[pool_name]
        if not pool:
            continue
        scored, _rej = rank_pool(pool, info, cats, must, tiers[tier_name],
                                 entry.get("avoid"))
        for total, name, meta, ext, kind in scored:
            out.append({"order": idx, "score": total, "name": name, "meta": meta,
                        "ext": ext, "kind": kind, "tier": tier_name})
    out.sort(key=lambda x: (x["order"], -x["score"]))
    # 同一张图会在多个档位都通过（strict 和 relaxed 各算一次），去重只留最先出现的
    dedup, seen = [], set()
    for p in out:
        if p["name"] in seen:
            continue
        seen.add(p["name"])
        dedup.append(p)
    return dedup, errs


def pick_for(entry):
    """在一个地标的候选里挑一张。

    返回 ((name, meta, ext, tier, kind), None, False)
      或 (None, 原因, 是否值得重试)。第三个值用来把"接口挂了"和"确实没有图"
      分开：前者进 --gaps 重试，后者重试一百次也没用。"""
    picks, errs = ranked_picks(entry)
    if not picks:
        if errs:
            return None, "检索接口全部失败（%s）" % "；".join(errs[:2]), True
        return None, "Wikidata / 维基 / Commons 都没有可用图", False

    must = (entry.get("must") or "").strip() or None
    fallback = None
    for p in picks:
        # 编辑图连专名都对不上，先记下当兜底，继续让实搜图去争 ——
        if p["kind"] in CURATED and must and not must_ok(p["name"], must):
            if fallback is None:
                fallback = p
            continue
        if p["order"] > 0:
            log("      ! %s 走 %s/%s 档"
                % (entry.get("zh"), p["kind"], p["tier"]))
        return (p["name"], p["meta"], p["ext"], p["tier"], p["kind"]), None, False

    if fallback:
        log("      ! %s 只剩一张与专名不符的编辑图：%s"
            % (entry.get("zh"), fallback["name"][:40]))
        return ((fallback["name"], fallback["meta"], fallback["ext"],
                 fallback["tier"], fallback["kind"]),
                "只有与专名不符的编辑图（%s）" % fallback["name"][:30], False)

    return None, "候选与专名都不符，已跳过", False


def pick_and_download(iso, entries, rec, cap=MAX_PER_COUNTRY):
    slug = iso.lower()
    adir = os.path.join(ROOT, "assets", slug)
    os.makedirs(adir, exist_ok=True)
    picked, notes, gaps = [], [], []
    used = set()
    low_used = 0

    # 已经抓好并落盘的图直接沿用。批量跑到一半重跑、或者只想补某一处地标时，
    # 不要把所有图重新下一遍（重下还会换掉已经人工确认过的那几张）。
    #
    # 认领键踩过两次坑：一开始按 kw 匹配，可中法美那批是人工稿，kw 全是 null，
    # 于是"补第五张"把整国四张重下了一遍，凡尔赛镜厅变成了某酒吧的镜子、
    # 白宫变成了拆除施工现场。后来想按 caption_zh 精确匹配，也不行 ——
    # 人工图注带修饰（"秦始皇陵兵马俑一号坑全景" vs 清单里的"秦始皇陵兵马俑"）。
    # 现在是：先精确，再双向包含。
    kept = [it for it in (rec.get("landmark_images") or [])
            if it.get("url") and os.path.exists(os.path.join(ROOT, it["url"]))]
    taken = set()

    def take_kept(zh):
        z = (zh or "").strip()
        if not z:
            return None
        for i, it in enumerate(kept):
            if i not in taken and (it.get("caption_zh") or "").strip() == z:
                taken.add(i)
                return it
        for i, it in enumerate(kept):
            if i in taken:
                continue
            c = (it.get("caption_zh") or "").strip()
            if c and (z in c or c in z):
                taken.add(i)
                return it
        return None

    for i, e in enumerate(entries, 1):
        if len(picked) >= cap:
            break
        kw, zh = e.get("kw"), e.get("zh")

        old = take_kept(zh)
        if old:
            picked.append(old)
            used.add(old.get("title"))
            if old.get("pick_tier") and old.get("pick_tier") != "strict":
                low_used += 1
            log("      [=] %-22s 沿用已有的 %s" % (zh, str(old.get("url")).split("/")[-1]))
            continue

        got, why, retryable = pick_for(e)
        if not got:
            notes.append("%s：%s" % (zh, why))
            gaps.append({"zh": zh, "kw": kw, "reason": why,
                         "retryable": bool(retryable)})
            log("      [x] %s %s" % (zh, why))
            continue

        name, meta, ext, tier_name, kind = got
        if name in used:
            notes.append("%s：与前面某处重复（同图），已跳过" % zh)
            gaps.append({"zh": zh, "kw": kw, "reason": "与其他地标同图",
                         "retryable": False})
            continue

        # 放宽档的图有总量配额：一国的画廊里留两张兜底就够，再多就是在教用户看烂图。
        # 小国尤其明显 —— 凑到第 4 张时通常已经是市政厅或者游客自拍。
        if tier_name != "strict":
            if low_used >= LOW_TIER_CAP:
                why = "只有放宽档的候选，低档配额（%d 张）已满" % LOW_TIER_CAP
                notes.append("%s：%s" % (zh, why))
                gaps.append({"zh": zh, "kw": kw, "reason": why, "retryable": True})
                log("      [x] %s %s" % (zh, why))
                continue
            low_used += 1
        used.add(name)

        rel = "assets/%s/L%02d%s" % (slug, i, ext)
        try:
            size = C.download(meta["remote_url"],
                              os.path.join(adir, "L%02d%s" % (i, ext)))
        except Exception as ex:
            notes.append("%s：下载失败（%s）" % (zh, str(ex)[:60]))
            gaps.append({"zh": zh, "kw": kw, "reason": "下载失败"})
            log("      [x] %s 下载失败" % zh)
            continue

        lic = meta.get("license")
        if not lic:
            notes.append("%s：未取到授权字段，保留并标「授权待确认」" % zh)

        picked.append({
            "title": "File:" + name,
            "caption_zh": zh,
            "caption": zh,                    # 兼容旧字段，值就是中文
            "caption_commons": (meta.get("description") or "")[:80],
            "kw": kw,
            "url": rel,
            "thumb_url": rel,
            "remote_url": meta["remote_url"],
            "source": kind,
            "pick_tier": tier_name,
            "author": meta.get("artist"),
            "license": lic,
            "license_url": meta.get("license_url"),
            "license_status": "ok" if lic else "unknown",
            "load_verified": True,
            "bytes": size,
        })
        log("      [ok] %-4s %-22s %.0f KB  %s" % (
            rel.split("/")[-1], zh, size / 1024, name[:52]))

    return picked, notes, gaps


def country_cap(iso, conf, n_entries, meta):
    """每国留几张图：min(清单条数, 硬顶 5)。conf["caps"] 可逐个覆盖。

    不再按人口折算 —— 见文件头 MAX_PER_COUNTRY 上那段，冰岛 37 万人六处名胜，
    卢森堡 65 万人只有两处，人口和文化资源不相关。"小国少几张"是质量闸门筛出来的
    结果，不是预设的规则。"""
    cap = max(1, min(MAX_PER_COUNTRY, n_entries))
    ov = (conf.get("caps") or {}).get(iso)
    if ov:
        cap = max(1, min(MAX_PER_COUNTRY, int(ov)))
    return cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("isos", nargs="*", help="ISO 码，如 CN FR US")
    ap.add_argument("--all", action="store_true", help="处理 landmarks.json 里全部国家")
    ap.add_argument("--gaps", action="store_true",
                    help="只处理上次留下缺口的国家（读 data/_image_gaps.json）")
    ap.add_argument("--plan", action="store_true",
                    help="只打印每国准备留几张图，不联网不下载")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "data", "landmarks.json"), encoding="utf-8") as f:
        conf = json.load(f)
    meta = read_json(os.path.join(ROOT, "geo", "meta.json"), {})

    isos = [x.upper() for x in args.isos]
    if args.all or args.plan:
        isos = list(conf.get("countries", {}).keys())
    if args.gaps:
        old_gaps = read_json(GAPS, {})
        isos = sorted(k for k, v in old_gaps.items() if v)
    if not isos:
        ap.error("给个 ISO 码，或用 --all / --gaps / --plan")

    if args.plan:
        rows = []
        for iso in isos:
            n = len((conf.get("countries") or {}).get(iso) or [])
            m = meta.get(iso) or {}
            rows.append((iso, m.get("n") or iso,
                         (m.get("pop") or 0), n, country_cap(iso, conf, n, meta)))
        rows.sort(key=lambda r: r[2])
        print("%-4s %-10s %12s %6s %6s" % ("ISO", "国家", "人口", "清单", "留图"))
        for r in rows:
            print("%-4s %-10s %12s %6d %6d" % (r[0], r[1], "{:,}".format(int(r[2] or 0)),
                                              r[3], r[4]))
        print("\n共 %d 国，合计 %d 张。想改某一国：在 data/landmarks.json 加 "
              '"caps": {"FJ": 5}' % (len(rows), sum(r[4] for r in rows)))
        return 0

    gapmap = read_json(GAPS, {})

    for iso in isos:
        entries = (conf.get("countries") or {}).get(iso) or []
        if not entries:
            log("[%s] landmarks.json 里没有配置，跳过" % iso)
            continue

        path = os.path.join(ROOT, "data", "%s.json" % iso.lower())
        if not os.path.exists(path):
            log("[%s] data/%s.json 不存在，先跑 collect.py" % (iso, iso.lower()))
            continue
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)

        cap = country_cap(iso, conf, len(entries), meta)
        log("[%s] %s —— 清单 %d 处，上限 %d 张"
            % (iso, rec.get("name_zh"), len(entries), cap))
        picked, notes, gaps = pick_and_download(iso, entries, rec, cap=cap)
        gapmap[iso] = gaps

        if not picked:
            log("      ! 一张都没拿到，保持原样")
            continue

        old = rec.get("landmark_images") or []
        rec["landmark_images"] = picked
        rec["image_license"] = picked[0].get("license")
        rec["image_source_note"] = ("人工按 data/landmarks.json 指定地标，"
                                    "Commons 关键词检索取图；原先自动获得的 %d 张已被替换" % len(old))

        rn = [n for n in (rec.get("review_notes") or [])
              if "地标" not in n and "P18" not in n]
        rn.extend(notes)
        rn.append("地标图改为人工指定清单（%d 张，中文图注）" % len(picked))
        rec["review_notes"] = rn

        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
            f.write("\n")
        log("      已写回 data/%s.json：%d 张，缺口 %d 处\n" % (iso.lower(), len(picked), len(gaps)))

    write_json(GAPS, gapmap)
    left = sum(len(v) for v in gapmap.values())
    if left:
        log("缺口合计 %d 处（%d 个国家）→ data/_image_gaps.json" % (left, len(gapmap)))
        log("补缺口：python scripts/fill_images.py --gaps （只重试这些国家，已有的图不动）")
    log("下一步：python scripts/optimize_images.py --all  然后 python scripts/sync_index.py")


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


if __name__ == "__main__":
    main()
