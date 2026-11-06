"""用实机截到的海克斯三选一画面（tests/fixtures/real_offer_2560.jpg）验证 OCR 识别链路。
把截图放进一个模拟的“游戏窗口”，按程序里完全相同的方式（横条裁剪 → OCR → 匹配名称）识别。
运行：py -3 tests/test_real_offer.py"""
import os, sys, tempfile, threading, time, tkinter as tk
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import winfx; winfx.set_dpi_aware()
from PIL import Image, ImageTk
import vision, config
from app import Helper
from data_source import Data

EXPECT = ["鼓声暴起 低音轰鸣", "吸元秘术", "杀戮时间到了"]
data = Data(config.CACHE_DIR); data.load(log=lambda *a: None)
src = Image.open(os.path.join(os.path.dirname(__file__), "fixtures", "real_offer_2560.jpg"))
root = tk.Tk(); root.title("League of Legends (TM) Client"); root.overrideredirect(True); root.attributes("-topmost", True)
lbl = tk.Label(root, bd=0); lbl.pack()
res = {}


def show(w, h):
    """把 16:9 的实机截图放进 w×h 的窗口：界面按高度缩放，宽出来的部分（超宽屏）两侧补背景。"""
    sw = round(h * 16 / 9)
    canvas = Image.new("RGB", (w, h), (30, 40, 60))
    canvas.paste(src.resize((sw, h), Image.LANCZOS), ((w - sw) // 2, 0))
    img = ImageTk.PhotoImage(canvas)
    lbl.configure(image=img); lbl.image = img
    root.geometry("%dx%d+0+0" % (w, h)); root.update()


def run():
    ocr = vision.OcrWorker(os.path.join(tempfile.gettempdir(), "文件", "海斗", "aramtest_real"))  # 含中文的路径（用户实际情况）
    res["ocr_ready"] = ocr.start()
    # 本机屏幕放不下 2560，用同比例或更小的尺寸（字更小，更难）；1920x822 模拟 21:9 超宽屏
    for w, h in ((1920, 1080), (1600, 900), (1366, 768), (1280, 720), (1920, 822)):
        root.after(0, lambda w=w, h=h: show(w, h)); time.sleep(1.0)
        rect = winfx.game_rect()
        crop, mw, mh = Helper.aug_crops(rect)[0]
        t = time.time()
        lines, iw, ih = ocr.ocr_screen(rect, max_w=mw, crop=crop, min_h=mh)
        hits = vision.find_augments(data, lines, iw, ih)
        names = [data.augment_name(x["aid"]) for x in hits]
        res["%dx%d" % (w, h)] = (names == EXPECT, names, round(time.time() - t, 2))
    # 连续快速识别 40 次（游戏里刚弹出时就是这样密集），文件名轮换复用：以前第 5 次就因文件被占用而崩溃
    ok, t = 0, time.time()
    for i in range(40):
        for crop, mw, mh in Helper.aug_crops(rect)[:2]:
            lines, iw, ih = ocr.ocr_screen(rect, max_w=mw, crop=crop, min_h=mh)
        hits = vision.find_augments(data, *ocr.ocr_screen(rect, max_w=mw, crop=Helper.aug_crops(rect)[0][0])[:3])
        ok += [data.augment_name(x["aid"]) for x in hits] == EXPECT
    res["stress_40x3"] = (ok == 40, ok, "平均每轮 %.2f 秒" % ((time.time() - t) / 40), ocr.last_error)
    ocr.stop()
    root.after(0, root.destroy)


threading.Thread(target=run, daemon=True).start()
root.mainloop()
for k, v in res.items():
    print(k, "=", v)

# 实机横带截图：第三张“台风”是两个字的短标题，Windows OCR 整条识别时会漏掉 → 需要补认
import shutil
ocr2 = vision.OcrWorker(os.path.join(tempfile.gettempdir(), "文件", "海斗", "aramtest_band"))
ocr2.start()
p = os.path.join(ocr2.dir, "band.png")
shutil.copy(os.path.join(os.path.dirname(__file__), "fixtures", "real_band_taifeng.png"), p)
L = ocr2.recognize(p)
h = vision.recover_missing(ocr2, data, p, vision.find_augments(data, L, 2048, 367), 2048, 367)
names = [data.augment_name(x["aid"]) for x in h]
print("短标题补认 =", (names == ["暗影疾奔", "山脉龙魂", "台风"], names))
ocr2.stop()
