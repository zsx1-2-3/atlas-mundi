# -*- coding: utf-8 -*-
"""把 235 份档案的「面板文字」合并成一份 data/cards.json。

为什么要有这个文件
------------------
每份档案 40~90KB，但其中 90% 是图片清单的冗长字段（remote_url、size、
license_url、encoding…）和国界轮廓坐标 —— 面板真正要显示的文字只有几百字节。

于是逐份 fetching 时，慢的那几份（大文件 + 网络抖动）就会让面板空白 0.几秒，
快的几份就像"瞬间出来"。用户看到的就是"有的国家快、有的国家慢"。

这里把「renderDetail 用到的全部字段」抽出来合并成一份，首屏空闲时一次取完
（约 350KB 原始 / 90KB gzip），之后点任何国家都是本地内存直出，0 延迟。

字段白名单对应 index.html 里 renderDetail() 的读取点，改那边要同步改这里。
"""
import glob
import gzip
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
OUT = os.path.join(DATA, 'cards.json')

SKIP = {'index.json', 'search.json', 'landmarks.json', 'cards.json'}

# renderDetail() 直接读的字段。多放一个字段就是白占用户流量，少放就要等网络。
TEXT_KEYS = (
    'iso_a2', 'iso_a3', 'name_zh', 'name_en', 'qid',
    'capital', 'continent', 'centroid',
    # geometry 只会是 'geo/xx.geojson' 这样的路径（cn/fr/us + 手工补的 VA），
    # 几十字节。带上它，面板就能用档案自带的轮廓画出「国界轮廓」那块 SVG，
    # 不必等 geo/ 分片那条链路 —— 梵蒂冈就是这么补上的。
    'geometry',
    'summary_text', 'history_text', 'custom_text', 'custom_facets',
    'review_short',
)

# 图片：面板要用 url（主图）/ thumb_url（胶片带）/ caption_zh（题注）/
# license+author（授权行）。remote_url 一条就 200 字符、title 是 Commons 文件名
# 一千多条加起来 45KB —— 面板一次都不读，全丢掉。
IMG_KEYS = ('url', 'thumb_url', 'caption_zh', 'license', 'author')

# 语言：renderDetail 只读这六个（名称/角色/音频/原文/译文/语种码）。
# audio_voice、audio_bytes、audio_status 那些是采集流水账，页面不用。
LANG_KEYS = ('name_zh', 'role', 'audio_url', 'tts_text', 'tts_text_zh', 'code')


def slim_image(it):
    o = {}
    for k in IMG_KEYS:
        v = it.get(k)
        if v:
            o[k] = v
    # 每张图带一个自己的版本号（文件大小 + 修改时间），前端拼 ?v=。
    # 原来所有资源共用全局 BUILD_TAG：改一行 CSS 也会让 2200 张图全部缓存失效，
    # 换完版本第一次打开明显变慢 —— 就是这个原因。按文件算，只有真换过的图会重下。
    rel = it.get("url") or ""
    try:
        import os as _os
        pth = _os.path.join(ROOT, rel.replace("/", _os.sep))
        st = _os.stat(pth)
        o["v"] = "%x" % ((st.st_size * 2654435761 + int(st.st_mtime)) & 0xFFFFFFFF)
    except Exception:
        pass
    return o


def slim_lang(l):
    o = {}
    for k in LANG_KEYS:
        v = l.get(k)
        if v:
            o[k] = v
    return o


def main():
    cards = {}
    kept = 0
    for path in sorted(glob.glob(os.path.join(DATA, '*.json'))):
        base = os.path.basename(path)
        if base in SKIP or base.startswith('_'):
            continue
        try:
            with io.open(path, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict) or d.get('status') == 'placeholder':
            continue
        iso = str(d.get('iso_a2') or base[:2]).upper()
        if not iso or len(iso) != 2:
            continue

        c = {}
        for k in TEXT_KEYS:
            if k in d and d[k] not in (None, '', [], {}):
                c[k] = d[k]
        c['iso_a2'] = iso

        # 边界那行要显示顶点数，但完整 geometry 路径几千字符 —— 只留计数。
        gs = d.get('geometry_stats') or {}
        if gs.get('vertices'):
            c['geometry_stats'] = {'vertices': gs['vertices']}

        imgs = d.get('landmark_images') or []
        if imgs:
            c['landmark_images'] = [slim_image(it) for it in imgs]

        langs = d.get('languages') or []
        if langs:
            c['languages'] = [slim_lang(l) for l in langs]

        cards[iso] = c
        kept += 1

    blob = json.dumps(cards, ensure_ascii=False, separators=(',', ':'))
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(blob)

    raw = len(blob.encode('utf-8'))
    gz = len(gzip.compress(blob.encode('utf-8'), 9))
    print('国家数 %d' % kept)
    print('cards.json  %.1f KB  (gzip %.1f KB)' % (raw / 1024.0, gz / 1024.0))
    print('平均每国 %.0f 字节' % (raw / max(kept, 1)))


if __name__ == '__main__':
    main()
