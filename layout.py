"""屏幕/界面布局的纯计算（不依赖 Qt 和 Windows，便于测试）：多显示器坐标换算、
从实际画面里量出商店面板/三选一卡片的左右边缘。让面板能适应不同机器上不同的分辨率、显示缩放、
多显示器、商店面板位置和界面缩放，而不是写死“商店在画面中央这么宽”。"""
import numpy as np


# ------------------------------------------------------------ 多显示器 / 显示缩放
# screens: [{"x","y","w","h","dpr","avail":(ax,ay,aw,ah)}]，与 Qt 一致：x,y 是物理像素的屏幕原点，
# w,h 是逻辑尺寸（物理尺寸 = w*dpr），avail 是逻辑坐标下的可用区域（去掉任务栏）。
def pick_screen(rect, screens):
    """物理像素矩形 (l,t,w,h) 的中心落在哪块屏幕上；都不在就用第一块（主屏）。"""
    cx, cy = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
    for s in screens:
        if s["x"] <= cx < s["x"] + s["w"] * s["dpr"] and s["y"] <= cy < s["y"] + s["h"] * s["dpr"]:
            return s
    return screens[0]


def to_logical(rect, s):
    """物理像素矩形 → 该屏幕的 Qt 逻辑坐标 (l,t,w,h)。"""
    d = s["dpr"]
    return (s["x"] + (rect[0] - s["x"]) / d, s["y"] + (rect[1] - s["y"]) / d, rect[2] / d, rect[3] / d)


def to_native(rect, s):
    """Qt 逻辑坐标矩形 → 物理像素（用来涂黑面板区域）。"""
    d = s["dpr"]
    return (s["x"] + (rect[0] - s["x"]) * d, s["y"] + (rect[1] - s["y"]) * d, rect[2] * d, rect[3] * d)


# ------------------------------------------------------------ 量出商店面板的左右边缘
def _run(on, c, gap):
    """从第 c 列向两边找连续为真的列（允许 gap 列的小间隙），返回 (左端, 右端)。"""
    W = len(on)
    lo = hi = c
    miss = 0
    for x in range(c, -1, -1):
        if on[x]:
            lo, miss = x, 0
        else:
            miss += 1
            if miss > gap:
                break
    miss = 0
    for x in range(c, W):
        if on[x]:
            hi, miss = x, 0
        else:
            miss += 1
            if miss > gap:
                break
    return lo, hi


def _center_col(on):
    W = len(on)
    seg = on[max(0, W // 2 - W // 12):W // 2 + W // 12]
    return None if not seg.any() else max(0, W // 2 - W // 12) + int(np.argmax(seg))


def shop_extent_diff(before, after, top=0.16, bottom=0.68):
    """打开商店前后的两张图（行×列×RGB）：面板出现的地方整列大面积变化。
    地图/地面颜色和面板一样暗时也能量准边缘；镜头同时在动（面板两侧也大面积变化）时返回 None。
    返回 (左边缘, 右边缘) 占画面宽度的比例。"""
    d = np.abs(after.astype(float) - before.astype(float)).mean(2)
    H, W = d.shape
    col = (d[int(top * H):int(bottom * H)] > 22).mean(0)
    on = col > 0.5
    c = _center_col(on)
    if c is None:
        return None
    lo, hi = _run(on, c, max(2, W // 40))
    if (hi - lo + 1) / W < 0.22 or (hi - lo + 1) / W > 0.92:   # 太窄 / 整个画面都变了（镜头在动）
        return None
    m = max(2, W // 16)
    outside = np.concatenate([col[:max(0, lo - m)], col[min(W, hi + 1 + m):]])
    if outside.size and outside.mean() > 0.3:
        return None
    return lo / W, (hi + 1) / W


def shop_extent_array(r, g, b, top=0.16, bottom=0.68):
    """单张图估计：商店面板的底色是很暗的棕褐色（实测约 90% 的格子符合）。逐列统计这种颜色的占比。
    面板旁边的地面如果也是暗棕色，这一侧会一直连到画面边缘——这种一侧返回 None（不可信）。
    返回 (左边缘或 None, 右边缘或 None)；整体没找到商店返回 None。"""
    H, W = r.shape
    lum = (r + g + b) / 3
    like = (lum > 8) & (lum < 70) & (b < 0.75 * r + 6) & (g <= r + 8)
    on = like[int(top * H):int(bottom * H)].mean(0) > 0.35
    c = _center_col(on)
    if c is None:
        return None
    lo, hi = _run(on, c, max(2, W // 40))
    if (hi - lo + 1) / W < 0.22:
        return None
    return (None if lo == 0 else lo / W), (None if hi == W - 1 else (hi + 1) / W)


def widen(ext, w_frac=0.012):
    """底色区域比面板外框略小（外框有金边/标签栏），向外补一点。"""
    if not ext:
        return None
    return (None if ext[0] is None else max(0.0, ext[0] - w_frac),
            None if ext[1] is None else min(1.0, ext[1] + w_frac))


# ------------------------------------------------------------ 三选一卡片的左右边缘
def cards_extent(hits):
    """hits：识别到的卡片标题位置 [{cx}]（占画面宽度的比例）。卡片间距 d 已知，
    实测卡片外框半宽约 0.44 倍间距。至少两张才能算出间距。"""
    xs = sorted(h["cx"] for h in hits)
    if len(xs) < 2:
        return None
    d = (xs[-1] - xs[0]) / (len(xs) - 1)
    if d < 0.05:
        return None
    return xs[0] - 0.44 * d, xs[-1] + 0.44 * d
