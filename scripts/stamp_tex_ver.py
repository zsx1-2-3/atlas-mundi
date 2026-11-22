# -*- coding: utf-8 -*-
"""把 assets/tex 里每张贴图的**内容 md5** 写进 index.html 的 TEX_VER 表。

为什么要有这一步
----------------
贴图 URL 原来挂的是全局构建号（?v=2026-11-11-AW）。构建号每次发版都变，
哪怕只改了一行 CSS，浏览器的缓存也全部作废 —— 回访用户要把两张 8K 贴图
（合计约 2MB）整套重下一遍。华为云的流量告警就是这么被刷出来的。

改成内容哈希之后：图片没重新生成 → 哈希不变 → URL 不变 → 直接命中缓存；
只有真的重新生成过的那张才会换 URL。

用法（重新生成任何 assets/tex 下的图之后都要跑一次）
----------------------------------------------------
    python scripts/stamp_tex_ver.py           # 写入
    python scripts/stamp_tex_ver.py --check   # 只报告是否一致（CI / 发版前）
"""
import argparse
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
TEXDIR = os.path.join(ROOT, "assets", "tex")
START = "    /* TEX_VER:start */"
END = "    /* TEX_VER:end */"


def collect():
    """只收 index.html 里真正引用到的贴图（tex / texLo / koppenTex 字段）。"""
    html = open(HTML, encoding="utf-8").read()
    # koppenTex 也要收：它是气候图层的第二张图（柯本 30 类），同样走 OBS 缓存，
    # 漏掉它的话只有这张图会退回用构建号，每次发版都重新下一遍。
    names = sorted(set(re.findall(r"(?:koppenTex|tex(?:Lo)?):\s*'([^']+)'", html)))
    # 本地没有的文件（planet 图、terrain 这些只在 OBS / 别处）不能直接丢掉：
    # 丢掉等于把线上那条哈希换成构建号，回访用户又要重下一遍。
    # 保留表里已有的旧值，只对本地存在的文件重算。
    old = {}
    i, j = html.find(START), html.find(END)
    if i >= 0 and j > i:
        for m in re.finditer(r"'([^']+)':\s*'([0-9a-f]{8})'", html[i:j]):
            old[m.group(1)] = m.group(2)
    out = []
    for n in names:
        p = os.path.join(TEXDIR, n)
        if not os.path.isfile(p):
            if n in old:
                out.append((n, old[n], -1))
            else:
                print("  缺少文件且无旧值:", n)
            continue
        h = hashlib.md5(open(p, "rb").read()).hexdigest()[:8]
        out.append((n, h, os.path.getsize(p)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    a = ap.parse_args()

    items = collect()
    if not items:
        print("没找到任何贴图引用")
        return 1

    html = open(HTML, encoding="utf-8").read()
    i, j = html.find(START), html.find(END)
    if i < 0 or j < 0:
        print("index.html 里找不到 TEX_VER 标记")
        return 1
    # 以旧表为底再覆盖，不整表重建：有些贴图（temp.webp、行星图）不是走 tex 字段
    # 加载的，正则收不到，整表重建会把它们的哈希一起抹掉。
    table = {}
    for m in re.finditer(r"'([^']+)':\s*'([0-9a-f]{8})'", html[i:j]):
        table[m.group(1)] = m.group(2)
    for n, h, _ in items:
        table[n] = h
    body = "".join("    '%s': '%s',\n" % (n, table[n]) for n in sorted(table))
    new = html[:i + len(START)] + "\n" + body + html[j:]

    changed = [n for n, h, _ in items if ("'%s': '%s'" % (n, h)) not in html[i:j]]
    for n, h, sz in items:
        print("  %-18s %s  %s %s" % (n, h,
              ("%7.1f KB" % (sz / 1024)) if sz >= 0 else "  本地无  ",
              "（新）" if n in changed else ""))
    if new == html:
        print("版本表已是最新")
        return 0
    if a.check:
        print("版本表过期，需要跑一次 stamp_tex_ver.py")
        return 2
    open(HTML, "w", encoding="utf-8", newline="").write(new)
    print("已更新 index.html 的 TEX_VER（%d 张）" % len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
