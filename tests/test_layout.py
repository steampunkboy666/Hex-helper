"""布局计算测试（多显示器/缩放坐标换算，商店面板和三选一卡片边缘测量）。运行：py -3 tests/test_layout.py"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import layout

ok = True


def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, info)


# ---- 多显示器：左边 1920x1080@100%，右边 2560x1440@125%（物理原点 1920,0）
S = [{"x": 0, "y": 0, "w": 1920, "h": 1080, "dpr": 1.0, "avail": (0, 0, 1920, 1040)},
     {"x": 1920, "y": 0, "w": 2048, "h": 1152, "dpr": 1.25, "avail": (1920, 0, 2048, 1112)}]
r1 = (1920, 0, 2560, 1440)       # 游戏在第二块屏幕上（物理像素）
s = layout.pick_screen(r1, S)
check("游戏在第二块屏幕", s is S[1])
lg = layout.to_logical(r1, s)
check("逻辑坐标换算", lg == (1920.0, 0.0, 2048.0, 1152.0), lg)
back = layout.to_native(lg, s)
check("换算可逆", all(abs(a - b) < 1e-6 for a, b in zip(back, r1)), back)
check("游戏在主屏", layout.pick_screen((0, 0, 1920, 1080), S) is S[0])
check("找不到屏幕时用主屏", layout.pick_screen((9000, 9000, 100, 100), S) is S[0])

# ---- 商店面板边缘（单张图的颜色法）：用实机 2560x1440 商店打开时的画面（96x54 缩小）
t = np.load(os.path.join(os.path.dirname(__file__), "fixtures", "shop_open_2560_96x54.npy"))
ext = layout.shop_extent_array(t[:, :, 0], t[:, :, 1], t[:, :, 2])
check("实机：右边缘可信（真实约 0.78）", ext and ext[1] and 0.70 < ext[1] < 0.82, ext)
check("实机：左侧地面也是暗棕色 → 左边缘标为不可信", ext and ext[0] is None, ext)
rng = np.random.default_rng(1)
plain = rng.integers(60, 230, (54, 96, 3)).astype(float)
check("普通画面不是商店", layout.shop_extent_array(plain[:, :, 0], plain[:, :, 1], plain[:, :, 2]) is None)
blue = np.zeros((54, 96, 3)); blue[:] = (110, 120, 160)
check("蓝色地图不是商店", layout.shop_extent_array(blue[:, :, 0], blue[:, :, 1], blue[:, :, 2]) is None)

# ---- 商店面板边缘（打开前后对比）：把商店放在不同位置/大小，背景是和面板同色的暗地面也能量准
def scene(seed, dark=False):
    g = np.random.default_rng(seed)
    base = g.integers(30, 70, (72, 128, 3)).astype(float) if dark else g.integers(40, 240, (72, 128, 3)).astype(float)
    return base

def with_shop(img, x0, x1):
    out = img.copy()
    out[int(.09 * 72):int(.78 * 72), int(x0 * 128):int(x1 * 128)] = (44, 34, 14)
    return out

for name, (x0, x1), dark in (("居中", (0.19, 0.78), False), ("被拖到右边", (0.35, 0.94), False), ("较小的商店", (0.28, 0.70), False),
                             ("暗地面", (0.19, 0.78), True)):
    b4 = scene(3, dark)
    e = layout.shop_extent_diff(b4, with_shop(b4, x0, x1))
    if dark:  # 面板和地面几乎同色时，对比看不出来是正常的：只要求“不给出错误的数”
        check("前后对比：" + name + "（看不出来就返回 None，不能给错）", e is None or (abs(e[0] - x0) < 0.03 and abs(e[1] - x1) < 0.03), e)
    else:
        check("前后对比：" + name, e and abs(e[0] - x0) < 0.03 and abs(e[1] - x1) < 0.03, e)
b4 = scene(3)
check("镜头同时移动 → 不采信", layout.shop_extent_diff(b4, scene(4)) is None)
check("什么都没变 → 没有商店", layout.shop_extent_diff(b4, b4) is None)

# ---- 三选一卡片边缘：实机卡片中心 0.28/0.50/0.72，外框实测 0.188~0.813
ce = layout.cards_extent([{"cx": 0.28}, {"cx": 0.5}, {"cx": 0.72}])
check("卡片外框边缘", abs(ce[0] - 0.183) < 0.01 and abs(ce[1] - 0.817) < 0.01, ce)
check("只认出一张时不猜", layout.cards_extent([{"cx": 0.5}]) is None)
print("ALL OK" if ok else "SOME FAILED")
