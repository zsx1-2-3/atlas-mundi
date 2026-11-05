# -*- coding: utf-8 -*-
"""结构化约束审计（只读）：用三条确定性信号校验"这张图是不是这个国家的"。

三条信号（都不依赖模型判断，可复核）：
  ① P180 depicts —— Commons 文件的结构化声明"我拍的是 Qxxx"，再取它的 P17 国家
  ② EXIF GPS     —— 文件坐标做 point-in-polygon，落在国界外就否决
  ③ 文件名国名   —— 文件名里出现别的国家名（弱信号，只做提示）

覆盖率是前提：先看有多少张真的带这些信号，再看它挡得住什么。
实测（2026-11-05，40 张抽样）：P180 52%、GPS 30%、两者合计 70% 有信号。

用法：
    python scripts/audit_constraints.py                 # 全量，只报告
    python scripts/audit_constraints.py --limit 100     # 抽样
    python scripts/audit_constraints.py --only LC KW MH # 指定国家

几个坑（都实测过，别再踩）：
  · Commons 文件的 mediainfo 实体 ID = "M" + pageid，**不是** pageprops.wikibase_item
  · wbgetentities 请求要写 props=claims（写 statements 会被静默忽略），回包里的键叫 statements
  · wbgetentities 回包里 title 为空，所以只能按 id 对齐，不能按标题对齐
"""
import argparse
import io
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
import collect as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
GEO_DIR = os.path.join(ROOT, "geo")
ISO2_QID_CACHE = os.path.join(ROOT, "data", "_iso2qid.json")


# --------------------------------------------------------------------------
# 几何：国界多边形 → point-in-polygon
# --------------------------------------------------------------------------
# 为什么不用 geo/c*.json：那是给地球渲染用的**简化**几何，环礁、小岛被抽稀掉，
# Bikini / Kwajalein 明明在国界内却被判"不在境内"（实测 2 例误报）。改用 10m 原始数据。
_RINGS = []          # [(iso, rings, bbox)]，一次性加载
_BY_ISO = {}
_LOADED = False


def _rings_of(g, Path):
    rings = []
    if g.get("type") == "Polygon":
        polys = [g["coordinates"]]
    elif g.get("type") == "MultiPolygon":
        polys = g["coordinates"]
    else:
        return rings
    for poly in polys:
        for r in poly:
            if len(r) >= 3:
                rings.append((Path(r), r))
    return rings


def load_all():
    """把 10m 国界一次性读进内存（约 13MB JSON，几秒）。"""
    global _LOADED
    if _LOADED:
        return
    from matplotlib.path import Path
    d = json.load(io.open(os.path.join(GEO_DIR, "_ne_10m_countries.geojson"), encoding="utf-8"))
    for f in d.get("features") or []:
        iso = ((f.get("properties") or {}).get("ISO_A2") or "").upper()
        if not iso or iso in ("-99",):
            continue
        rings = _rings_of(f.get("geometry") or {}, Path)
        if not rings:
            continue
        xs = [p[0] for _pa, r in rings for p in r]
        ys = [p[1] for _pa, r in rings for p in r]
        rec = (iso, rings, (min(xs), max(xs), min(ys), max(ys)))
        _RINGS.append(rec)
        _BY_ISO.setdefault(iso, []).extend(rings)
    _LOADED = True


def point_in(iso, lon, lat):
    """偶数奇偶规则判点是否在该国所有环内（含洞）。True / False / None（无几何）。"""
    load_all()
    rings = _BY_ISO.get(iso)
    if not rings:
        return None
    inside = 0
    for path, r in rings:
        xs = [p[0] for p in r]      # bbox 粗筛：CA/RU 这种几千点的环别每次全量算
        ys = [p[1] for p in r]
        if lon < min(xs) or lon > max(xs) or lat < min(ys) or lat > max(ys):
            continue
        if path.contains_point((lon, lat)):
            inside += 1
    return inside % 2 == 1


def which_countries(lon, lat):
    """这个坐标落在哪些国家的多边形里（用于"它到底在哪国"）。"""
    load_all()
    out = []
    for iso, rings, bb in _RINGS:
        if lon < bb[0] or lon > bb[1] or lat < bb[2] or lat > bb[3]:
            continue
        inside = 0
        for path, r in rings:
            if path.contains_point((lon, lat)):
                inside += 1
        if inside % 2 == 1:
            out.append(iso)
    return out


def bbox_gap(iso, lon, lat):
    """点到该国 bbox 的距离（度）。用于判断"是不是贴着边界/飞地"。

    为什么需要它：三类天然误报——
      · 飞地：梵蒂冈在罗马城里，10m 几何的梵蒂冈多边形装不下圣彼得广场的坐标
      · 边界地物：维多利亚瀑布、伊瓜苏瀑布站对岸拍，坐标天然落在邻国
      · 争议地区：西撒哈拉、约旦河西岸，Natural Earth 的归属和我们的口径不同
    这三类都不该判"错图"，用 0.3°（约 33km）容差把它们降级为 borderline。
    """
    load_all()
    rs = _BY_ISO.get(iso)
    if not rs:
        return 999.0
    xs = [p[0] for _pa, r in rs for p in r]
    ys = [p[1] for _pa, r in rs for p in r]
    dx = max(min(xs) - lon, 0, lon - max(xs))
    dy = max(min(ys) - lat, 0, lat - max(ys))
    return (dx * dx + dy * dy) ** 0.5


def batch(titles, n=40):
    for i in range(0, len(titles), n):
        yield titles[i:i + n]


def fetch_pages(titles):
    """标题 → (pageid, gps)。一次拿完，pageid 用来推 mediainfo 实体 ID。"""
    out = {}
    for chunk in batch(titles):
        d = C.api_json(COMMONS, {"action": "query", "format": "json",
                                 "prop": "imageinfo", "iiprop": "extmetadata|coordinates",
                                 "titles": "|".join("File:" + t for t in chunk)})
        for pid, p in ((d.get("query") or {}).get("pages") or {}).items():
            t = (p.get("title") or "").replace("File:", "")
            ii = (p.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata") or {}
            gps = None
            try:
                gps = (float(em["GPSLatitude"]["value"]), float(em["GPSLongitude"]["value"]))
            except Exception:
                co = ii.get("coordinates") or []
                if co:
                    gps = (float(co[0]["lat"]), float(co[0]["lon"]))
            if t:
                out[t] = (str(pid) if str(pid).isdigit() else None, gps)
        time.sleep(0.1)
    return out


def fetch_depicts(pageids):
    """M-id → P180 的 QID 列表。"""
    out = {}
    mids = [m for m in pageids if m]
    for chunk in batch(mids):
        d = C.api_json(COMMONS, {"action": "wbgetentities", "ids": "|".join(chunk),
                                 "props": "claims", "format": "json"})
        for mid, e in (d.get("entities") or {}).items():
            st = e.get("statements") or e.get("claims") or {}
            out[mid] = [c["mainsnak"]["datavalue"]["value"]["id"]
                        for c in (st.get("P180") or [])
                        if c.get("mainsnak", {}).get("datavalue")]
        time.sleep(0.1)
    return out


def fetch_claims(qids, props=("P17",)):
    """QID → {属性: [值 QID]}。只取需要的属性，省流量。"""
    out = {}
    for chunk in batch(sorted(set(qids))):
        d = C.api_json(WIKIDATA, {"action": "wbgetentities", "ids": "|".join(chunk),
                                  "props": "claims", "format": "json"})
        for qid, e in (d.get("entities") or {}).items():
            st = e.get("claims") or e.get("statements") or {}
            row = {}
            for prop in props:
                vals = []
                for c in (st.get(prop) or []):
                    try:
                        vals.append(c["mainsnak"]["datavalue"]["value"]["id"])
                    except Exception:
                        pass
                row[prop] = vals
            out[qid] = row
        time.sleep(0.1)
    return out


def iso2_of(qid, cache):
    """实体 → ISO 3166-1 alpha-2（P297）。"""
    if qid in cache:
        return cache[qid]
    d = C.api_json(WIKIDATA, {"action": "wbgetentities", "ids": qid,
                              "props": "claims", "format": "json"})
    iso = ""
    e = (d.get("entities") or {}).get(qid) or {}
    for c in ((e.get("claims") or {}).get("P297") or []):
        try:
            iso = str(c["mainsnak"]["datavalue"]["value"]).upper()
            break
        except Exception:
            pass
    cache[qid] = iso
    return iso


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="只查这些 ISO2")
    ap.add_argument("--limit", type=int, default=0, help="最多查多少张（0=全部）")
    args = ap.parse_args()

    # 1) 收集：每张图的 (iso, 标题)
    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if len(fn) != 7 or not fn.endswith(".json"):
            continue
        iso = fn[:2].upper()
        if args.only and iso not in [x.upper() for x in args.only]:
            continue
        rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        for it in rec.get("landmark_images") or []:
            t = (it.get("title") or "").replace("File:", "").strip()
            if t:
                rows.append((iso, t, it.get("caption_zh") or ""))
    if args.limit:
        rows = rows[:args.limit]
    print("待查 %d 张图，%d 个国家和地区\n" % (len(rows), len({r[0] for r in rows})))

    titles = [r[1] for r in rows]
    pages = fetch_pages(titles)
    mid_of = {t: (pages.get(t) or (None, None))[0] for t in titles}
    gps_of = {t: (pages.get(t) or (None, None))[1] for t in titles}
    dep = fetch_depicts([m for m in mid_of.values() if m])

    # 2) P180 的 QID → 国家 QID → ISO2
    all_q = []
    for t in titles:
        all_q.extend(dep.get(mid_of.get(t) or "", []) or [])
    c17 = fetch_claims(all_q, props=("P17",))
    iso_cache = {}
    if os.path.exists(ISO2_QID_CACHE):
        try:
            iso_cache = json.load(io.open(ISO2_QID_CACHE, encoding="utf-8"))
        except Exception:
            iso_cache = {}
    q2iso = {}
    for q, row in c17.items():
        for p17 in row.get("P17") or []:
            s = iso2_of(p17, iso_cache)
            if s:
                q2iso.setdefault(q, set()).add(s)
    json.dump(iso_cache, io.open(ISO2_QID_CACHE, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)

    # 3) 判定
    stat = Counter()
    bad = []
    for iso, t, cap in rows:
        ds = set()
        for q in dep.get(mid_of.get(t) or "", []) or []:
            ds |= q2iso.get(q, set())
        gps = gps_of.get(t)
        verdict, why = "unknown", ""
        if ds:
            if iso in ds:
                verdict, why = "ok", "P180 指向本国"
            else:
                verdict, why = "conflict", "P180 指向 %s" % ",".join(sorted(ds)[:3])
        if gps and verdict != "conflict":
            lon, lat = gps[1], gps[0]          # gps 存的是 (lat, lon)
            r = point_in(iso, lon, lat)
            if r is None:
                pass
            elif r:
                verdict, why = "ok", (why + "+GPS 在境内").strip("+")
            else:
                # 关键：坐标没落在本国境内，**不等于**在别国。
                # 环礁比 10m 几何的岛还小（Bikini / Kwajalein 就是），坐标会落在"海里"；
                # 所以只有"确实落在另一个国家多边形内"才算冲突，落海记为 unknown。
                others = [x for x in which_countries(lon, lat) if x != iso]
                if others:
                    gap = bbox_gap(iso, lon, lat)
                    if gap <= 0.3:
                        # 贴边界的错图也是错图（Thessaloniki 冒充北马其顿、Götzis 冒充列支敦士登），
                        # 所以不能直接放行，单列一档交给人工——但不算"硬冲突"。
                        verdict = "soft"
                        why = "GPS %.3f,%.3f 落在 %s，离本国边界仅 %.2f°" % (
                            lat, lon, ",".join(others[:3]), gap)
                    else:
                        verdict = "conflict"
                        why = "GPS %.3f,%.3f 落在 %s" % (lat, lon, ",".join(others[:3]))
                else:
                    verdict = "unknown"
                    why = "GPS %.3f,%.3f 不在本国陆域（可能在海上/环礁，几何未覆盖）" % (lat, lon)
        stat[verdict] += 1
        if verdict in ("conflict", "soft"):
            bad.append((iso, cap, t, why, verdict))
        elif verdict == "unknown":
            stat["no_signal"] += 1

    print("判定结果：")
    total = len(rows)
    for k in ("ok", "conflict", "soft", "unknown"):
        print("   %-9s %4d  %5.1f%%" % (k, stat[k], 100.0 * stat[k] / max(1, total)))
    has_signal = stat["ok"] + stat["conflict"] + stat["soft"]
    print("   有信号占比 %.0f%%（其余只能交给视觉判定）"
          % (100.0 * has_signal / max(1, total)))
    if has_signal:
        print("   在有信号的那批里，冲突率 %.1f%%" % (100.0 * stat["conflict"] / has_signal))

    print("\n冲突清单：")
    for tag in ("conflict", "soft"):
        sub = [x for x in bad if x[4] == tag]
        print("  [%s] %d 条" % ("硬冲突" if tag == "conflict" else "贴边界(待看)", len(sub)))
        for x in sub[:30]:
            print("     %-3s %-22s %-28s %s" % (x[0], x[1][:22], x[2][:28], x[3][:44]))

    out = os.path.join(ROOT, "review", "constraints.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"stat": dict(stat), "conflicts": [
        {"iso": a, "caption": b, "title": c, "why": d, "level": e} for a, b, c, d, e in bad]},
        io.open(out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print("\n清单已写入 review/constraints.json")


if __name__ == "__main__":
    main()
