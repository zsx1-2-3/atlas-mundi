# -*- coding: utf-8 -*-
"""在候选级标注集上训练一个判别器，并和"单特征基线"逐项对比。

为什么要对比：只报一个 AUC 说明不了什么，要说清"比不比原来那些土办法强"。
基线就是我们真实用过的规则：
  · must_hit     —— 文件名必须含地标专名（最早的做法）
  · 像素/长宽比   —— 只看尺寸和构图的打分器（第二版）
  · clip         —— 零成本的多模态相似度（本轮新增）
对照：
  · LR(全部特征) —— 训练出来的融合器

标签：视觉判定的结果（代理真值，约 85~90% 准确）。
用法：python scripts/train_candidate_ranker.py
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
DS = os.path.join(ROOT, "review", "ds", "with_clip.json")


def build():
    rows = json.load(io.open(DS, encoding="utf-8"))
    conf = json.load(io.open(os.path.join(ROOT, "data", "landmarks.json"), encoding="utf-8"))
    must_of, kw_of = {}, {}
    for iso, ents in (conf.get("countries") or {}).items():
        for e in ents:
            if e.get("zh"):
                must_of[(iso, e["zh"])] = (e.get("must") or "").lower()
                kw_of[(iso, e["zh"])] = e.get("kw") or ""

    out = []
    for r in rows:
        name = (r["name"] or "").lower()
        must = must_of.get((r["iso"], r["zh"]), "")
        w, h = r.get("w") or 0, r.get("h") or 0
        ar = (w / h) if h else 1.5
        try:
            fs = os.path.getsize(os.path.join(ROOT, r["file"]))
        except OSError:
            fs = 0
        out.append({
            "y": r["label"],
            "clip": r.get("clip") or 0.0,
            "must_hit": 1.0 if (must and must in name) else 0.0,
            "aspect_dev": abs(ar - 1.5),
            "portrait": 1.0 if (h and h > w * 1.15) else 0.0,
            "log_w": float(np.log1p(w)),
            "log_bytes": float(np.log1p(fs)),
            "name_len": float(len(r["name"] or "")),
            "kw_len": float(len(kw_of.get((r["iso"], r["zh"]), ""))),
            "tier_strict": 1.0 if (r.get("tier") == "strict") else 0.0,
            "tier_loose": 1.0 if (r.get("tier") in ("relaxed", "loose")) else 0.0,
        })
    return out


FEATS = ["clip", "must_hit", "aspect_dev", "portrait", "log_w", "log_bytes",
         "name_len", "kw_len", "tier_strict", "tier_loose"]


def main():
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score, average_precision_score
    from sklearn.preprocessing import StandardScaler

    rows = build()
    X = np.array([[r[f] for f in FEATS] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=int)
    print("样本 %d，错图 %d（%.1f%%）" % (len(y), (y == 0).sum(), 100.0 * (y == 0).mean()))

    print("\n单特征基线（统一成 分高=更像错 再算）：")
    # 极性：这些特征"越大越好"，取负号才是"错"的方向
    NEG = {"must_hit", "clip", "log_w"}
    base = {}
    for f in ("must_hit", "aspect_dev", "log_w", "clip"):
        v = X[:, FEATS.index(f)]
        s_ = -v if f in NEG else v
        a = roc_auc_score(1 - y, s_)
        p = average_precision_score(1 - y, s_)
        base[f] = a
        print("   %-12s AUC %.3f   PR-AUC %.3f" % (f, a, p))

    # 5 折
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=13)
    aucs, aps = [], []
    for tr, te in skf.split(X, y):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(class_weight="balanced", max_iter=3000)
        clf.fit(sc.transform(X[tr]), y[tr])
        p = clf.predict_proba(sc.transform(X[te]))[:, 0]     # 错类概率
        aucs.append(roc_auc_score(1 - y[te], p))
        aps.append(average_precision_score(1 - y[te], p))
    print("\nLR 融合（5 折）：AUC %.3f ± %.3f   PR-AUC %.3f ± %.3f"
          % (np.mean(aucs), np.std(aucs), np.mean(aps), np.std(aps)))
    print("相对最强单特征（%s，AUC %.3f）提升 %+.3f"
          % (max(base, key=base.get), max(base.values()), np.mean(aucs) - max(base.values())))

    sc = StandardScaler().fit(X)
    clf = LogisticRegression(class_weight="balanced", max_iter=3000).fit(sc.transform(X), y)
    print("\n系数（标准化后；负=更像错，因为标签 1 是对）：")
    for k, w in sorted(zip(FEATS, clf.coef_[0]), key=lambda kv: -abs(kv[1])):
        print("   %-12s %+6.2f  %s" % (k, w, "█" * int(abs(w) * 8)))

    # 混池实验：只用 clip 做门槛，看能不能替代视觉调用
    print("\n" + "=" * 52)
    print("成本对照（每 1000 张候选）：")
    print("   视觉判定全跑：1000 次 × 0.0008 元 ≈ 0.80 元，约 6~10 分钟")
    print("   CLIP 全跑：    0 元（本地 CPU，约 0.1 秒/张）")
    print("   LR 融合：      0 元（CLIP + 元数据，跑完 CLIP 才有意义）")


if __name__ == "__main__":
    main()
