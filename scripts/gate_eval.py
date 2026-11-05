# -*- coding: utf-8 -*-
"""实验：P180 / GPS 能不能当**采集时**的硬闸？

现在 audit_constraints.py 只做事后审计（图已经入库了才发现不对）。
采集时用它的前提是：对**候选**判得准，而且不能误杀好图。

所以这个脚本在候选级上量两件事：
  ① 闸的精确率：被它判"冲突"的候选里，有多少张视觉模型也认为不对
  ② 闸的漏网率：它放过去的候选里，还有多少张是错的（这部分只能靠视觉/人）

做法：
  抽 N 个地标 → 走真实候选池 ranked_picks → 取前 K 个候选
  → 批量取 P180/GPS 判闸 → 对"被拒的"和"抽样的通过的"跑视觉判定
输出直接是可写进文档的表格。
"""
import argparse
import base64
import io
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from envfile import load           # noqa: E402
load()
import fill_images as F            # noqa: E402
import verify_images as V          # noqa: E402
import audit_constraints as A      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pageid_gps(titles):
    """标题 → (M-id, GPS)。一次拿完。"""
    out = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        d = F.C.api_json("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "format": "json", "prop": "imageinfo",
            "iiprop": "extmetadata|coordinates",
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
                out[t] = ("M" + str(pid) if str(pid).isdigit() else None, gps)
        time.sleep(0.1)
    return out


def depicts(mids):
    out = {}
    mids = [m for m in mids if m]
    for i in range(0, len(mids), 40):
        d = F.C.api_json("https://commons.wikimedia.org/w/api.php", {
            "action": "wbgetentities", "ids": "|".join(mids[i:i + 40]),
            "props": "claims", "format": "json"})
        for mid, e in (d.get("entities") or {}).items():
            st = e.get("statements") or e.get("claims") or {}
            out[mid] = [c["mainsnak"]["datavalue"]["value"]["id"]
                        for c in (st.get("P180") or [])
                        if c.get("mainsnak", {}).get("datavalue")]
        time.sleep(0.1)
    return out


def gate(iso, title, pg, dep, q2iso):
    """返回 (verdict, why)。verdict ∈ ok / conflict / unknown。"""
    mid, gps = pg.get(title) or (None, None)
    ds = set()
    for q in dep.get(mid or "", []) or []:
        ds |= q2iso.get(q, set())
    if ds:
        if iso in ds:
            return "ok", "P180 本国"
        gap_ok = A.bbox_gap(iso, *((gps[1], gps[0]) if gps else (999, 999)))
        if gps and gap_ok <= 0.3:
            return "soft", "P180 指向 %s 但贴边界" % ",".join(sorted(ds)[:2])
        return "conflict", "P180 指向 %s" % ",".join(sorted(ds)[:3])
    if gps:
        r = A.point_in(iso, gps[1], gps[0])
        if r:
            return "ok", "GPS 在境内"
        others = [x for x in A.which_countries(gps[1], gps[0]) if x != iso]
        if others:
            if A.bbox_gap(iso, gps[1], gps[0]) <= 0.3:
                return "soft", "GPS 贴边界（%s）" % ",".join(others[:2])
            return "conflict", "GPS 落在 %s" % ",".join(others[:3])
        return "unknown", "GPS 不在陆域"
    return "unknown", "无信号"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", type=int, default=30)
    ap.add_argument("--per", type=int, default=10)
    ap.add_argument("--survivors", type=int, default=3, help="每个地标抽几个'通过'的去判定")
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args()

    conf = json.load(io.open(os.path.join(ROOT, "data", "landmarks.json"), encoding="utf-8"))
    pool = []
    for iso, ents in (conf.get("countries") or {}).items():
        for e in ents:
            if e.get("kw"):
                pool.append((iso, e))
    random.seed(args.seed)
    sample = random.sample(pool, min(args.entries, len(pool)))
    print("抽 %d 个地标，每个最多 %d 个候选\n" % (len(sample), args.per))

    # 1) 走真实候选池
    cands = []
    for iso, e in sample:
        try:
            picks, _err = F.ranked_picks(e)
        except Exception as ex:
            print("  x", iso, e.get("zh"), str(ex)[:40])
            continue
        for c in picks[:args.per]:
            cands.append({"iso": iso, "zh": e.get("zh") or "", "name": c["name"],
                          "meta": c["meta"], "tier": c.get("tier")})
        print("  %-3s %-18s 候选 %d" % (iso, (e.get("zh") or "")[:18], len(picks[:args.per])))

    titles = [c["name"] for c in cands]
    print("\n取元数据 %d 个候选…" % len(titles))
    pg = pageid_gps(titles)
    dep = depicts([m for m, _g in pg.values() if m])

    # P180 → 国家
    qids = []
    for t in titles:
        qids.extend(dep.get((pg.get(t) or (None, None))[0] or "", []) or [])
    c17 = A.fetch_claims(qids, props=("P17",))
    iso_cache = {}
    if os.path.exists(A.ISO2_QID_CACHE):
        try:
            iso_cache = json.load(io.open(A.ISO2_QID_CACHE, encoding="utf-8"))
        except Exception:
            pass
    q2iso = {}
    for q, row in c17.items():
        for p17 in row.get("P17") or []:
            s = A.iso2_of(p17, iso_cache)
            if s:
                q2iso.setdefault(q, set()).add(s)

    # 2) 判闸
    for c in cands:
        c["verdict"], c["why"] = gate(c["iso"], c["name"], pg, dep, q2iso)
    from collections import Counter
    print("\n闸的判定分布：", dict(Counter(c["verdict"] for c in cands)))

    # 3) 挑去视觉判定的：全部 conflict/soft + 每个地标抽样 survivors
    judge_set = [c for c in cands if c["verdict"] in ("conflict", "soft")]
    by_iso = {}
    for c in cands:
        if c["verdict"] in ("ok", "unknown"):
            by_iso.setdefault(c["iso"], []).append(c)
    for iso, arr in by_iso.items():
        judge_set.extend(random.sample(arr, min(args.survivors, len(arr))))
    print("送视觉判定 %d 张（冲突 %d + 抽样通过 %d）"
          % (len(judge_set), sum(1 for c in cands if c["verdict"] in ("conflict", "soft")),
             len(judge_set) - sum(1 for c in cands if c["verdict"] in ("conflict", "soft"))))

    # 4) 视觉判定（按国家批量）
    groups = {}
    for c in judge_set:
        groups.setdefault((c["iso"], c["zh"]), []).append(c)
    for (iso, zh), arr in groups.items():
        pairs, keep = [], []
        for c in arr:
            uri = (c["meta"] or {}).get("remote_url")
            if not uri:
                continue
            dst = os.path.join(ROOT, "review", "_gate_tmp.jpg")
            try:
                F.C.download(uri, dst)
                pairs.append((zh, V.to_jpeg(dst)))
                keep.append(c)
            except Exception:
                pass
        if not pairs:
            continue
        try:
            res, _a, _b = V.judge(zh, pairs)
        except Exception as e:
            print("  x judge", iso, str(e)[:40])
            continue
        for c, (ok, why) in zip(keep, res):
            c["vision_ok"] = bool(ok)
            c["vision_why"] = (why or "")[:32]

    # 5) 汇总
    judged = [c for c in cands if "vision_ok" in c]
    killed = [c for c in judged if c["verdict"] in ("conflict", "soft")]
    passed = [c for c in judged if c["verdict"] in ("ok", "unknown")]
    k_bad = sum(1 for c in killed if not c["vision_ok"])
    p_bad = sum(1 for c in passed if not c["vision_ok"])
    print("\n=== 结果 ===")
    print("闸拒掉 %d 张，其中视觉也判错 %d 张 → 精确率 %.0f%%"
          % (len(killed), k_bad, 100.0 * k_bad / max(1, len(killed))))
    print("闸放行 %d 张（抽样），其中视觉判错 %d 张 → 漏网率 %.0f%%"
          % (len(passed), p_bad, 100.0 * p_bad / max(1, len(passed))))
    print("误杀好图：%d 张（闸拒了但视觉认为对）" % (len(killed) - k_bad))
    print("\n被拒但视觉认为对的（可能是误杀，要人看）：")
    for c in killed:
        if c.get("vision_ok"):
            print("   %-3s %-36s %s" % (c["iso"], c["name"][:36], c["why"][:34]))
    print("\n放行但视觉判错的（闸的盲区）：")
    for c in passed:
        if c.get("vision_ok") is False:
            print("   %-3s %-36s %s" % (c["iso"], c["name"][:36], c.get("vision_why", "")[:30]))

    json.dump({"cands": cands}, io.open(os.path.join(ROOT, "review", "gate_eval.json"), "w",
                                        encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
