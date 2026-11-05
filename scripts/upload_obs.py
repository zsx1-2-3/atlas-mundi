#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把站点文件上传到华为云 OBS，并设为公共读。

用法：
    export HUAWEI_AK=xxx HUAWEI_SK=xxx
    python scripts/upload_obs.py --bucket atlas-mundi-1419 --region cn-north-4 --dry-run
    python scripts/upload_obs.py --bucket atlas-mundi-1419 --region cn-north-4

设计要点
--------
1. **Content-Type 必须显式设置**。浏览器靠它决定「渲染」还是「下载」：
   少了 `text/html` 会变成下载文件，少了 `image/webp` 图片在部分浏览器里显示不出来。
   OBS SDK 会按扩展名猜，但 .webp / .geojson 这类新格式猜不准，所以这里写死映射。
2. **AK/SK 只从环境变量读**，不落盘、不入库（.gitignore 已覆盖 .env）。
3. 只上传站点运行需要的文件（assets / data / geo / index.html / docs），
   不传 .git、review、缓存包。
"""

from __future__ import annotations

import argparse
import mimetypes
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


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扩展名 -> Content-Type（显式写死，不依赖系统 mimetypes）
CTYPE = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".geojson": "application/geo+json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".ico": "image/x-icon",
}

# 缓存策略：html/json 必须 no-cache，改了才能立刻看到；静态素材长缓存省流量费
CACHE = {
    ".html": "no-cache, must-revalidate",
    ".json": "no-cache, must-revalidate",
    ".md": "no-cache",
    ".txt": "no-cache",
    ".py": "no-cache",
    ".webp": "public, max-age=604800",
    ".jpg": "public, max-age=604800",
    ".jpeg": "public, max-age=604800",
    ".png": "public, max-age=604800",
    ".svg": "public, max-age=604800",
    ".mp3": "public, max-age=604800",
    ".wav": "public, max-age=604800",
    ".ogg": "public, max-age=604800",
    ".geojson": "public, max-age=86400",
    ".css": "public, max-age=604800",
    ".js": "public, max-age=604800",
}

# 要上传的路径（相对仓库根），只挑站点运行必需的
INCLUDE_DIRS = ["assets", "data", "geo", "docs", "scripts", "vendor"]
INCLUDE_FILES = ["index.html", "README.md"]
# 排除：缓存包与预览副本（既大又没用）
# 排除：Natural Earth 原始缓存（本地生成 globe.json 用，站点不引用）
EXCLUDE_SUFFIX = ("_ne_50m_countries.geojson", "_ne_110m_land.geojson")
EXCLUDE_DIRS = {"__pycache__", "_llm"}


def collect(only: str = "") -> list[tuple[str, str]]:
    """收集待上传文件，返回 [(绝对路径, 对象键)]。"""
    out = []
    for name in INCLUDE_FILES:
        p = os.path.join(ROOT, name)
        if os.path.isfile(p):
            out.append((p, name))
    dirs = INCLUDE_DIRS
    if only:
        keep = {x.strip().strip("/") for x in only.split(",") if x.strip()}
        dirs = [d for d in INCLUDE_DIRS if d in keep]
    for d in dirs:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn.endswith(EXCLUDE_SUFFIX) or fn.startswith("."):
                    continue
                # 压图后的源图（674MB）：数据里已经全部指向 .webp，
                # 传上去只是白占空间和带宽。assets 根目录的 favicon/logo 除外。
                low = fn.lower()
                under_country = os.path.relpath(dirpath, base).count(os.sep) >= 0 and \
                    dirpath != base
                if under_country and low.endswith((".jpg", ".jpeg", ".png")):
                    continue
                p = os.path.join(dirpath, fn)
                key = os.path.relpath(p, ROOT).replace(os.sep, "/")
                out.append((p, key))
    out.sort(key=lambda x: x[1])
    return out


def cache_of(key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    return CACHE.get(ext, "no-cache")


def ctype_of(key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    if ext in CTYPE:
        return CTYPE[ext]
    guess, _ = mimetypes.guess_type(key)
    return guess or "application/octet-stream"


def main() -> int:
    ap = argparse.ArgumentParser(description="上传站点到华为云 OBS")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", default="cn-north-4",
                    help="cn-north-4 北京四 / cn-east-3 上海一 / cn-south-1 广州")
    ap.add_argument("--dry-run", action="store_true", help="只列文件与体积，不上传")
    ap.add_argument("--prefix", default="", help="对象键前缀，如 v1/")
    ap.add_argument("--only", default="",
                    help="只传这些目录（逗号分隔），如 assets。data/geo 现在走 jsDelivr 更快，"
                         "一般只需要传 assets")
    a = ap.parse_args()

    try:
        from envfile import load as load_env
        load_env()
    except Exception:
        pass

    files = collect(a.only)
    total = sum(os.path.getsize(p) for p, _ in files)
    print("待上传 %d 个文件，合计 %.2f MB" % (len(files), total / 1048576))
    by_ext = {}
    for p, k in files:
        e = os.path.splitext(k)[1].lower() or "(无)"
        by_ext[e] = by_ext.get(e, 0) + 1
    print("  类型分布: " + "  ".join("%s×%d" % (k, v)
                                   for k, v in sorted(by_ext.items(), key=lambda x: -x[1])))
    if a.dry_run:
        print("\n（--dry-run，不上传。清单前 10 条：）")
        for p, k in files[:10]:
            print("  %-42s %7d B  %-22s %s"
                  % (k, os.path.getsize(p), ctype_of(k).split(";")[0], cache_of(k)))
        return 0

    ak = (os.environ.get("HUAWEI_AK") or "").strip()
    sk = (os.environ.get("HUAWEI_SK") or "").strip()
    if not ak or not sk:
        print("\n请先设置环境变量：export HUAWEI_AK=xxx HUAWEI_SK=xxx")
        return 2

    try:
        from obs import ObsClient, PutObjectHeader
    except ImportError:
        print("缺少 SDK：pip install esdk-obs-python -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return 2

    server = "https://obs.%s.myhuaweicloud.com" % a.region
    cli = ObsClient(access_key_id=ak, secret_access_key=sk, server=server)
    print("\n连接 %s 桶 %s …" % (server, a.bucket))

    ok = fail = 0
    failed = []
    for i, (p, key) in enumerate(files, 1):
        obj_key = (a.prefix + key) if a.prefix else key
        try:
            with open(p, "rb") as f:
                data = f.read()
            # 注意：headers 必须传 PutObjectHeader 对象，传 dict 会被静默忽略。
            # 实测传 dict 时 .webp 被存成 binary/octet-stream，浏览器会当文件
            # 下载而不是显示图片 —— 这是阻断性问题。
            # 另外 PutObjectHeader.allowedAttr 里**没有** cacheControl，
            # 想设 Cache-Control 必须走 S3 兼容接口（scripts/set_cache_control.py）。
            # Cache-Control 只能走 extensionHeaders（标准头），
            # PutObjectHeader 里没有这个属性，硬赋值会被 SDK 静默丢掉 ——
            # 结果线上 2000 多张图一个缓存头都没有，每次访问都回源。
            hdr = PutObjectHeader(contentType=ctype_of(key))
            resp = cli.putContent(a.bucket, obj_key, data, headers=hdr,
                                  extensionHeaders={"Cache-Control": cache_of(key)})
            if resp.status >= 300:
                fail += 1
                failed.append((obj_key, resp.status, str(resp.errorMessage)[:80]))
                print("  [x] %-44s %s %s" % (obj_key, resp.status, resp.errorMessage))
            else:
                ok += 1
                if i % 10 == 0 or i == len(files):
                    print("  … %d/%d" % (i, len(files)))
        except Exception as e:
            fail += 1
            failed.append((obj_key, "EXC", str(e)[:100]))
            print("  [x] %-44s %s" % (obj_key, str(e)[:70]))

    # 静态网站域名（需在控制台开启「静态网站托管」后生效）
    host = "%s.obs-website.%s.myhuaweicloud.com" % (a.bucket, a.region)
    print("\n成功 %d，失败 %d" % (ok, fail))
    print("静态网站地址（需先开启静态网站托管）：https://%s/" % host)
    print("普通对象地址：https://%s.obs.%s.myhuaweicloud.com/index.html" % (a.bucket, a.region))
    if failed:
        print("\n失败明细：")
        for k, s, m in failed[:10]:
            print("  %-46s %s %s" % (k, s, m))
    cli.close()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
