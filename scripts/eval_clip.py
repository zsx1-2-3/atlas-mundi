# -*- coding: utf-8 -*-
"""用 CLIP 做零成本预筛，量它能替代多少次视觉判定。

问题：视觉判定准（约 85~90%），但每张都要花钱、要等。
     有没有一种"免费"的信号先过一遍，把明显不对的挡掉，只把边缘的送进视觉模型？

CLIP 正好是免费的（本地 CPU 推理）：算"图像 ↔ 地标名"的相似度。
它判的是"画面内容和这串文字像不像"，与坐标/文件名无关，
所以正好补 P180/GPS 抓不到的那类（室内佛像冒充大金塔、蟾蜍冒充海滩）。

评测口径（这是能写进论文的那张表）：
  对每个阈值 t：
    · 拦截率 = 相似度 < t 的比例（这些图省下了视觉调用的钱）
    · 错图召回 = 被拦的图里，视觉判定为错的比例 / 全部错图
    · 误杀率 = 被拦的图里，视觉判定为对的比例 / 全部对图
  → 画一条"省下多少调用 vs 丢掉多少好图"的曲线，找拐点。

用法：
    python scripts/eval_clip.py                 # 默认 review/ds/labels.json
    python scripts/eval_clip.py --model openai/clip-vit-base-patch32
"""
import argparse
import io
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = os.path.join(ROOT, "review", "ds", "labels.json")


def embed_all(rows, model_name, batch=16):
    """图片 + 文本 → L2 归一化向量。文本用英文检索词（CLIP 对英文最敏感）。"""
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    model = CLIPModel.from_pretrained(model_name)
    proc = CLIPProcessor.from_pretrained(model_name)
    model.eval()

    texts = [("a photo of " + (r["kw"] or r["zh"])).strip() for r in rows]
    sims = []
    with torch.no_grad():
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            imgs = []
            for r in chunk:
                p = os.path.join(ROOT, r["file"])
                try:
                    imgs.append(Image.open(p).convert("RGB"))
                except Exception:
                    imgs.append(Image.new("RGB", (224, 224)))
            inp = proc(text=texts[i:i + batch], images=imgs, return_tensors="pt",
                       padding=True, truncation=True)
            out = model(**inp)
            img_e = out.image_embeds / out.image_embeds.norm(dim=-1, keepdim=True)
            txt_e = out.text_embeds / out.text_embeds.norm(dim=-1, keepdim=True)
            sims.extend((img_e * txt_e).sum(dim=-1).tolist())
            if (i // batch) % 5 == 0:
                print("  编码 %d/%d" % (min(i + batch, len(rows)), len(rows)))
    return np.array(sims, dtype=float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=DS)
    ap.add_argument("--model", default="openai/clip-vit-base-patch32")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = json.load(io.open(args.json, encoding="utf-8"))
    rows = [r for r in rows if "label" in r]
    y = np.array([r["label"] for r in rows])
    neg = int((y == 0).sum())
    print("样本 %d 张，其中错图 %d 张（%.1f%%）" % (len(y), neg, 100.0 * neg / len(y)))
    if neg < 5:
        print("错图太少，先建数据集（scripts/build_gate_dataset.py）")
        return

    sims = embed_all(rows, args.model)
    for r, s in zip(rows, sims):
        r["clip"] = float(s)
    json.dump(rows, io.open(args.out or os.path.join(ROOT, "review", "ds", "with_clip.json"),
                            "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)

    from sklearn.metrics import roc_auc_score, average_precision_score
    auc = roc_auc_score(1 - y, -sims)          # 分低 = 更像错
    ap_ = average_precision_score(1 - y, -sims)
    print("\nCLIP 相似度当作错图分数：AUC %.3f   PR-AUC %.3f" % (auc, ap_))

    print("\n阈值扫描（t 以下直接拦掉，不送视觉模型）：")
    print("   %-6s %-10s %-10s %-10s %-10s" % ("阈值", "拦截率", "错图召回", "误杀好图", "净收益"))
    best = None
    pos = int((y == 1).sum())
    for t in np.arange(0.20, 0.36, 0.01):
        m = sims < t
        cut = m.mean()
        caught = (m & (y == 0)).sum() / max(1, neg)
        killed = (m & (y == 1)).sum() / max(1, pos)
        # 净收益：省下的调用 - 误杀的代价（把误杀算 3 倍代价，因为好图比错图更贵）
        gain = cut - 3 * killed
        print("   %-6.2f %-10.1f%% %-10.1f%% %-10.1f%% %+.3f"
              % (t, 100 * cut, 100 * caught, 100 * killed, gain))
        if best is None or gain > best[1]:
            best = (t, gain, cut, caught, killed)
    t, _g, cut, caught, killed = best
    print("\n拐点 t=%.2f：拦掉 %.0f%% 的图（省下 %.0f%% 视觉调用），"
          "抓到 %.0f%% 的错图，误杀 %.0f%% 的好图"
          % (t, 100 * cut, 100 * cut, 100 * caught, 100 * killed))

    # 分池看：CLIP 在哪些来源上更靠谱
    print("\n按候选来源分组的平均相似度（错图应该更低）：")
    tiers = {}
    for r in rows:
        k = r.get("tier") or "?"
        tiers.setdefault(k, []).append((r["clip"], r["label"]))
    for k, arr in sorted(tiers.items(), key=lambda kv: -len(kv[1])):
        good = [s for s, l in arr if l == 1]
        bad = [s for s, l in arr if l == 0]
        print("   %-10s n=%-4d 对均值 %.3f  错均值 %.3f%s"
              % (k, len(arr), np.mean(good) if good else float("nan"),
                 np.mean(bad) if bad else float("nan"),
                 "" if len(bad) >= 3 else "  (错样本少)"))


if __name__ == "__main__":
    main()
