# -*- coding: utf-8 -*-
"""给已上传的对象补 Cache-Control（这一步以前一直缺）。

背景
----
`PutObjectHeader` 的 allowedAttr 里**没有** cacheControl：
    hdr = PutObjectHeader(contentType=...)
    hdr.cacheControl = "public, max-age=604800"   # ← 只是给 Python 对象挂了个属性
SDK 不报错、也不生效。结果线上 2220 张图**一个缓存头都没有**，
浏览器只能靠启发式缓存，每次进来都重新回源：单张 0.5~1.0 秒（实测）。

正确写法是 putFile/putContent 的 extensionHeaders：
    extensionHeaders={"Cache-Control": "public, max-age=604800"}
它会作为标准头写进去（不是 x-obs-meta-*）。

用法
----
    python scripts/set_cache_control.py --dry-run        # 只统计
    python scripts/set_cache_control.py --only assets    # 只补图片
    python scripts/set_cache_control.py                 # 图片 + 数据 + 页面
"""

import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upload_obs as U                          # noqa: E402
from envfile import load                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = "atlas-mundi-1419"
REGION = "cn-east-3"

# 图片：内容按文件名不变（换图会换版本号或文件名），可以长缓存
# 数据/页面：必须重新验证，否则改了看不到
CACHE = {
    ".webp": "public, max-age=604800",
    ".jpg": "public, max-age=604800",
    ".png": "public, max-age=604800",
    ".svg": "public, max-age=604800",
    ".mp3": "public, max-age=604800",
    ".woff2": "public, max-age=2592000",
    ".json": "no-cache, must-revalidate",
    ".html": "no-cache, must-revalidate",
    ".js": "no-cache, must-revalidate",
    ".css": "no-cache, must-revalidate",
}


def collect(only):
    out = []
    for rel in ("assets", "data", "geo"):
        if only != "all" and only != rel:
            continue
        if only == "assets" and rel != "assets":
            continue
        for root, _dirs, files in os.walk(os.path.join(ROOT, rel)):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in CACHE:
                    continue
                p = os.path.join(root, f)
                key = os.path.relpath(p, ROOT).replace("\\", "/")
                out.append((p, key))
    if only in ("all", "root"):
        for f in os.listdir(ROOT):
            if f.endswith((".html", ".js", ".css", ".webmanifest")):
                out.append((os.path.join(ROOT, f), f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="all", choices=["all", "assets", "data", "geo", "root"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    load()
    files = collect(args.only)
    if args.limit:
        files = files[:args.limit]
    print("待补缓存头 %d 个对象（only=%s）" % (len(files), args.only))
    if args.dry_run:
        from collections import Counter
        c = Counter(CACHE[os.path.splitext(k)[1].lower()] for _p, k in files)
        for k, v in c.items():
            print("  %-40s %d" % (k, v))
        return 0

    from obs import ObsClient
    cli = ObsClient(access_key_id=os.environ["HUAWEI_AK"],
                    secret_access_key=os.environ["HUAWEI_SK"],
                    server="https://obs.%s.myhuaweicloud.com" % REGION)
    ok = fail = 0
    for i, (p, key) in enumerate(files, 1):
        ext = os.path.splitext(key)[1].lower()
        try:
            with open(p, "rb") as f:
                data = f.read()
            r = cli.putContent(
                BUCKET, key, data,
                extensionHeaders={"Cache-Control": CACHE[ext],
                                  "Content-Type": U.ctype_of(key).split(";")[0]})
            if r.status >= 300:
                fail += 1
                print("  [x] %s %s" % (key, r.status))
            else:
                ok += 1
        except Exception as e:
            fail += 1
            print("  [x] %s %s" % (key, str(e)[:60]))
        if i % 100 == 0:
            print("  … %d/%d（成功 %d）" % (i, len(files), ok), flush=True)
    cli.close()
    print("完成：成功 %d，失败 %d" % (ok, fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
