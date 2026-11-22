# -*- coding: utf-8 -*-
"""生成球面用的柯本气候分类图层（类别序号图 + 调色板）。

数据来源
  分类：Kottek et al. 2006 / Rubel et al. 2017 的 0.5° 全球柯本分类
        （koeppen-geiger.vu-wien.ac.at，数据文件 Koeppen-Geiger-ASCII.txt，
        时段 1961-1990，30 个类别）。
  海岸线：项目自带的 geo/borders.json（1/50° 国界多边形，index.html 画国界用的同一份）
        在本脚本里光栅化成陆地覆盖率（见 build_landmask）。这样气候层的边缘和球面上的
        国界线、海岸线是同一套几何，放大后不会出现 0.5° 的方块台阶。

为什么分类不用那张 5′ 彩色图
  它是 1986-2010 的全球产品，但中国境内站点稀疏、插值偏干：北京、天津、石家庄
  一整片被划成 BSk（温带半干旱），华北平原的温带季风气候几乎消失，与教材口径明显冲突。
  0.5° 的 1961-1990 版本在北京、济南、兰州等地的结果与教材一致，因此分类用它。
  类别的空间精度因此是 0.5°（约 55 km），这一点写在页面上。

  代价是类别值在整格里是常量，直接放大就是方块。所以下面做了两件事：
  高斯 + 低频域扭曲把「等值线」从经纬网格上拽下来；海岸线则整条改用项目的国界多边形，
  和球面上画的国界线是同一份几何。

输出的灰度图约定
  R = 类别序号 * 8，255 表示海洋/无数据；G = 高原标记；A = 陆地覆盖率（0-255）。
  着色器里 floor(R/8) 得类别，查调色板取色；A 用来做海岸线抗锯齿。

  绿通道另存「高原」标记（255 = 是）。教材的「高原山地气候」在柯本分类里没有对应项，
  判据是：纬度 45° 以内，且海拔 2800m 以上（青藏高原、安第斯、帕米尔），
  或低纬的苔原/冰原（高山苔原）。极地苔原都在 45° 以外，不会被误判。
  标记单独占一个通道，是为了不破坏原始类别 —— 切回柯本 30 类时仍能显示本来的分类。
"""
import json
import os
import zipfile

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.abspath(os.path.join(ROOT, '..'))
ASCII_ZIP = os.path.join(DATA, 'kg', 'Koeppen-Geiger-ASCII.zip')
BORDERS = os.path.join(ROOT, 'geo', 'borders.json')   # 项目画国界用的同一份 1/50° 多边形
DEM = os.path.join(DATA, 'kg', 'wc', 'wc2.1_10m_elev.tif')
OUT_DIR = os.path.join(ROOT, 'assets', 'tex')
MASK_SS = 2                      # 掩膜先按 2× 分辨率光栅化再降采样，保住亚像素海岸线

W, H = 8192, 4096
GRID_W, GRID_H = 720, 360        # 0.5° 网格
HIGHLAND_M = 2800                # 高原判据：海拔（米）
HIGHLAND_LAT = 45                # 高原判据：纬度绝对值上限
SIGMA_CELL = 0.85                # 类别支持场的平滑半径（单位：0.5° 格）
WARP_AMP = 0.45                  # 边界域扭曲幅度（单位：0.5° 格），让等值线脱离经纬网格

# 代码 -> (颜色, 组, 中文名, 判读要点, 典型分布)
CLASSES = [
    ('Af',  '#960000', 'A', '热带雨林气候', '各月降水均超过 60mm，全年高温多雨',
     '亚马孙平原、刚果盆地、马来群岛'),
    ('Am',  '#F50000', 'A', '热带季风气候', '干湿季分明，湿季降水极多，干季仍不至于成旱',
     '印度西岸、缅甸、菲律宾'),
    ('As',  '#FF5A5A', 'A', '热带夏干气候', '极罕见：夏半年反而少雨，冬半年多雨',
     '夏威夷背风局部、加勒比个别岛屿'),
    ('Aw',  '#FFB2B2', 'A', '热带草原气候', '干湿季分明且干季更长，最热月出现在雨季前',
     '东非高原、巴西高原、德干高原'),
    ('BSh', '#CF901F', 'B', '热带半干旱气候', '降水不足但未到沙漠程度，蒸发旺盛',
     '萨赫勒、印度西北、澳大利亚内陆边缘'),
    ('BSk', '#CCA54A', 'B', '温带半干旱气候', '冷冬半干旱草原，降水多集中在夏季',
     '蒙古、中亚、北美大平原西部、中国内蒙古'),
    ('BWh', '#F2C314', 'B', '热带沙漠气候', '年降水量远低于潜在蒸散，全年炎热',
     '撒哈拉、阿拉伯半岛、澳大利亚中部'),
    ('BWk', '#FFFD60', 'B', '温带沙漠气候', '同上但冬季寒冷，气温年较差大',
     '塔克拉玛干、戈壁、伊朗中部'),
    ('Cfa', '#1E880C', 'C', '亚热带湿润气候', '四季有雨、夏雨偏多，最热月高于 22℃',
     '中国东南、美国东南、阿根廷潘帕斯'),
    ('Cfb', '#005800', 'C', '温带海洋性气候', '全年温和、降水均匀，最热月低于 22℃',
     '西欧、新西兰、智利南部'),
    ('Cfc', '#003200', 'C', '亚寒带海洋性气候', '短夏凉爽，一年中少于四个月高于 10℃',
     '冰岛、挪威沿海、阿拉斯加东南'),
    ('Csa', '#88EC00', 'C', '地中海气候（夏干热）', '夏炎热干燥、冬温和多雨，雨热不同期',
     '地中海沿岸、加州、智利中部'),
    ('Csb', '#1BDE00', 'C', '地中海气候（夏干暖）', '同上但夏季不酷热',
     '旧金山、开普敦、智利中部'),
    ('Csc', '#05AA00', 'C', '地中海气候（夏干冷）', '高海拔少见，夏季冷凉',
     '智利南部高地、安第斯局部'),
    ('Cwa', '#B2BC00', 'C', '亚热带季风气候', '夏湿热、冬干凉，冬季降水不足夏季十分之一',
     '中国华南、台湾、印度北部'),
    ('Cwb', '#939300', 'C', '亚热带高原气候', '冬干夏雨，因海拔高夏季不酷热',
     '昆明、墨西哥高原、安第斯山区'),
    ('Cwc', '#595405', 'C', '亚寒带高原气候', '极罕见，高原上冬干且夏凉',
     '玻利维亚高原'),
    ('Dfa', '#550252', 'D', '温带大陆性湿润气候', '夏热冬冷、全年有雨，最冷月低于 0℃',
     '美国中西部、中国华北部分'),
    ('Dfb', '#7C007C', 'D', '温带大陆性气候', '夏暖冬寒，最热月低于 22℃',
     '东欧、俄罗斯欧洲部分、加拿大南部'),
    ('Dfc', '#CD0ACD', 'D', '亚寒带针叶林气候', '长冬短夏，一年中仅一至三个月高于 10℃',
     '西伯利亚、加拿大北部'),
    ('Dfd', '#FE6DFE', 'D', '亚寒带极冷冬气候', '同上但最冷月低于 -38℃',
     '东西伯利亚'),
    ('Dsa', '#726472', 'D', '地中海型大陆性（夏干）', '夏干热、冬冷，雨热不同期',
     '伊朗、土耳其、中亚山地'),
    ('Dsb', '#929292', 'D', '地中海型大陆性（夏暖）', '同上，夏季较凉',
     '伊朗北部、高加索'),
    ('Dsc', '#BFBFBF', 'D', '地中海型大陆性（夏冷）', '同上，夏季冷凉',
     '阿拉斯加内陆、西伯利亚山地'),
    ('Dwa', '#7831BE', 'D', '温带季风气候', '夏湿热、冬干冷，冬季降水不足夏季十分之一',
     '中国华北与东北、朝鲜半岛'),
    ('Dwb', '#B567FA', 'D', '温带季风气候（夏暖）', '同上，最热月低于 22℃',
     '中国东北北部、俄罗斯远东'),
    ('Dwc', '#C799FA', 'D', '亚寒带季风气候', '冬干冷、夏季短而凉',
     '西伯利亚东部、蒙古北部'),
    ('Dwd', '#D2D2FF', 'D', '亚寒带季风气候（极冷冬）', '最冷月低于 -38℃',
     '东西伯利亚，雅库茨克一带'),
    ('ET',  '#64FFFF', 'E', '苔原气候', '最热月 0~10℃，无木本植物',
     '北极沿岸、青藏高原部分'),
    ('EF',  '#F0FFFF', 'E', '冰原气候', '全年冰冻，最热月低于 0℃',
     '南极、格陵兰冰盖'),
]
SHORT = {
    'Af': '雨林', 'Am': '季风', 'Aw': '草原', 'BWh': '沙漠',
    'Cwa': '亚热带季风', 'Csa': '地中海',
    'Dwa': '温带季风', 'Cfb': '温带海洋性', 'BSk': '温带大陆性',
    'Dfc': '亚寒带针叶林', 'ET': '极地', 'H': '高原山地',
}
GROUP_NAMES = {'A': 'A 热带', 'B': 'B 干旱', 'C': 'C 亚热带／温带',
               'D': 'D 大陆性', 'E': 'E 极地'}

# 教材口径：人教版必修一讲的 12 种。柯本 30 类按这张表归并。
#   ids 是柯本序号（CLASSES 的下标）；高原山地没有柯本对应项，靠 G 通道 + 海拔判据，
#   所以 ids 留空。前端切「教材模式」时，先查 REMAP 落到这 12 个色，再看 G 通道改判高原。
EXAM12 = [
    ('Af',  '热带雨林气候',        '#17663A', [0],           '全年高温多雨，各月均温 25℃ 以上，年降水 2000mm 以上', '亚马孙平原、刚果盆地、马来群岛'),
    ('Am',  '热带季风气候',        '#2F8A4F', [1],           '全年高温，分旱雨两季，雨季降水集中且量大', '印度半岛西岸、中南半岛、菲律宾'),
    ('Aw',  '热带草原气候',        '#B0B654', [3, 2],        '全年高温，干湿季分明，湿季草木繁茂、干季草原枯黄', '东非高原、巴西高原、澳大利亚北部'),
    ('BWh', '热带沙漠气候',        '#DFAB63', [6, 4],        '全年炎热干燥，年降水不足 250mm，气温日较差大', '撒哈拉、阿拉伯半岛、澳大利亚中西部'),
    ('Cwa', '亚热带季风气候',      '#79BF6E', [14, 8, 15, 16], '夏季高温多雨、冬季温和少雨，最冷月 0~15℃', '秦岭—淮河以南、美国东南部、巴西东南部'),
    ('Csa', '地中海气候',          '#C2A34D', [11, 12, 13, 21, 22, 23], '夏季炎热干燥、冬季温和多雨，雨热不同期', '地中海沿岸、加州、智利中部、南非西南'),
    ('Dwa', '温带季风气候',        '#5A9C8C', [24, 25],      '夏季高温多雨、冬季寒冷干燥，最冷月低于 0℃', '中国华北与东北、朝鲜半岛、日本北部'),
    ('Cfb', '温带海洋性气候',      '#4D8FB2', [9, 10],       '全年温和湿润，最热月低于 22℃、最冷月高于 0℃', '西欧、新西兰、智利南部、北美西北沿岸'),
    ('BSk', '温带大陆性气候',      '#7A8EA8', [5, 7, 17, 18], '冬冷夏热、气温年较差大，降水少且集中在夏季', '蒙古、中亚、北美大平原、中国内蒙古与新疆'),
    ('Dfc', '亚寒带针叶林气候',    '#5A76BC', [19, 20, 26, 27], '冬季漫长严寒，夏季短促温暖，最热月 10℃ 以上不足四个月', '西伯利亚、加拿大北部、北欧'),
    ('ET',  '极地气候（苔原／冰原）', '#D5DFE9', [28, 29],    '全年严寒，最热月低于 10℃，无木本植物', '北冰洋沿岸、格陵兰、南极大陆'),
    ('H',   '高原山地气候',        '#A596AD', [],             '海拔高、气温低，年较差小、日较差大，垂直地带性显著', '青藏高原、安第斯山、埃塞俄比亚高原'),
]


def load_grid():
    """读 0.5° ASCII，返回 GRID_H x GRID_W 的类别序号（255=海）。"""
    index_of = {c[0]: i for i, c in enumerate(CLASSES)}
    grid = np.full((GRID_H, GRID_W), 255, dtype=np.uint8)
    unknown = set()
    with zipfile.ZipFile(ASCII_ZIP) as z:
        txt = z.read('Koeppen-Geiger-ASCII.txt').decode()
    for line in txt.splitlines()[1:]:
        p = line.split()
        if len(p) < 3:
            continue
        lat, lon, code = float(p[0]), float(p[1]), p[2]
        i = index_of.get(code)
        if i is None:
            unknown.add(code)
            continue
        row = int((90 - lat) / 180 * GRID_H)
        col = int((lon + 180) / 360 * GRID_W)
        grid[min(max(row, 0), GRID_H - 1), min(max(col, 0), GRID_W - 1)] = i
    if unknown:
        print('  跳过未知类别:', sorted(unknown))
    return grid


def load_highland(grid):
    """0.5° 网格的「高原」标记：低纬 + 高海拔（或低纬苔原）。

    高程用 WorldClim 2.1 的 10′ 栅格（2160×1080），3×3 块平均降采样到 0.5°，
    与分类网格逐格对齐。
    """
    flag = np.zeros((GRID_H, GRID_W), dtype=np.uint8)
    if not os.path.exists(DEM):
        print('  ! 没有高程文件，跳过高原标记：', DEM)
        return flag
    dem = np.array(Image.open(DEM)).astype(np.float32)
    dem[dem < -1000] = 0.0                       # 海洋 nodata（-32768）
    dem = dem.reshape(GRID_H, 3, GRID_W, 3).mean(axis=(1, 3))

    lat_g = 90.0 - (np.arange(GRID_H) + 0.5) / GRID_H * 180.0
    low = np.abs(lat_g)[:, None] < HIGHLAND_LAT
    cold = (grid == 28) | (grid == 29)           # ET 苔原 / EF 冰原
    land = grid != 255
    flag[low & land & ((dem >= HIGHLAND_M) | cold)] = 255
    print('  高原标记：%d 格（%.1f%% 陆地）'
          % (int((flag > 0).sum()), 100.0 * (flag > 0).sum() / max(1, land.sum())))
    return flag


def _clip_half(x, y, val, keep_ge):
    """把闭合多边形裁到 x >= val（keep_ge）或 x <= val 的一半（Sutherland–Hodgman）。

    只在处理跨整圈的环（南极洲）时用：那种环在等距圆柱上首尾相隔 360°，
    fillPoly 自动闭合的那条边会横穿整幅画布，把多边形切成两半、偶奇规则失效。
    先在 x=0 / x=±180 处把它切开，每半段都变成正常多边形。
    """
    if len(x) < 3:
        return None
    xs, ys = [], []
    n = len(x)
    for i in range(n):
        xi, yi = x[i], y[i]
        xj, yj = x[i - 1], y[i - 1]
        ini = xi >= val if keep_ge else xi <= val
        inj = xj >= val if keep_ge else xj <= val
        if ini != inj:
            t = 0.0 if xi == xj else (val - xj) / (xi - xj)
            xs.append(val)
            ys.append(yj + t * (yi - yj))
        if ini:
            xs.append(xi)
            ys.append(yi)
    if len(xs) < 3:
        return None
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def _clip_open(x, y, val, keep_ge):
    """把开放折线裁到 x >= val（keep_ge）或 x <= val 的一侧，保持点的顺序。

    和 _clip_half 的区别是不处理首尾闭合边 —— 跨整圈的环必须先当开放折线裁，
    否则 i=0 那条「末点回到首点」的边会被插到序列最前面，闭合边跑到画布另一头。
    """
    xs, ys = [], []
    n = len(x)
    for i in range(n):
        xi, yi = x[i], y[i]
        ini = xi >= val if keep_ge else xi <= val
        if i == 0:
            if ini:
                xs.append(xi)
                ys.append(yi)
            continue
        xj, yj = x[i - 1], y[i - 1]
        inj = xj >= val if keep_ge else xj <= val
        if ini != inj:
            t = 0.0 if xi == xj else (val - xj) / (xi - xj)
            xs.append(val)
            ys.append(yj + t * (yi - yj))
        if ini:
            xs.append(xi)
            ys.append(yi)
    if len(xs) < 3:
        return None
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def build_landmask(W, H, ss=MASK_SS):
    """从 geo/borders.json 光栅化陆地覆盖率，返回到 H×W、0-255 的灰度。

    所有国家的闭合环并起来就是陆地（含岛屿）。图上 index.html 画国界用的也是这份数据，
    所以气候层的边缘能和球面上的国界线严丝合缝。

    三个坑：
      1. 环如果跨 180°（俄罗斯楚科奇、斐济），直接用原经度连线会在图上横穿
         整个地球，必须先沿序列展开成连续经度；
      2. 展开后跨度逼近 360° 的环（南极洲绕极点一整圈）不能直接丢给 fillPoly ——
         首尾相隔 360°，自动闭合边会横穿画布，结果只剩零星几块。必须先按 x 切段；
      3. 先按 ss 倍分辨率画，再用 BOX 降采样回去 —— 每个输出像素拿到的是 2×2 的
         覆盖率，海岸线才有亚像素精度。
    """
    with open(BORDERS, encoding='utf-8') as f:
        d = json.load(f)
    scale = d['s']
    ws, hs = W * ss, H * ss
    canvas = np.zeros((hs, ws), dtype=np.uint8)

    def draw(x, y):
        py = np.clip((90.0 - y) / 180.0 * hs, 0.0, hs - 0.5)
        for k in (-1, 0, 1):
            px = (x + 360.0 * k + 180.0) / 360.0 * ws
            if px.max() < 0 or px.min() > ws:
                continue
            pts = np.stack([px * 16.0, py * 16.0], axis=1).astype(np.int32)
            cv2.fillPoly(canvas, [pts], 255, lineType=cv2.LINE_AA, shift=4)

    n_poly = n_wrap = 0
    for rings in d['c'].values():
        for ring in rings:
            if len(ring) < 3:
                continue
            x = np.array([p[0] for p in ring], dtype=np.float64) / scale
            y = np.array([p[1] for p in ring], dtype=np.float64) / scale
            for i in range(1, len(x)):
                dd = x[i] - x[i - 1]
                if abs(dd) > 180.0:
                    x[i] -= 360.0 * round(dd / 360.0)
            span = x.max() - x.min()
            if span > 350.0:
                # 绕了整圈（南极洲）：三个副本分别裁到 [-180, 180] 再画。
                # 只裁还不够 —— 环的首尾在同一个经度上，裁完的闭合边会把多边形
                # 切回"海岸线以北的月牙"。必须显式补上一条沿极点的底边，
                # 让闭合路径从海岸线下到极点、沿底边横穿、再回起点。
                n_wrap += 1
                pole = -90.0 if float(y.mean()) < 0 else 90.0
                # 原环在极点上有个来回的尖刺（走到 -90 再原路返回）。留着它再补底边，
                # 往返线段会把极点附近的面积算两次，偶奇规则一抵消就成了空洞。
                m = y > pole + 0.05
                if m.sum() < 3:
                    continue
                xr, yr = x[m], y[m]
                # 从最西点起步再滚一圈：起点落在中间时，裁剪后序列会先吐尾部交点、
                # 再跳回主体，闭合边直接横穿画布。滚到最西点后顺序才是连贯的。
                i0 = int(np.argmin(xr))
                xr = np.roll(xr, -i0)
                yr = np.roll(yr, -i0)
                for k in (-1, 0, 1):
                    xs = xr + 360.0 * k
                    if xs.max() < -180.0 or xs.min() > 180.0:
                        continue
                    part = _clip_open(xs, yr, 180.0, False)
                    if part is None:
                        continue
                    part = _clip_open(part[0], part[1], -180.0, True)
                    if part is None:
                        continue
                    px_, py_ = part
                    px_ = np.append(px_, [px_[-1], px_[0]])
                    py_ = np.append(py_, [pole, pole])
                    draw(px_, py_)
                    n_poly += 1
                continue
            if span > 360.0:
                continue
            draw(x, y)
            n_poly += 1
    if ss > 1:
        canvas = np.array(Image.fromarray(canvas).resize((W, H), Image.BOX))
    # 覆盖率再软一档：多边形在 2× 画布上的抗锯齿只有 1 px，降到 8192 后仍是 1 texel，
    # 球面放大就是一级台阶。σ=1.4（约 0.06°、7 km）把过渡铺到 3 texel，
    # 50% 等值线位置不变（海岸线不会漂），只是边缘变连贯。
    canvas = cv2.GaussianBlur(canvas, (0, 0), 1.4)
    print('  国界多边形 %d 段（其中绕整圈环 %d 个），陆地覆盖率均值 %.2f%%'
          % (n_poly, n_wrap, 100.0 * canvas.mean() / 255))
    return canvas


def spread_ids(grid, rounds=4):
    """把海格（255）用 3×3 邻域里最近的陆地类别填上，只铺 rounds 层。

    用途是让支持场在海岸线上连续：0.5° 网格里海岸那一格常常被标成海，
    支持场在那里突然掉到 0，argmax 会跳回 255，陆地就透明了。
    只铺几层而不是铺满全球 —— 远洋留 255 无所谓，那里 alpha 本来就是 0。
    """
    out = grid.copy()
    known = out != 255
    for _ in range(rounds):
        if known.all():
            break
        # 经度方向环绕，纬度方向不环绕（两极不是邻居）
        pad = np.pad(out, ((1, 1), (0, 0)), constant_values=255)
        padk = np.pad(known, ((1, 1), (0, 0)), constant_values=False)
        nxt = out.copy()
        nxtk = known.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                s = np.roll(pad[1 + dy:1 + dy + out.shape[0]], dx, axis=1)
                sk = np.roll(padk[1 + dy:1 + dy + out.shape[0]], dx, axis=1)
                m = (~known) & sk
                nxt[m] = s[m]
                nxtk[m] = True
        out, known = nxt, nxtk
    return out


def blur_wrap(m, sigma):
    """沿经度环绕的高斯模糊（GaussianBlur 不接受 BORDER_WRAP）。"""
    p = max(1, int(np.ceil(sigma * 3)))
    mp = np.concatenate([m[:, -p:], m, m[:, :p]], axis=1)
    mp = cv2.GaussianBlur(mp, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
    return mp[:, p:p + m.shape[1]]


def main():
    print('读取 0.5° 分类网格 ...')
    grid = load_grid()
    print('  有效单元格 %.1f%%' % (100 * (grid != 255).mean()))

    # 支持场：每个类别在 0.5° 网格上做一次高斯，得到连续场，再用三次插值放大到 8192。
    #
    # 关键在「先做小图、再插值放大」，而不是「先放大、再模糊」。
    # 后者的输入是一堆 5.7 px 的硬方块，高斯只能在方块直角上磨出个圆角，
    # argmax 出来的边界仍旧沿经纬网格走，放大看就是一级一级的台阶（σ 再大也只是
    # 把台阶磨钝，不会消失）。前者先把方块化成连续场，插值上去的等值线本身就是
    # 光滑曲面上的曲线，0.5° 的网格痕迹彻底没了。
    print('平滑类别边界 sigma=%.2f 格 / 扭曲 %.2f 格 ...' % (SIGMA_CELL, WARP_AMP))
    rng = np.random.default_rng(20261121)
    base = spread_ids(grid, rounds=4)

    def noise(seed):
        n = rng.normal(size=(GRID_H // 3, GRID_W // 3)).astype(np.float32)
        n = cv2.resize(n, (GRID_W, GRID_H), interpolation=cv2.INTER_CUBIC)
        n = cv2.GaussianBlur(n, (0, 0), 1.6)
        return n / max(float(np.abs(n).max()), 1e-6)

    gyy, gxx = np.mgrid[0:GRID_H, 0:GRID_W].astype(np.float32)
    gmapx = (gxx + noise(1) * WARP_AMP).astype(np.float32)
    gmapy = (gyy + noise(2) * WARP_AMP).astype(np.float32)

    best_score = np.zeros((H, W), dtype=np.float32)
    best_id = np.full((H, W), 255, dtype=np.uint8)
    for i in range(len(CLASSES)):
        m = (base == i).astype(np.float32)
        if not m.any():
            continue
        m = blur_wrap(m, SIGMA_CELL)
        if WARP_AMP > 0:
            m = cv2.remap(m, gmapx, gmapy, interpolation=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_WRAP)
        up = cv2.resize(m, (W, H), interpolation=cv2.INTER_CUBIC)
        np.clip(up, 0.0, None, out=up)
        better = up > best_score
        best_score[better] = up[better]
        best_id[better] = i
    del best_score

    # 海陆掩膜：项目自己的国界多边形（1/50°），16384 宽 → 面积平均降到 8192。
    # 保留的是 0-255 的覆盖率，而不是先二值化：海岸线上那一条像素的灰度
    # 就是「这个像素有多少落在陆地」，写进 alpha 通道，着色器据此做抗锯齿，
    # 放大后不会有锯齿状的海岸。
    cover = build_landmask(W, H)
    land = cover > 64
    keep = land & (best_id != 255)
    print('  掩膜裁切：陆地 %.2f%%，其中无类别 %.2f%%'
          % (100.0 * land.mean(),
             100.0 * (land & (best_id == 255)).sum() / max(1, int(land.sum()))))

    # 类别不按掩膜二值裁：掩膜外那一圈像素也留着最近的类别（支持场本来就扩散到海上），
    # 海陆完全交给 alpha 的覆盖率去判定。否则「最后一个陆地像素」会变成硬边，
    # 覆盖率那半个像素的信息就白留了 —— 放大后仍是一格一格的台阶。
    # 支持场里没有类别的陆地（离网格陆格太远的小岛、南极冰架）用低分辨率的最近类兜底，
    # 否则 R 会留成 255、被着色器 discard，陆地上出现透明的洞。
    fallback = cv2.resize(base, (W, H), interpolation=cv2.INTER_NEAREST)
    ids = np.where(best_id == 255, fallback, best_id).astype(np.uint8)
    # 等值线上还会有零星单像素跳变（argmax 在两类支持度接近处来回翻），
    # 3×3 中值能去掉这点抖动，顺带清掉小于 3 px 的孤立小斑。
    ids = cv2.medianBlur(ids, 3)

    print('统计高原标记 ...')
    high = load_highland(grid)
    # 高原标记也必须先化成连续场再上采样。直接 NEAREST 放大就是 5.7 px 的方块，
    # 高原东缘会沿着 0.5° 网格切出一圈直角台阶 —— 这是「边界看着还是方块」的第二个来源。
    hf = blur_wrap(high.astype(np.float32) / 255.0, SIGMA_CELL)
    hf = cv2.resize(hf, (W, H), interpolation=cv2.INTER_CUBIC)
    high_full = np.where(hf > 0.5, 255, 0).astype(np.uint8)
    high_full = np.where(keep, high_full, 0).astype(np.uint8)

    arr = np.where(ids == 255, 255, ids.astype(np.int16) * 8).astype(np.uint8)
    # alpha 只由陆地覆盖率决定，和类别解耦：分辨率 0.5° 的分类网格在海岸、小岛上
    # 常常没有对应格，若让 alpha 跟着类别走，那些地方会整块透明。
    # 注意离屏 canvas 取像素是预乘的，海上读回来 RGB 会是 0，前端判断海陆一律看 alpha。
    alpha = cover.copy()
    rgba = np.dstack([arr, high_full, np.zeros_like(arr), alpha])
    png = os.path.join(OUT_DIR, 'koppen-id-8192.png')
    Image.fromarray(rgba, mode='RGBA').save(png, optimize=True)
    print('写出 %s  %.1f KB' % (png, os.path.getsize(png) / 1024))

    # ---------------------------------------------------------------- 主站接口
    # 主站的专题图层是「一张 RGBA 直接当颜色叠上去」（uLayerMap），所以这里输出
    # 一张已经烘好 12 类配色的彩色图：RGB = 教材配色，A = 陆地覆盖率。
    #
    # 为什么不改成「索引图 + 前端查表」：
    #   显示要 Linear 采样 + mipmap —— 远景才不闪、斜视才不糊、边界才有抗锯齿；
    #   索引图必须 Nearest —— 插值会把 3 和 4 混成 3.5，那是个不存在的类别。
    #   两件事对采样方式的要求正好相反，一张贴图做不到，所以图（彩色）与索引（灰度）
    #   分开：这张只管好看，climate_idx.png 只管报名字。
    lut = np.zeros((32, 4), dtype=np.uint8)
    for c, zh, hx, ids_, key, note in EXAM12:
        if not ids_:
            continue
        lut[ids_] = [int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16), 255]
    safe = np.where(ids > 31, 0, ids)
    rgb = lut[safe][:, :, :3].copy()
    h_col = dict((c, h) for c, zh, h, ids_, key, note in EXAM12)['H']
    rgb[high_full > 127] = [int(h_col[1:3], 16), int(h_col[3:5], 16), int(h_col[5:7], 16)]
    rgba_main = np.dstack([rgb, alpha])

    # 8K 主图：无损 WebP。12 色 + 连续 alpha 的图，PNG 能压到 2.4MB，无损 WebP 只要一半，
    # 而且像素逐字节一致（lossless），不会出现「图例色和球面色差一点」。
    p8 = os.path.join(OUT_DIR, 'climate.webp')
    Image.fromarray(rgba_main, mode='RGBA').save(p8, 'WEBP', lossless=True, exact=True, method=6)
    print('写出 %s  %.1f KB' % (p8, os.path.getsize(p8) / 1024))

    # 4K 低配版：颜色也走面积平均 —— 边界上那圈中间色正好当抗锯齿，
    # 比最近邻硬切一圈台阶好看得多。alpha 同样面积平均。
    lo_rgb = np.array(Image.fromarray(rgb).resize((W // 2, H // 2), Image.BOX))
    lo_a = np.array(Image.fromarray(alpha).resize((W // 2, H // 2), Image.BOX))
    rgba_lo = np.dstack([lo_rgb, lo_a])
    p4 = os.path.join(OUT_DIR, 'climate-lo.webp')
    Image.fromarray(rgba_lo, mode='RGBA').save(p4, 'WEBP', lossless=True, exact=True, method=6)
    print('写出 %s  %.1f KB' % (p4, os.path.getsize(p4) / 1024))

    # ---------------------------------------------------------------- 柯本 30 类（专业口径）
    # 教材模式回答「考试要背的 12 种」，柯本模式回答「气候学家怎么分」。
    # 两者共用同一份索引数据，只是配色和归并不同 —— 所以这张图也是从 ids 直接上色，
    # 不经过 EXAM12 的归并，高原标记在这里不生效（高原山地是教材概念）。
    # 只出 4K：这是给「想细看」的人准备的，8K 再翻一倍显存不划算。
    klut = np.zeros((32, 4), dtype=np.uint8)
    for i, (c, hx, g, zh, key, where) in enumerate(CLASSES):
        klut[i] = [int(hx[1:3], 16), int(hx[3:5], 16), int(hx[5:7], 16), 255]
    krgb = klut[safe][:, :, :3]
    lo_k = np.array(Image.fromarray(krgb).resize((W // 2, H // 2), Image.BOX))
    pjk = os.path.join(OUT_DIR, 'climate-koppen.webp')
    Image.fromarray(np.dstack([lo_k, lo_a]), mode='RGBA').save(
        pjk, 'WEBP', lossless=True, exact=True, method=6)
    print('写出 %s  %.1f KB' % (pjk, os.path.getsize(pjk) / 1024))

    # 探针索引：4K，R = 柯本序号 + 1（0 = 海洋），G = 高原标记。
    # 为什么要 G：教材的「高原山地气候」在柯本里没有对应项，拉萨的柯本值是 Dwc，
    # 只读 R 会把拉萨报成「亚寒带针叶林」—— 这正是实验室里踩过的坑。
    # 降采样用最近邻：类别号不能被平均（3 和 4 平均出 3.5 是不存在的类）。
    idx8 = np.where(ids == 255, 0, ids.astype(np.int16) + 1).astype(np.uint8)
    lo_i = np.array(Image.fromarray(idx8).resize((W // 2, H // 2), Image.NEAREST))
    lo_h = np.array(Image.fromarray(high_full).resize((W // 2, H // 2), Image.NEAREST))
    rgba_idx = np.dstack([lo_i, lo_h, np.zeros_like(lo_i), np.full_like(lo_i, 255)])
    pi = os.path.join(OUT_DIR, 'climate_idx.png')
    Image.fromarray(rgba_idx, mode='RGBA').save(pi, optimize=True)
    print('写出 %s  %.1f KB' % (pi, os.path.getsize(pi) / 1024))

    rows = [{'i': i, 'c': c, 'zh': zh, 'hex': hx, 'g': g, 'key': key, 'note': where}
            for i, (c, hx, g, zh, key, where) in enumerate(CLASSES)]
    js = ('// 自动生成，勿手改：scripts/build_koppen_layer.py\n'
          'window.KOPPEN_PALETTE = ' +
          json.dumps(rows, ensure_ascii=False, separators=(',', ':')) + ';\n'
          'window.KOPPEN_GROUPS = ' + json.dumps(GROUP_NAMES, ensure_ascii=False) + ';\n')
    out_js = os.path.join(OUT_DIR, 'koppen-palette.js')
    with open(out_js, 'w', encoding='utf-8', newline='\n') as f:
        f.write(js)
    print('写出 %s  %d 类' % (out_js, len(rows)))

    # 主站用的一张总表：教材 12 类（含判读要点，气泡要显示）+ 柯本 30 类（切口径用）。
    # 前端拿 exam[].ids 反推 REMAP，把柯本序号映射到教材序号；两套调色板各 32 槽。
    #
    # 面积占比在这里算：按 cos(纬度) 加权（等距圆柱贴图每行代表的面积不等），
    # 再用海岸覆盖率当权重 —— 海岸那一圈像素是半陆半海，不该整格计入。
    # 前端图例直接显示这个数，和贴图上的色块面积严格一致（不是另算一份近似值）。
    _lat = 90.0 - (np.arange(H) + 0.5) / H * 180.0
    _w = np.cos(np.radians(_lat))[:, None] * (cover.astype(np.float32) / 255.0)
    _tot = float(_w.sum())
    share = []
    for c, zh, hx, ids_, key, note in EXAM12:
        if c == 'H':
            m = (high_full > 127)
        else:
            sel = np.isin(ids, ids_) & (high_full < 128)
            m = sel
        share.append(round(100.0 * float((_w * m).sum()) / _tot, 1))
    print('  教材 12 类面积占比：' + ' · '.join(
        '%s %.1f%%' % (e[0], s) for e, s in zip(EXAM12, share)),
        '（合计 %.1f%%）' % sum(share))

    pal = {
        'alt': len(EXAM12) - 1,
        'share': share,
        'exam': [{'c': c, 'zh': zh, 'short': SHORT[c], 'hex': hx,
                  'ids': ids, 'key': key, 'note': note}
                 for c, zh, hx, ids, key, note in EXAM12],
        'koppen': rows,
    }
    pj = os.path.join(OUT_DIR, 'climate-pal.js')
    with open(pj, 'w', encoding='utf-8', newline='\n') as f:
        f.write('// 自动生成，勿手改：scripts/build_koppen_layer.py\n'
                'window.CLIMATE_PAL = ' +
                json.dumps(pal, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print('写出 %s  教材 %d 类 / 柯本 %d 类' % (pj, len(EXAM12), len(rows)))

    checks = [('北京', 39.90, 116.41, 'Dwa'), ('上海', 31.23, 121.47, 'Cfa'),
              ('广州', 23.13, 113.26, 'Cwa/Cfa'), ('哈尔滨', 45.80, 126.53, 'Dwa'),
              ('乌鲁木齐', 43.83, 87.62, 'BWk/BSk'), ('拉萨', 29.65, 91.10, 'Dwc/Cwb → 高原'),
              ('昆明', 25.04, 102.71, 'Cwb → 非高原'), ('加德满都', 27.70, 85.30, '→ 高原'),
              ('伦敦', 51.51, -0.13, 'Cfb'), ('罗马', 41.90, 12.50, 'Csa'),
              ('开罗', 30.04, 31.24, 'BWh'), ('新加坡', 1.35, 103.82, 'Af'),
              ('雅库茨克', 62.03, 129.73, 'Dfd'), ('珀斯', -31.95, 115.86, 'Csa'),
              ('圣保罗', -23.55, -46.63, 'Cfb'), ('努克', 64.18, -51.69, 'ET')]
    print('\n抽查（括号内为常见口径，▣ 表示被标为高原）：')
    for name, lat, lon, expect in checks:
        x = min(max(int((lon + 180) / 360 * W), 0), W - 1)
        y = min(max(int((90 - lat) / 180 * H), 0), H - 1)
        v = int(arr[y, x])
        got = CLASSES[v // 8][0] if v != 255 else '（海）'
        if high_full[y, x]:
            got += ' ▣'
        print('  %-8s %-8s 期望 %s' % (name, got, expect))


if __name__ == '__main__':
    main()
