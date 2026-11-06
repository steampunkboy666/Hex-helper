"""验证数据源降级链：模拟 aramgg / op.gg 故障。运行：py -3 tests/test_fallback.py（需要联网访问另一个源）"""
import os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sources as S
import data_source as D

def fresh(name):
    p = os.path.join(tempfile.gettempdir(), "aram_fb_" + name); shutil.rmtree(p, ignore_errors=True); os.makedirs(p); return D.Data(p)

def boom(*a, **k): raise OSError("模拟故障")
orig = {k: getattr(S, k) for k in dir(S) if k.startswith(("aramgg_", "opgg_", "douhun_"))}
def restore():
    for k, v in orig.items(): setattr(S, k, v)
def kill(prefix):
    for k in orig:
        if k.startswith(prefix): setattr(S, k, boom)
log = lambda m: None
res = {}

# 1) aramgg 全挂、无缓存 → 退到 op.gg；出装：无缓存 → douhunwiki 兜底
kill("aramgg_"); d = fresh("1"); d.load(log=log)
res["1 source"] = d.meta["source"]; res["1 summary winRate None"] = d.summary(875)["winRate"] is None
det = d.ensure_detail(875)
res["1 aug_source"], res["1 build_source"] = det["aug_source"], det["build_source"]
res["1 augments"] = sum(len(v) for v in det["allAugmentsByRarity"].values())
res["1 builds"] = len(d.builds(875))
restore()

# 2) 先正常加载（缓存 aramgg），再让 aramgg 挂，强制刷新 → 沿用缓存
d = fresh("2"); d.load(log=log); d.ensure_detail(875)
kill("aramgg_"); d2 = D.Data(d.dir); d2.load(force=True, log=log)
res["2 source"] = d2.meta["source"]; d2.meta["fetched_at"]; 
det = d2.ensure_detail(875)  # 详情缓存新鲜 → 仍是 aramgg 数据
res["2 detail aug_source"] = det["aug_source"]
restore()

# 3) 缓存里的详情已过期且 aramgg 挂 → 用旧缓存而不是空
d = fresh("3"); d.load(log=log); d.ensure_detail(875)
for f in os.listdir(os.path.join(d.dir, "detail")):
    p = os.path.join(d.dir, "detail", f); import json; j = json.load(open(p, encoding="utf-8")); j["fetched_at"] -= 99999; json.dump(j, open(p, "w", encoding="utf-8"))
kill("aramgg_"); d3 = D.Data(d.dir); d3.load(log=log); d3.meta = d.meta
det = d3.ensure_detail(875)
res["3 aug_source (stale ok)"] = det["aug_source"] if det else None
restore()

# 4) 全部挂且无缓存 → 报错
for pre in ("aramgg_", "opgg_", "douhun_"): kill(pre)
try: fresh("4").load(log=log); res["4"] = "未报错(异常)"
except RuntimeError as e: res["4"] = "RuntimeError: " + str(e)[:80]
restore()

# 5) 全部挂但有缓存 → 用缓存
d = fresh("5"); d.load(log=log)
for pre in ("aramgg_", "opgg_", "douhun_"): kill(pre)
d5 = D.Data(d.dir); d5.load(force=True, log=log); res["5 source"] = d5.meta["source"]
restore()
for k, v in res.items(): print(k, "=", v)
