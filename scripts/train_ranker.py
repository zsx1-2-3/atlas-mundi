# -*- coding: utf-8 -*-
"""训练一个排序/判别器：把"人工拍脑袋的权重"换成"从数据拟合的权重"。

为什么之前没训练：没有 baseline、没有评估集，而且规则 + 视觉判定已经 98.8%。
但只要把已有的标注和特征整理出来，训练本身只要几十行 —— 这个脚本就是那几十行。

特征（都能在采集时拿到，不需要额外下载图片）：
  · 来源池 one-hot（P18 / 维基正文 / 检索 / 人工）
  · 文件名是否命中 must / 多语言别名
  · 宽高比偏离 1.5 的程度、竖图、文件体积
  · 有没有授权信息
  · 结构化约束结论（P180 / GPS：ok / conflict / unknown）
  · 题注长度

标签：data/_vision_cache.json 里的视觉判定（ok / 不是）。
      这是"弱标签"，但比没有强；人工否决的 25 条可以在注释里看到权重差异。

输出：AUC、PR-AUC、系数表（谁在决定对错）、以及 5 折交叉验证的稳定性。
用法：python scripts/train_ranker.py
"""
import io
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO_VERDICT = os.path.join(ROOT, "review", "constraints.json")


def load_rows():
    cache = json.load(io.open(os.path.join(ROOT, "data", "_vision_cache.json"), encoding="utf-8"))
    geo = {}
    if os.path.exists(GEO_VERDICT):
        try:
            d = json.load(io.open(GEO_VERDICT, encoding="utf-8"))
            for c in d.get("conflicts") or []:
                geo[(c["iso"], c["title"])] = c.get("level") or "conflict"
        except Exception:
            pass
    conf = json.load(io.open(os.path.join(ROOT, "data", "landmarks.json"), encoding="utf-8"))
    must_of = {}
    for iso, ents in (conf.get("countries") or {}).items():
        for e in ents:
            if e.get("zh"):
                must_of[(iso, e["zh"])] = (e.get("must") or "").lower()

    rows = []
    for fn in sorted(os.listdir(os.path.join(ROOT, "data"))):
        if len(fn) != 7 or not fn.endswith(".json"):
            continue
        iso = fn[:2].upper()
        rec = json.load(io.open(os.path.join(ROOT, "data", fn), encoding="utf-8"))
        for it in rec.get("landmark_images") or []:
            t = it.get("title") or ""
            v = cache.get(t) or {}
            if "ok" not in v:
                continue                      # 没判过的不进训练集
            name = t.replace("File:", "").lower()
            must = must_of.get((iso, it.get("caption_zh") or ""), "")
            sz = (it.get("size") or {}).get("detail") or ""
            m = re.match(r"^(\d+)x(\d+)$", sz)
            w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            ar = (w / h) if h else 1.5
            src = it.get("source") or ""
            g = geo.get((iso, t), "")
            rows.append({
                "label": 1 if v.get("ok") else 0,
                "src_p18": 1.0 if src.startswith("wikidata_p18") else 0.0,
                "src_lead": 1.0 if src == "wikipedia_lead" else 0.0,
                "src_article": 1.0 if src == "article_img" else 0.0,
                "src_search": 1.0 if src == "commons_search" else 0.0,
                "src_manual": 1.0 if ("manual" in src or "handpick" in src) else 0.0,
                "must_hit": 1.0 if (must and must in name) else 0.0,
                "aspect_dev": abs(ar - 1.5),
                "portrait": 1.0 if (h and h > w * 1.15) else 0.0,
                "log_bytes": float(np.log1p(it.get("bytes") or 0)),
                "has_license": 1.0 if (it.get("license") or "").strip() else 0.0,
                "geo_ok": 1.0 if g == "ok" else 0.0,
                "geo_conflict": 1.0 if g in ("conflict", "soft") else 0.0,
                "cap_len": float(len(it.get("caption_zh") or "")),
            })
    return rows


FEATS = ["src_p18", "src_lead", "src_article", "src_search", "src_manual",
         "must_hit", "aspect_dev", "portrait", "log_bytes", "has_license",
         "geo_ok", "geo_conflict", "cap_len"]


def main():
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score, average_precision_score
    from sklearn.preprocessing import StandardScaler

    rows = load_rows()
    X = np.array([[r[f] for f in FEATS] for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows], dtype=int)
    print("样本 %d，其中判错的 %d 张（%.1f%%）" % (len(y), (y == 0).sum(), 100.0 * (y == 0).mean()))
    if y.sum() < 10 or (y == 0).sum() < 5:
        print("正负样本太少，先别训")
        return

    # 5 折交叉验证，看稳定性
    aucs, aps = [], []
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=7)
    for tr, te in skf.split(X, y):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(class_weight="balanced", max_iter=2000, C=1.0)
        clf.fit(sc.transform(X[tr]), y[tr])
        p = clf.predict_proba(sc.transform(X[te]))[:, 1]
        aucs.append(roc_auc_score(y[te], p))
        aps.append(average_precision_score(y[te], p))
    print("\n5 折交叉验证：AUC %.3f ± %.3f    PR-AUC %.3f ± %.3f"
          % (np.mean(aucs), np.std(aucs), np.mean(aps), np.std(aps)))

    # 全量拟合，看系数（标准化后可比大小）
    sc = StandardScaler().fit(X)
    clf = LogisticRegression(class_weight="balanced", max_iter=2000)
    clf.fit(sc.transform(X), y)
    coef = sorted(zip(FEATS, clf.coef_[0]), key=lambda kv: -abs(kv[1]))
    print("\n系数（标准化后，正=更像对的，负=更像错的）：")
    for k, w in coef:
        bar = "█" * int(abs(w) * 6)
        print("   %-14s %+6.2f  %s" % (k, w, bar))

    # 只看结构化信号与文件名规则的对比
    print("\n单特征判别力（各自单独用它：AUC）：")
    for f in ("must_hit", "geo_ok", "src_p18", "aspect_dev"):
        try:
            a = roc_auc_score(y, X[:, FEATS.index(f)])
            print("   %-12s AUC %.3f" % (f, max(a, 1 - a)))
        except Exception:
            pass


if __name__ == "__main__":
    main()
