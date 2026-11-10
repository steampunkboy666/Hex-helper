"""v1.3 新功能：导入出装到客户端、赛后战报卡、精简模式与分项开关。

全部离线，用本地缓存的真实英雄/装备数据：
  1. 导入出装：生成的推荐页内容（不含鞋子）；写入时保留玩家自己的推荐页、不重复堆积本程序的页；
  2. 赛后战报：用一份按客户端赛后统计格式构造的数据（tests/fixtures/eog_sample.json）算大腿分、MVP，并渲染战报图；
  3. 精简模式：商店/三选一面板比标准模式短，关键信息还在；
  4. 分项开关：关掉某个面板后对应场景不再显示，其他面板不受影响；
  5. 控制台“显示与功能”区域截图。
图片输出到 tests/out/。用法：py -3 tests/test_new_features.py
"""
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # offscreen 没有中文字体

import config  # noqa: E402
import advisor  # noqa: E402
import itemsets  # noqa: E402
import report  # noqa: E402
from data_source import Data  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
FIX = os.path.join(ROOT, "tests", "fixtures")
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, ok, info=""):
    results.append((name, ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, info))


d = Data(config.CACHE_DIR)
assert d.load_cached(), "需要先运行过一次助手（有本地缓存）"
HERO = d.hero_by_alias("Yone") or next(h for h in d.meta["summary"] if d.ensure_detail(h, offline=True))
d.ensure_detail(HERO, offline=True)

# ---------------------------------------------------------------- 1. 导入出装
print("== 导入出装")
page = itemsets.build_item_set(d, HERO)
check("生成了推荐页", bool(page and page["blocks"]), page and page["title"])
ids = [i["id"] for b in page["blocks"] for i in b["items"]]
check("鞋子只出现在“鞋子”一栏", not any(d.is_boots(i["id"]) for bl in page["blocks"] if bl["type"] != "鞋子"
                                     for i in bl["items"]), "%d 件" % len(ids))
sets_ = [b for b in page["blocks"] if b["type"].startswith("三件套")]
check("每栏里不重复", all(len({i["id"] for i in b["items"]}) == len(b["items"]) for b in page["blocks"]))
check("和出装面板一样有三套完整三件套", len(sets_) == 3 and all(len(b["items"]) == 3 for b in sets_),
      " / ".join(b["type"] for b in sets_))
check("三件套和面板里的三套一致", [[i["id"] for i in b["items"]] for b in sets_] ==
      [[str(i.get("itemId") or i.get("id")) for i in b["items"]] for b in d.builds(HERO, 3)])
other_ids = [i["id"] for b in page["blocks"] if not b["type"].startswith("三件套") for i in b["items"]]
check("高胜率装备/鞋子不和三件套重复", not set(other_ids) & {i["id"] for b in sets_ for i in b["items"]})
check("推荐页至少 10 件", len(ids) >= 10, "%d 件" % len(ids))
check("每栏标题都很短（商店推荐栏窄）", all(len(bl["type"]) <= 8 for bl in page["blocks"]),
      " / ".join(bl["type"] for bl in page["blocks"]))
# 单件统计还没拿到时（上次实测“3 组 6 件”就是这种情况）也要有完整的一页
saved = d.detail_cached(HERO)["itemStats"]
d.detail_cached(HERO)["itemStats"] = None
thin = itemsets.build_item_set(d, HERO)
d.detail_cached(HERO)["itemStats"] = saved
tids = [i["id"] for b in thin["blocks"] for i in b["items"]]
check("单件统计缺失时推荐页仍完整", len(thin["blocks"][0]["items"]) == 3,
      " / ".join("%s %d 件" % (b["type"], len(b["items"])) for b in thin["blocks"]))
check("推荐页绑定这个英雄", page["associatedChampions"] == [int(HERO)])
for b in page["blocks"]:
    print("    %s：%s" % (b["type"], " / ".join((d.item(i["id"]) or {}).get("name", i["id"]) for i in b["items"])))


class FakeLCU:
    """记录写入内容的假客户端：里面已有一页玩家自己的推荐页。"""

    def __init__(self):
        self.sets = {"accountId": 42, "timestamp": 0, "itemSets": [
            {"uid": "my-own-page", "title": "我自己的出装", "blocks": [], "associatedChampions": [int(HERO)]}]}
        self.puts = 0

    def get(self, path):
        if path.endswith("current-summoner"):
            return {"summonerId": 7, "accountId": 42}
        if path == "/lol-item-sets/v1/item-sets/7/sets":
            return self.sets
        return None

    def put(self, path, body):
        assert path == "/lol-item-sets/v1/item-sets/7/sets", path
        self.sets, self.puts = json.loads(json.dumps(body)), self.puts + 1


lcu = FakeLCU()
ok, msg = itemsets.import_item_set(lcu, d, HERO)
check("写入客户端成功", ok and lcu.puts == 1, msg)
other = d.hero_by_alias("Lillia") or HERO
d.ensure_detail(other, offline=True)
itemsets.import_item_set(lcu, d, other)        # 换英雄再写一次
uids = [s["uid"] for s in lcu.sets["itemSets"]]
check("玩家自己的推荐页保留", "my-own-page" in uids, str(uids))
check("本程序的推荐页只保留最新一页（不会越堆越多）",
      sum(u.startswith(itemsets.UID_PREFIX) for u in uids) == 1 and ("hexhelper-%s" % other) in uids)
check("写入的 accountId 正确", lcu.sets["accountId"] == 42)

# ---------------------------------------------------------------- 2. 赛后战报
print("== 赛后战报")
block = json.load(open(os.path.join(FIX, "eog_sample.json"), encoding="utf-8"))
rep = report.parse_eog(block, d)
check("解析出两队各 5 人", rep and [len(t["players"]) for t in rep["teams"]] == [5, 5])
check("我方排在前面且识别出我", rep["teams"][0]["mine"] and rep["me"] and rep["me"]["local"],
      rep["me"]["name"] if rep and rep["me"] else "")
win_team = next(t for t in rep["teams"] if t["win"])
lose_team = next(t for t in rep["teams"] if not t["win"])
check("胜方最高分是 MVP", win_team["players"][0].get("badge") == "MVP", win_team["players"][0]["name"])
check("败方最高分是 虽败犹荣", lose_team["players"][0].get("badge") == "虽败犹荣", lose_team["players"][0]["name"])
scores = [r["score"] for t in rep["teams"] for r in t["players"]]
check("大腿分都在 0~100", all(0 <= x <= 100 for x in scores), "%.0f ~ %.0f" % (min(scores), max(scores)))
check("输出最高的人有“输出最高”称号",
      "输出最高" in max((r for t in rep["teams"] for r in t["players"]), key=lambda r: r["dmg"]).get("titles", []))
check("一句话总结", bool(report.verdict(rep)), report.verdict(rep))
check("空数据不报错", report.parse_eog(None, d) is None and report.parse_eog({"teams": []}, d) is None)
for t in rep["teams"]:
    for r in t["players"]:
        print("    %s %-8s %-10s %d/%d/%d 输出%6d 分%5.1f %s" % (
            "我" if r["local"] else "  ", (d.hero(r["cid"]) or {}).get("title", "?"), r["name"], r["k"], r["d"],
            r["a"], r["dmg"], r["score"], " ".join(([r["badge"]] if r.get("badge") else []) + r.get("titles", []))))

# ---------------------------------------------------------------- 界面
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
from PySide6.QtGui import QColor, QPixmap, QRegion  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
app_qt = QApplication.instance() or QApplication([])
import ui  # noqa: E402
import app as appmod  # noqa: E402

settings = config.Settings()
settings.save = lambda: None                   # 测试里改设置不写盘
ctx = ui.Ctx(d, ui.ImageLoader(config.CACHE_DIR), settings)


def pump(n=30):
    for _ in range(n):
        app_qt.processEvents()


def grab(widget, path):
    widget.layout().activate()
    widget.resize(widget.width(), widget.layout().sizeHint().height())
    pm = QPixmap(widget.size())
    pm.fill(QColor(8, 16, 24))
    widget.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    pm.save(path)
    return pm.height()


win = ui.ReportWindow(ctx, rep, os.path.join(OUT, "战报_测试.png"))
win.show()
pump()
import time  # noqa: E402
time.sleep(1.0)
pump()
check("战报图保存成功", win.save() and os.path.exists(win.path), win.path)
win.close()

# ---------------------------------------------------------------- 3. 精简模式
print("== 精简模式")
panel = ui.SidePanel(ctx)
panel._present = lambda: None
RECT = (0, 0, 1920, 1080)
lst = advisor._hero_list(d, HERO, "gold")[:3]
offer = [str(e["augmentId"]) for e in lst]
ev = advisor.evaluate_offer(d, HERO, offer, [])
own = [advisor.top_items_model(d, HERO, [], 0)[0]["itemId"]]
hs = {}
for preset in ("standard", "lite"):
    settings.v["preset"] = preset
    for kind in ("offer", "shop"):
        for _ in range(2):
            panel._fit = 1.0
            if kind == "offer":
                panel.show_offer(HERO, ev, RECT)
            else:
                panel.show_shop(HERO, own, 2500, RECT)
            pump()
        hs[(kind, preset)] = grab(panel.frame, os.path.join(OUT, "精简_%s_%s.png" % (kind, preset)))
for kind in ("offer", "shop"):
    check("%s 面板精简后更短" % {"offer": "三选一", "shop": "商店"}[kind],
          hs[(kind, "lite")] < hs[(kind, "standard")] * 0.8,
          "标准 %d px → 精简 %d px" % (hs[(kind, "standard")], hs[(kind, "lite")]))
settings.v["preset"] = "standard"

# 拼对比图
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
cols = [("三选一 · 标准", "精简_offer_standard.png"), ("三选一 · 精简", "精简_offer_lite.png"),
        ("商店 · 标准", "精简_shop_standard.png"), ("商店 · 精简", "精简_shop_lite.png")]
ims = [(t, Image.open(os.path.join(OUT, f)).convert("RGB")) for t, f in cols]
pad, top = 16, 44
out = Image.new("RGB", (sum(i.width for _, i in ims) + pad * 5, max(i.height for _, i in ims) + top + pad), (20, 28, 36))
dr = ImageDraw.Draw(out)
x = pad
for t, im in ims:
    dr.text((x, 10), t, font=font, fill=(230, 192, 104))
    out.paste(im, (x, top))
    x += im.width + pad
out.save(os.path.join(OUT, "精简模式_对比.png"))

# ---------------------------------------------------------------- 4. 分项开关
print("== 分项开关")


class Stub:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append(name)
        return f


def fake_helper(**kw):
    side, cs = Stub(), Stub()
    cs.dismissed, cs.isVisible = False, (lambda: "show" in cs.calls and cs.calls[-1] == "show")
    h = types.SimpleNamespace(args=types.SimpleNamespace(demo=True), phase="InProgress", cards=[], cs=cs,
                              hidden_all=False, live={"items": [], "gold": 1000}, _fg=True, hero_id=HERO,
                              offer=None, offer_hidden=False, overview_manual=False, chosen=[], _sig=None,
                              side=side, settings=settings, data=d, shop_state=False)
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def run(**kw):
    h = fake_helper(**kw)
    appmod.Helper.refresh(h)
    return h


offer_hits = [{"aid": a} for a in offer]
for key, scene, method in (("show_offer_panel", dict(offer=offer_hits), "show_offer"),
                           ("show_shop_panel", dict(shop_state=True), "show_shop")):
    settings.v[key] = True
    on = run(**scene).side.calls
    settings.v[key] = False
    off = run(**scene).side.calls
    settings.v[key] = True
    check("%s：打开时显示，关掉后不显示" % key, method in on and method not in off and "dismiss" in off,
          "开=%s 关=%s" % (on, off))
settings.v["show_offer_panel"] = False
both = run(offer=offer_hits, overview_manual=True).side.calls
check("关掉三选一面板后，手动按强化速查键仍能看", "show_overview" in both or "show_offer" in both, str(both))
settings.v["show_offer_panel"] = True
settings.v["show_champ_select"] = False
cs_calls = run(phase="ChampSelect", cards=[("1", "mine")], live=None).cs.calls
settings.v["show_champ_select"] = True
cs_on = run(phase="ChampSelect", cards=[("1", "mine")], live=None).cs.calls
check("show_champ_select：关掉后选人卡片不弹出", "show" not in cs_calls and "show" in cs_on, "关=%s 开=%s" % (cs_calls, cs_on))

# ---------------------------------------------------------------- 5. 控制台截图
cw = ui.ControlWindow(settings, settings["hotkeys"])
cw.resize(560, 900)
cw.show()
pump()
changed = []
cw.setting_changed.connect(lambda k, v: changed.append((k, v)))
from PySide6.QtWidgets import QCheckBox, QComboBox  # noqa: E402
cb = [c for c in cw.findChildren(QCheckBox) if "导入出装" in c.text()][0]
check("导入出装默认开启", cb.isChecked() and config.DEFAULTS["import_item_set"] is True)
rb = [c for c in cw.findChildren(QCheckBox) if "战报" in c.text()][0]
check("赛后战报是可选项、默认开启", rb.isChecked() and config.DEFAULTS["post_game_report"] is True, rb.text())
cb.setChecked(False)
rb.setChecked(False)
cw.findChildren(QComboBox)[0].setCurrentIndex(1)
check("取消勾选/切换会发出设置变更", ("import_item_set", False) in changed and ("post_game_report", False) in changed
      and ("preset", "lite") in changed, str(changed))
cb.setChecked(True)
rb.setChecked(True)
cw.findChildren(QComboBox)[0].setCurrentIndex(0)
pump()
cw.grab().save(os.path.join(OUT, "控制台_显示与功能.png"))
cw.hide()

# ---------------------------------------------------------------- 6. 三选一“收起”后再展开
print("== 三选一收起/展开")
logs = []
aids = [a for a in list(d.meta["augments"])[:3]] if isinstance(d.meta.get("augments"), dict) else ["1", "2", "3"]
offer = [{"aid": a, "cx": x, "cy": 0.4} for a, x in zip(aids, (0.3, 0.5, 0.7))]
st = types.SimpleNamespace(offer=None, chosen=[], _gone=None, offer_hidden=True, offer_end=0, _offer_gen=0,
                           settings={"offer_panel_secs": 0}, data=d, log=logs.append, refresh=lambda: None)
st.record_choice = lambda: (st.chosen.append(aids[0]), aids[0])[1]   # 收起时鼠标点在卡片区域：被误记为选择
appmod.Helper.on_offer(st, offer)
appmod.Helper.on_offer(st, None)                 # 卡片消失（玩家点了“收起”）
check("收起时先记下了一次选择", st.chosen == [aids[0]])
appmod.Helper.on_offer(st, offer)                # 同一组卡片又出现
check("同一组卡片重新出现：撤销误记的选择", st.chosen == [], str(logs[-1:]))
check("同一组卡片重新出现：推荐面板重新显示", st.offer and not st.offer_hidden)
appmod.Helper.on_offer(st, None)                 # 这次真的选了
appmod.Helper.on_offer(st, [dict(o, aid=o["aid"] + "x") for o in offer])   # 下一次三选一是新的一组
check("真正选择后出现新的一组：保留选择记录", st.chosen == [aids[0]])

print("== 判断为“收起”后出现完全不同的一组：补记选择（实机 0:30 那次）")
st2 = types.SimpleNamespace(offer=None, chosen=[], _gone=None, offer_hidden=True, offer_end=0, _offer_gen=0,
                            settings={"offer_panel_secs": 0}, data=d, log=logs.append, refresh=lambda: None)


def hide_but_clicked_card():
    st2._maybe = aids[1]          # 最后点过的卡片是中间那张，但最后一次点击落在“收起”按钮区域
    return None


st2.record_choice = hide_but_clicked_card
appmod.Helper.on_offer(st2, offer)
appmod.Helper.on_offer(st2, None)
appmod.Helper.on_offer(st2, offer)                # 同一组：真的只是收起，不补记
check("收起后同一组重新出现：不补记", st2.chosen == [])
appmod.Helper.on_offer(st2, None)
appmod.Helper.on_offer(st2, [dict(o, aid=o["aid"] + "y") for o in offer])
check("收起后出现完全不同的一组：补记最后点过的卡片", st2.chosen == [aids[1]], str(logs[-1:]))
st2.record_choice = lambda: (setattr(st2, "_maybe", None), None)[1]
appmod.Helper.on_offer(st2, None)
appmod.Helper.on_offer(st2, [dict(o, aid=o["aid"] + "z") for o in offer])
check("没点过卡片时不乱补记", st2.chosen == [aids[1]])

bad = [n for n, ok in results if not ok]
print("\n%d 项通过，%d 项失败" % (len(results) - len(bad), len(bad)))
print("图片：", OUT)
sys.exit(1 if bad else 0)
