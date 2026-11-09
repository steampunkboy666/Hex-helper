"""海克斯三选一的“套路说明”：每张强化写明适合什么套路、和已选强化怎么联动、和英雄出装是否冲突、配套出装。

用本地缓存的真实数据（不联网），挑几个英雄、按真实统计选出风格不同的三张强化当作三选一，
  1. 检查每个选项都生成了说明，出装不含鞋子，物理英雄拿到纯法术强化会提示冲突；
  2. 把同一组三选一分别按“改前 / 改后”渲染成图片（tests/out/aug_notes_*.png）方便对比。
用法：py -3 tests/test_aug_notes.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # offscreen 没有中文字体

import config  # noqa: E402
import advisor  # noqa: E402
from data_source import Data  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QPixmap, QColor, QRegion  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, ok, info=""):
    results.append((name, ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, info))


d = Data(config.CACHE_DIR)
assert d.load_cached(), "需要先运行过一次助手（有本地缓存）"


def pick_offer(hid, rarity_key, want):
    """从该英雄某稀有度的强化里，按标签各挑一张（胜率高的优先），凑成三选一。
    该稀有度里没有就去其他稀有度找；纯法术那张要求“只加法强、不加物理属性”。"""
    keys = [rarity_key] + [k for k in ("silver", "gold", "prismatic") if k != rarity_key]
    out = []
    for tagset in want:
        pure_ap = tagset == {"法强"}
        for k in keys:
            hit = None
            for e in advisor._hero_list(d, hid, k):
                a = d.augment(e["augmentId"])
                aid = str(e["augmentId"])
                if not a or aid in out:
                    continue
                t = advisor.aug_tags(a)
                if t & tagset and (not pure_ap or not t & advisor.AD_TAGS):
                    hit = aid
                    break
            if hit:
                out.append(hit)
                break
        else:   # 该英雄的统计里一张都没有：从全部强化里找（三选一也可能刷出这个英雄很少选的强化）
            for aid, a in d.meta["augments"].items():
                t = advisor.aug_tags(a)
                if aid not in out and t & tagset and (not pure_ap or not t & advisor.AD_TAGS):
                    out.append(aid)
                    break
    return out


def hero_with(tag):
    """缓存里主流出装带某标签（AD/AP/Tank）的英雄。"""
    for hid in d.meta["summary"]:
        det = d.ensure_detail(hid, offline=True)
        gs = (det or {}).get("itemBuilds") or []
        if gs and tag in max(gs, key=lambda g: g.get("pickRate") or 0).get("tags", []) and det.get("trios"):
            return hid
    return None


SCENES = []
ad = hero_with("Crit")
ap = hero_with("AP")
if ad:
    offer = pick_offer(ad, "gold", [{"暴击"}, {"法强"}, {"肉盾"}])
    SCENES.append(("物理英雄_第一次三选一", ad, offer, []))
    chosen = pick_offer(ad, "silver", [{"攻速", "暴击"}])
    offer2 = pick_offer(ad, "prismatic", [{"暴击", "攻速"}, {"法强"}, {"急速"}])
    SCENES.append(("物理英雄_已选一张后", ad, offer2, chosen))
if ap:
    offer = pick_offer(ap, "gold", [{"法强", "法伤"}, {"急速"}, {"暴击", "攻击力"}])
    SCENES.append(("法师英雄_第一次三选一", ap, offer, []))

check("找到测试英雄", bool(ad and ap), "物理=%s 法术=%s" % (d.hero(ad)["title"] if ad else None,
                                                     d.hero(ap)["title"] if ap else None))

# ---- 1. 逻辑检查
for name, hid, offer, chosen in SCENES:
    ev = advisor.evaluate_offer(d, hid, offer, chosen)
    ok = len(ev["options"]) == len(offer) and all("note" in o for o in ev["options"])
    check("%s：每个选项都有说明" % name, ok, " | ".join(o["name"] for o in ev["options"]))
    for o in ev["options"]:
        n = o["note"]
        b = n["build"]
        print("    %s  套路=%s  联动=%s  冲突=%s  出装=%s" % (
            o["name"], n["styles"], n["links"], n["warn"],
            ("%s %s" % (b["label"], " → ".join(b["items"]))) if b else None))
        if b:
            names_boots = [i for i in b["items"] if "鞋" in i or "靴" in i]
            check("    %s 的出装不含鞋子" % o["name"], not names_boots, str(names_boots))
    if name.startswith("物理") and not chosen:
        check("%s：出现了纯法术强化（用来测冲突提示）" % name,
              any(o["tags"] & advisor.AP_TAGS and not o["tags"] & advisor.AD_TAGS for o in ev["options"]))
    if name.startswith("物理"):
        ap_opts = [o for o in ev["options"] if o["tags"] & advisor.AP_TAGS and not o["tags"] & advisor.AD_TAGS]
        if ap_opts:
            check("%s：纯法术强化提示和出装冲突" % name, all(o["note"]["warn"] for o in ap_opts),
                  ap_opts[0]["note"]["warn"] or "")
    if chosen:
        linked = [o for o in ev["options"] if o["tags"] & ev["chosen"][0]["tags"]]
        check("%s：与已选同类的强化写出联动" % name,
              all(any("配合已选" in s for s in o["note"]["links"]) for o in linked) and bool(linked),
              linked[0]["note"]["links"][0] if linked and linked[0]["note"]["links"] else "")

# ---- 2. 渲染改前/改后
app = QApplication.instance() or QApplication([])
import ui  # noqa: E402

settings = config.Settings()
ctx = ui.Ctx(d, ui.ImageLoader(config.CACHE_DIR), settings)
panel = ui.SidePanel(ctx)
panel._present = lambda: None          # 不弹悬浮窗，只取排好版的内容
RECT = (0, 0, 1920, 1080)


def render(hid, ev, notes, path):
    settings.v["aug_notes"] = notes
    panel.show_offer(hid, ev, RECT)
    for _ in range(30):                # 等图标加载
        app.processEvents()
    panel.show_offer(hid, ev, RECT)
    fr = panel.frame
    fr.layout().activate()
    fr.resize(fr.width(), fr.layout().sizeHint().height())
    pm = QPixmap(fr.size())
    pm.fill(QColor(8, 16, 24))
    fr.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    pm.save(path)
    return pm.width(), pm.height()


import time  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
for name, hid, offer, chosen in SCENES:
    ev = advisor.evaluate_offer(d, hid, offer, chosen)
    ims = []
    for notes, tag in ((False, "改前"), (True, "改后")):
        p = os.path.join(OUT, "aug_notes_%s_%s.png" % (name, tag))
        render(hid, ev, notes, p)
        time.sleep(0.3)
        for _ in range(20):
            app.processEvents()
        render(hid, ev, notes, p)
        ims.append((tag, Image.open(p).convert("RGB")))
    pad, top = 16, 44
    W = sum(i.width for _, i in ims) + pad * 3
    H = max(i.height for _, i in ims) + top + pad
    out = Image.new("RGB", (W, H), (20, 28, 36))
    dr = ImageDraw.Draw(out)
    x = pad
    for tag, im in ims:
        dr.text((x, 10), "%s · %s" % (tag, name.replace("_", " ")), font=font, fill=(230, 192, 104))
        out.paste(im, (x, top))
        x += im.width + pad
    p = os.path.join(OUT, "aug_notes_对比_%s.png" % name)
    out.save(p)
    print("图片:", p, out.size)

settings.v["aug_notes"] = True
bad = [n for n, ok in results if not ok]
print("\n%d 项通过，%d 项失败" % (len(results) - len(bad), len(bad)))
sys.exit(1 if bad else 0)
