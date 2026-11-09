"""用模拟的“游戏窗口”验证画面识别链路（OCR 三选一、商店开关感知）。运行：py -3 tests/test_vision.py"""
import os, random, sys, tempfile, threading, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import winfx; winfx.set_dpi_aware()
import vision
from data_source import Data
import config

data = Data(config.CACHE_DIR); data.load(log=lambda *a: None)
names = ["逃跑计划", "升级：献祭", "魔法转物理"]
ids = [data.augment_names()[__import__("data_source").norm(n)] for n in names]
root = tk.Tk(); root.title("League of Legends (TM) Client"); root.overrideredirect(True)
W, H = 1280, 720
root.geometry("%dx%d+60+40" % (W, H)); root.attributes("-topmost", True)
cv = tk.Canvas(root, width=W, height=H, highlightthickness=0, bg="#223"); cv.pack()
rnd = random.Random()

SEED = [None]
def scene():
    global rnd
    rnd = random.Random(SEED[0])  # 校准时背景固定（人站着不动），检测时随机换背景（镜头移动）
    cv.delete("all")
    for _ in range(60):
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        cv.create_rectangle(x, y, x + rnd.randint(40, 200), y + rnd.randint(40, 160), fill="#%02x%02x%02x" % (rnd.randint(20, 120), rnd.randint(40, 140), rnd.randint(40, 160)), width=0)

def mode(m):
    scene()
    if m == "augment":
        cv.create_rectangle(0, 0, W, H, fill="#0b1018", stipple="gray50")
        for i, n in enumerate(names):
            cx = W * (0.25 + 0.25 * i)
            cv.create_rectangle(cx - 150, 200, cx + 150, 520, fill="#1a2433", outline="#c8aa6e", width=3)
            cv.create_text(cx, 290, text=n, fill="#f0e6d2", font=("Microsoft YaHei", 26, "bold"))
            cv.create_text(cx, 420, text="获得一些属性并在战斗中触发效果", fill="#a09b8c", font=("Microsoft YaHei", 12), width=240)
    if m == "options":  # Esc 设置菜单：深青色
        cv.create_rectangle(360, 140, 920, 580, fill="#0e1c22", outline="#c8aa6e", width=3)
    if m == "shop":
        cv.create_rectangle(243, 65, 998, 562, fill="#2c220e", outline="#c8aa6e", width=4)  # 实测商店底色  # 真实游戏里商店面板的位置
        for r in range(3):  # 实际商店里图标只占面板一小部分
            for c in range(8):
                cv.create_rectangle(290 + c * 80, 120 + r * 120, 326 + c * 80, 156 + r * 120, fill="#%02x%02x%02x" % (60 + r * 20, 50 + c * 12, 90), outline="#c8aa6e")
    root.update()

res = {}
def run():
    try:
        ocr = vision.OcrWorker(os.path.join(tempfile.gettempdir(), "aramtest")); t = time.time()
        res["ocr_ready"] = ocr.start(); res["ocr_start_s"] = round(time.time() - t, 1); res["lang"] = ocr.lang
        rect = winfx.game_rect(); res["rect"] = rect
        # ---- 海克斯三选一
        root.after(0, lambda: mode("augment")); time.sleep(0.8)
        t = time.time()
        lines, w, h = ocr.ocr_screen(rect, crop=(0.06, 0.10, 0.94, 0.90)); res["ocr_s"] = round(time.time() - t, 2)
        res["ocr_text"] = ["".join(x[0] for x in l) for l in lines][:12]; res["ocr_err"] = ocr.last_error
        got = vision.find_augments(data, lines, w, h)
        for g in got: g["cx"] = 0.06 + g["cx"] * 0.88
        res["augment_ok"] = [g["aid"] for g in got] == ids
        res["found"] = [(data.augment_name(g["aid"]), round(g["cx"], 2)) for g in got]
        root.after(0, lambda: mode("plain")); time.sleep(0.8)
        lines, w, h = ocr.ocr_screen(rect, crop=(0.06, 0.10, 0.94, 0.90))
        res["no_popup_ok"] = vision.find_augments(data, lines, w, h) == []
        # ---- 商店状态感知：只有商店打开时为 True；三选一、设置菜单、普通画面都应为 False
        sensor = vision.ShopSensor()
        seq = []
        for m, expect in (("plain", False), ("shop", True), ("augment", False), ("options", False), ("shop", True), ("plain", False)):
            root.after(0, lambda m=m: mode(m)); time.sleep(0.5)
            sc = sensor.score(rect)
            seq.append((m, sc >= sensor.OPEN, expect, round(sc, 2)))
        res["shop_seq"] = seq
        res["shop_ok"] = all(got == exp for _, got, exp, _ in seq)
        # ---- 商店位置测量：真实截屏 → 打开/关闭前后对比 + 底色法（面板画在 x 0.19~0.78）
        import layout
        SEED[0] = 7
        def frame(m):
            root.after(0, lambda: mode(m)); time.sleep(0.5)
            return vision.grab(rect, 128, 72).astype("float32")[:, :, 2::-1]
        f_open, f_closed = frame("shop"), frame("plain")
        res["extent_diff"] = layout.shop_extent_diff(f_open, f_closed)
        res["extent_color"] = layout.shop_extent_array(f_open[:, :, 0], f_open[:, :, 1], f_open[:, :, 2])
        d_ = res["extent_diff"]; c_ = res["extent_color"]
        res["extent_ok"] = bool(c_ and c_[1] and abs(c_[1] - 0.78) < 0.05)   # 前后对比的结果只作参考（面板和背景颜色接近时会量小，所以面板只用它来“扩大”避让）
        import numpy as np
        t96 = np.load(os.path.join(os.path.dirname(__file__), "fixtures", "shop_open_2560_96x54.npy"))
        sc = sensor.score_array(t96[:, :, 0], t96[:, :, 1], t96[:, :, 2], sensor.regions((0, 0, 2560, 1440)))
        res["real_shop_frame"] = (sc >= sensor.OPEN, round(sc, 2))
        t = time.time()
        for _ in range(20): vision.grab(rect, 96, 54)
        res["grab_ms"] = round((time.time() - t) / 20 * 1000, 1)
        ocr.stop()
    except Exception as e:
        import traceback; res["error"] = traceback.format_exc()
    root.after(0, root.destroy)

threading.Thread(target=run, daemon=True).start()
root.mainloop()
for k, v in res.items(): print(k, "=", v)
