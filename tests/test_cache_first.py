"""缓存优先：第二次起先用上次的数据立刻显示，后台联网更新，卡片上标出数据更新时间。

在一份复制出来的缓存上做（不动 data/cache）：把缓存改成“3 天前”，然后
  1. 对比旧做法（启动时先联网）和新做法（先读缓存）显示出数据要多久；
  2. 按真实流程跑一遍选人时的取数据：先显示缓存 → 卡片标“更新中…” → 联网拿到新数据 → 卡片变成“刚刚”；
  3. 断网时：继续显示旧数据，不报错；
  4. 把三种状态的卡片渲染成图片（tests/out/）。
用法：py -3 tests/test_cache_first.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config  # noqa: E402
import data_source  # noqa: E402
from data_source import Data  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
HEROES = ["105", "21", "22"]          # 菲兹、好运姐、艾希
DAYS = 3
results = []


def check(name, ok, info=""):
    results.append(ok)
    print("%s %-34s %s" % ("PASS" if ok else "FAIL", name, info))


def age_cache(d, secs):
    """把复制出来的缓存全部改成 secs 秒以前获取的。"""
    for root, _, files in os.walk(d):
        for f in files:
            if not f.endswith(".json"):
                continue
            p = os.path.join(root, f)
            obj = json.load(open(p, encoding="utf-8"))
            if isinstance(obj, dict) and "fetched_at" in obj:
                obj["fetched_at"] = time.time() - secs
                json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def fresh_copy():
    tmp = tempfile.mkdtemp(prefix="hexcache_")
    shutil.copytree(config.CACHE_DIR, tmp, dirs_exist_ok=True)
    age_cache(tmp, DAYS * 86400)
    return tmp


# ------------------------------------------------------------------ 1. 显示出数据要多久
tmp = fresh_copy()
d = Data(tmp)
t = time.time()
d.load(log=lambda *a: None)                 # 旧做法：缓存过期就先联网，等完才显示
for h in HEROES:
    d.ensure_detail(h)
old_secs = time.time() - t
shutil.rmtree(tmp, ignore_errors=True)

tmp = fresh_copy()
d = Data(tmp)
t = time.time()
ok = d.load_cached()                        # 新做法：直接读缓存
for h in HEROES:
    d.ensure_detail(h, offline=True)
new_secs = time.time() - t
check("缓存可直接读出", ok and all(d.detail_cached(h) for h in HEROES))
check("先显示缓存比先联网快", new_secs < old_secs,
      "旧做法 %.2f 秒 → 新做法 %.3f 秒（3 个英雄，缓存为 %d 天前）" % (old_secs, new_secs, DAYS))
check("缓存被标为旧数据", d.meta_stale() and all(d.detail_cached(h)["stale"] for h in HEROES))
before = {h: d.detail_cached(h)["fetched_at"] for h in HEROES}

# ------------------------------------------------------------------ 2. 真实流程：先缓存 → 更新中 → 新数据
import app  # noqa: E402
import ui  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication.instance() or QApplication([])
events = []


class Bus:
    class detail:  # noqa: N801
        @staticmethod
        def emit(cid):
            det = d.detail_cached(cid)
            events.append((cid, "更新中" if cid in d.refreshing else "显示",
                           ui.age_text(det["fetched_at"]).split("（")[0] if det else "无数据"))


fake = types.SimpleNamespace(data=Data(tmp), _fetching=set(), bus=Bus, log=lambda m: events.append(("log", m, "")),
                             pool=types.SimpleNamespace(submit=lambda f: f()), _guard=lambda f, restart=False: f)
d = fake.data
d.load_cached()
snap = {}
orig_emit = Bus.detail.emit


def emit_and_render(cid):           # 每次界面要刷新时，把卡片状态渲染下来
    orig_emit(cid)
    snap.setdefault(events[-1][1] + events[-1][2], cid)
    if cid == HEROES[0]:
        render(cid, "%02d_%s" % (len(events), events[-1][1]))


ctx = None


def render(cid, name):
    card = ui.HeroCard(ctx, cid, "mine", True, False)
    card.adjustSize()
    end = time.time() + 0.8          # 等头像/装备图标从本地缓存加载出来
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    card.grab().save(os.path.join(OUT, "card_%s.png" % name))
    card.deleteLater()


os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    if f.startswith("card_"):
        os.remove(os.path.join(OUT, f))
ctx = ui.Ctx(d, ui.ImageLoader(config.CACHE_DIR), config.Settings())
ctx.auto = 1.0
Bus.detail.emit = staticmethod(emit_and_render)
d.refreshing.add("meta")
t = time.time()
app.Helper.fetch_detail_async(fake, HEROES[0])
d.load(log=lambda *a: None)                 # 榜单也在后台更新完成
d.refreshing.discard("meta")
render(HEROES[0], "99_全部更新完")
flow = [e for e in events if e[0] == HEROES[0]]
print("     卡片状态变化：" + " → ".join("%s(%s)" % (s, a) for _, s, a in flow))
check("第一次刷新就是缓存数据", flow and flow[0][1] == "显示" and "天前" in flow[0][2])
check("联网期间显示“更新中”", any(s == "更新中" for _, s, _ in flow))
det = d.detail_cached(HEROES[0])
check("联网后换成新数据", flow[-1][1] == "显示" and not det["stale"] and det["fetched_at"] > before[HEROES[0]],
      "更新于 %s，用时 %.1f 秒" % (ui.age_text(det["fetched_at"]), time.time() - t))
check("榜单也已更新", not d.meta_stale(), ui.age_text(d.meta["fetched_at"]))

# ------------------------------------------------------------------ 3. 断网：继续用旧数据
tmp2 = fresh_copy()
d2 = Data(tmp2)
d2.load_cached()
real_get = data_source.S.webutil.http_get if hasattr(data_source.S, "webutil") else None


def offline(*a, **k):
    raise OSError("模拟断网")


import sources  # noqa: E402
import webutil  # noqa: E402
saved = (webutil.http_get, sources.http_get if hasattr(sources, "http_get") else None)
webutil.http_get = offline
if saved[1]:
    sources.http_get = offline
fake2 = types.SimpleNamespace(data=d2, _fetching=set(), bus=types.SimpleNamespace(
    detail=types.SimpleNamespace(emit=lambda c: None)), log=lambda m: None,
    pool=types.SimpleNamespace(submit=lambda f: f()), _guard=lambda f, restart=False: f)
err = None
try:
    app.Helper.fetch_detail_async(fake2, HEROES[1])
    try:
        d2.load(log=lambda *a: None)
    except Exception as e:  # noqa: BLE001
        err = e
finally:
    webutil.http_get = saved[0]
    if saved[1]:
        sources.http_get = saved[1]
det2 = d2.detail_cached(HEROES[1])
check("断网时仍显示旧数据", bool(det2) and det2["stale"] and bool(d2.meta.get("summary")),
      "卡片：%s，榜单：%s" % (ui.age_text(det2["fetched_at"]) if det2 else "无", "仍可用" if d2.meta else "丢失"))
render_ctx = ui.Ctx(d2, ui.ImageLoader(config.CACHE_DIR), config.Settings())
render_ctx.auto = 1.0
ctx = render_ctx
render(HEROES[1], "断网_旧数据")

for p in (tmp, tmp2):
    shutil.rmtree(p, ignore_errors=True)
print("卡片截图：%s" % OUT)
print("ALL OK" if all(results) else "有失败项")
sys.exit(0 if all(results) else 1)
