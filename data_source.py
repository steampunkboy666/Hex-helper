"""数据仓库：多数据源 + 本地缓存 + 自动降级。

英雄榜/强化统计：aramgg.com（主）→ op.gg（备，无胜率）→ 本地缓存 → douhunwiki.cn（最后兜底，可能很旧）
出装：aramgg.com → 本地缓存 → douhunwiki.cn（op.gg 的出装表无法可靠解析，不使用）
英雄名/头像/装备价格：腾讯官方数据。
请求量很小：榜单每 6 小时刷新一次；英雄详情只在选人/进游戏时按需获取，缓存 12 小时。
"""
import json
import os
import re
import threading
import time

import sources as S
from webutil import http_get, parse_module  # noqa: F401  (其他模块从这里导入)

SCHEMA = 5
CACHE_TTL = 6 * 3600
DETAIL_TTL = 12 * 3600
RARITY_KEYS = ("silver", "gold", "prismatic")  # augment.rarity 0/1/2
SOURCE_NAMES = {"aramgg": "aramgg.com", "opgg": "op.gg", "douhun": "douhunwiki.cn"}


def norm(text):
    """名称归一化：去空白和标点，便于 OCR 文本与数据库名称比对。"""
    return re.sub(r"[\s·:：!！?？。.,，、\-—_~～'\"“”‘’()（）\[\]【】《》]", "", text or "")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


class Data:
    def __init__(self, cache_dir):
        self.dir = cache_dir
        os.makedirs(os.path.join(cache_dir, "detail"), exist_ok=True)
        self.meta_path = os.path.join(cache_dir, "meta.json")
        self.meta = {}
        self._detail = {}
        self._aug_names = None
        self._down = {}
        self._lock = threading.Lock()
        self._douhun_files = None
        self._lock_dh = threading.Lock()
        self.refreshing = set()   # 正在联网更新的英雄 id（界面据此显示“更新中…”）；"meta" 表示榜单

    # ------------------------------------------------------------ 基础数据
    def _is_down(self, name):
        if name == "aramgg" and os.environ.get("ARAM_FORCE_OPGG"):  # 仅测试用：模拟 aramgg 不可用
            return True
        return time.time() < self._down.get(name, 0)

    def _mark_down(self, name, secs=300):
        self._down[name] = time.time() + secs  # 短时间内不再反复请求已失败的源

    def source_label(self):
        return SOURCE_NAMES.get(self.meta.get("source"), "?")

    def load_cached(self):
        """只读本地缓存（不联网、不管多旧）：第二次起启动时先用它立刻显示，联网更新放到后台。"""
        cached = _read(self.meta_path)
        if cached and cached.get("schema") == SCHEMA and cached.get("summary"):
            self.meta = cached
            return True
        return False

    def meta_age(self):
        """榜单数据距上次联网获取过了多少秒（没有数据时为无穷大）。"""
        return time.time() - self.meta["fetched_at"] if self.meta.get("fetched_at") else float("inf")

    def meta_stale(self):
        return self.meta_age() >= CACHE_TTL

    def load(self, force=False, log=print):
        cached = _read(self.meta_path)
        if cached and cached.get("schema") != SCHEMA:
            cached = None
        age = (time.time() - cached.get("fetched_at", 0)) if cached else 1e18
        if cached and not force and age < CACHE_TTL:
            self.meta = cached
            return
        self._down.clear()
        errors, meta = [], None
        for name, fn in (("aramgg", self._meta_aramgg), ("opgg", self._meta_opgg)):
            if name == "opgg" and cached and cached.get("source") == "aramgg" and age < 72 * 3600:
                log("aramgg 暂时不可用，沿用 %.0f 小时前的 aramgg 缓存" % (age / 3600))
                self.meta = cached
                return
            try:
                log("正在从 %s 更新数据…" % SOURCE_NAMES[name])
                meta = fn(cached)
                break
            except Exception as e:  # noqa: BLE001
                self._mark_down(name)
                errors.append("%s：%s" % (SOURCE_NAMES[name], e))
                log("%s 获取失败：%s" % (SOURCE_NAMES[name], e))
        if meta is None and cached:
            log("所有数据源失败，使用本地缓存（%s，%.0f 小时前）" % (SOURCE_NAMES.get(cached.get("source")), age / 3600))
            self.meta = cached
            return
        if meta is None:
            try:
                log("尝试最后兜底：douhunwiki.cn（数据可能较旧）")
                meta = self._meta_douhun(cached)
            except Exception as e:  # noqa: BLE001
                errors.append("douhunwiki.cn：%s" % e)
                raise RuntimeError("无法获取数据且没有本地缓存 —— " + "；".join(errors))
        self.meta = meta
        _write(self.meta_path, meta)

    def _common(self, cached):
        c = cached or {}
        out = []
        for fn, key in ((S.official_heroes, "heroes"), (S.official_items, "items")):
            try:
                out.append(fn())
            except Exception:  # noqa: BLE001
                out.append(c.get(key))
        if not out[0] or not out[1]:
            raise RuntimeError("无法获取英雄/装备基础数据（game.gtimg.cn）")
        try:
            cat = S.aramgg_catalog()
        except Exception:  # noqa: BLE001
            cat = c.get("augments")
        return out[0], out[1], cat

    def _pack(self, source, summary, version, updated, heroes, items, cat, **extra):
        summary = {k: v for k, v in summary.items() if k in heroes}
        return {"schema": SCHEMA, "fetched_at": time.time(), "source": source, "version": version,
                "updated": updated, "total": len(summary), "heroes": heroes, "augments": cat or {}, "items": items,
                "summary": summary, **extra}

    def _meta_aramgg(self, cached):
        if self._is_down("aramgg"):
            raise RuntimeError("已被强制禁用（测试）")
        summary, ver, upd = S.aramgg_summary()
        heroes, items, cat = self._common(cached)
        if not cat:
            raise RuntimeError("强化目录缺失")
        return self._pack("aramgg", summary, ver, upd, heroes, items, cat)

    def _meta_opgg(self, cached):
        summary, keys, ver, upd = S.opgg_summary()
        heroes, items, cat = self._common(cached)
        if not cat:
            cat = S.douhun_meta()[1]
        return self._pack("opgg", summary, ver, upd, heroes, items, cat, opgg_keys=keys)

    def _meta_douhun(self, cached):
        summary, cat, ver, upd, files = S.douhun_meta()
        heroes, items, _ = self._common(cached)
        self._douhun_files = files
        return self._pack("douhun", summary, ver, upd, heroes, items, cat, douhun_files=files)

    # ------------------------------------------------------------ 查询
    def hero(self, cid):
        return self.meta["heroes"].get(str(cid))

    def hero_by_alias(self, alias):
        a = (alias or "").lower()
        for hid, h in self.meta["heroes"].items():
            if h["alias"].lower() == a:
                return hid
        return None

    def hero_by_name(self, name):
        for hid, h in self.meta["heroes"].items():
            if name in (h["title"], h["name"]):
                return hid
        return None

    def summary(self, cid):
        return self.meta["summary"].get(str(cid))

    def augment(self, aid):
        return self.meta["augments"].get(str(aid))

    def augment_name(self, aid):
        a = self.augment(aid)
        return a["name"] if a else "#%s" % aid

    def item(self, iid):
        return self.meta["items"].get(str(iid))

    def augment_names(self):
        """归一化名称 → 强化 id，用于 OCR 匹配。"""
        if self._aug_names is None:
            self._aug_names = {}
            for aid, a in self.meta["augments"].items():
                self._aug_names.setdefault(norm(a["name"]), aid)
        return self._aug_names

    # ------------------------------------------------------------ 英雄详情（按需、带缓存、可降级）
    def _chain(self):
        """详情的尝试顺序：aramgg 优先；失败过的源 5 分钟内不再重试，恢复后自动切回。"""
        return [n for n in ("aramgg", "opgg", "douhun") if not self._is_down(n)]

    def _cached_file(self, kind, hid, ttl_for):
        path = os.path.join(self.dir, "detail", "%s_%s.json" % (kind, hid))
        c = _read(path)
        fresh = bool(c) and time.time() - c.get("fetched_at", 0) < ttl_for(c.get("source"))
        return path, c, fresh

    def _opgg_key(self, hid):
        keys = self.meta.get("opgg_keys") or {}
        if hid in keys:
            return keys[hid]
        kp = os.path.join(self.dir, "opgg_keys.json")
        k = _read(kp) or {}
        if not k:
            try:
                k = S.opgg_summary()[1]
                _write(kp, k)
            except Exception:  # noqa: BLE001
                k = {}
        return k.get(hid) or (self.hero(hid) or {}).get("alias", "").lower()

    def _douhun_detail_files(self):
        if self.meta.get("douhun_files"):
            return self.meta["douhun_files"]
        with self._lock_dh:  # 多个英雄同时需要时只取一次
            if self._douhun_files is None:
                path = os.path.join(self.dir, "douhun_files.json")
                c = _read(path)
                if c and time.time() - c.get("t", 0) < 24 * 3600:
                    self._douhun_files = c["files"]
                else:
                    self._douhun_files = S.douhun_files()
                    _write(path, {"t": time.time(), "files": self._douhun_files})
            return self._douhun_files

    def _augment_entries(self, hid, offline=False):
        ttl = lambda src: DETAIL_TTL if src == "aramgg" else 3600  # noqa: E731
        path, cached, fresh = self._cached_file("aug", hid, ttl)
        if offline:
            return cached
        if fresh and not (cached["source"] != "aramgg" and not self._is_down("aramgg") and
                          time.time() - cached["fetched_at"] > 900):
            return cached
        for src in self._chain():
            if src != "aramgg" and cached and cached.get("source") == "aramgg" and                     time.time() - cached["fetched_at"] < 72 * 3600:
                return cached  # 3 天内的 aramgg 旧数据（有胜率）优先于 op.gg 的新数据（只有综合分）
            try:
                trios = []
                if src == "aramgg":
                    champ = S.aramgg_champion(hid)
                    raw = S.aramgg_augments(hid, champ)
                    trios = S.aramgg_trios(champ)
                    ents = [{"aid": r["aid"], "tier": r["tier"], "rank": r["rank"], "winRate": r["winRate"],
                             "pickRate": r["pickRate"], "playCount": r["games"], "perf": None} for r in raw]
                elif src == "opgg":
                    raw = S.opgg_augments(self._opgg_key(hid))
                    # op.gg 的梯队(0最好~5最差)才是可靠排序依据；综合分在低样本时会出现 170、0 等异常值，予以丢弃
                    ents = [{"aid": r["aid"], "tier": r["tier"] if r["pickRate"] >= 0.0005 else "5", "rank": None,
                             "winRate": None, "pickRate": r["pickRate"], "playCount": None,
                             "perf": r["perf"] if 0 < r["perf"] <= 100 and r["pickRate"] >= 0.0005 else None}
                            for r in raw]
                else:
                    f = self._douhun_detail_files().get(hid)
                    raw = S.douhun_detail(f)["allAugmentsByRarity"] if f else {}
                    ents = [{"aid": str(e["augmentId"]), "tier": e["tier"], "rank": None, "winRate": e["winRate"],
                             "pickRate": e["pickRate"], "playCount": e["playCount"], "perf": None}
                            for lst in raw.values() for e in lst]
                if not ents:
                    raise S.NoData("空数据")
                out = {"source": src, "fetched_at": time.time(), "entries": ents, "trios": trios}
                _write(path, out)
                return out
            except S.NoData:
                continue  # 这个源没有该英雄的数据：只换下一个源，不判定源故障
            except Exception:  # noqa: BLE001
                self._mark_down(src)
        return cached  # 全部失败：用旧缓存（可能为 None）

    def _builds(self, hid, offline=False):
        path, cached, fresh = self._cached_file("build", hid, lambda s: DETAIL_TTL)
        if fresh or offline:
            return cached
        if not self._is_down("aramgg"):
            try:
                out = {"source": "aramgg", "fetched_at": time.time(), "groups": S.aramgg_builds(hid)}
                _write(path, out)
                return out
            except S.NoData:
                pass  # 该英雄样本不足，aramgg 没有出装：用旧缓存或 douhunwiki 的旧数据补上
            except Exception:  # noqa: BLE001
                self._mark_down("aramgg")
        if cached and cached.get("groups"):
            return cached
        try:
            f = self._douhun_detail_files().get(hid)
            if f:
                groups = S.douhun_detail(f).get("itemBuilds", [])
                if groups:
                    out = {"source": "douhun", "fetched_at": time.time(), "groups": groups}
                    _write(path, out)
                    return out
        except Exception:  # noqa: BLE001
            pass
        return None

    def _item_stats(self, hid, offline=False):
        """单件装备胜率（op.gg）。只是锦上添花：拿不到就用旧缓存，再拿不到就没有。"""
        path, cached, fresh = self._cached_file("items", hid, lambda s: DETAIL_TTL)
        fresh = fresh and "cores" in cached  # 旧版缓存没有三件套，重新获取
        if fresh or offline or self._is_down("opgg"):
            return cached
        try:
            out = {"source": "opgg", "fetched_at": time.time(), **S.opgg_items(self._opgg_key(hid))}
            _write(path, out)
            return out
        except S.NoData:
            return cached
        except Exception:  # noqa: BLE001
            self._mark_down("opgg")
            return cached

    def _adj_wr(self, cid, r, k=100):
        """排序用胜率：场次少的向英雄整体胜率收缩（30 场 70% 不该排在 400 场 60% 前面）；界面仍显示原始胜率。"""
        base = (self.summary(cid) or {}).get("winRate") or 0.5
        return (r["winRate"] * r["games"] + base * k) / (r["games"] + k)

    def is_boots(self, iid, depth=0):
        """鞋子及其所有升级版（合成路线里含“鞋子”1001 的都算）。"""
        iid = str(iid)
        if iid == "1001" or depth > 4:
            return iid == "1001"
        return any(self.is_boots(c, depth + 1) for c in (self.item(iid) or {}).get("from", []))

    def top_items(self, cid, n=3):
        """除鞋子外胜率最高的 n 件装备。样本太少的胜率不可信：先要求选用率 ≥3% 且 ≥30 场，不够再放宽。"""
        st = (self.detail_cached(cid) or {}).get("itemStats") or {}
        boots = {str(b["id"]) for b in st.get("boots", [])}
        rows = [r for r in st.get("items", []) if str(r["id"]) not in boots and not self.is_boots(r["id"])
                and self.item(r["id"])]
        for min_pick, min_games in ((0.03, 30), (0.01, 15), (0, 0)):
            ok = [r for r in rows if r["pickRate"] >= min_pick and r["games"] >= min_games]
            if len(ok) >= n:
                break
        ok.sort(key=lambda r: -self._adj_wr(cid, r))
        return [dict(r, itemId=str(r["id"]), name=self.item(r["id"])["name"], icon=self.item(r["id"]).get("icon"))
                for r in ok[:n]]

    def trios(self, cid):
        return (self.detail_cached(cid) or {}).get("trios") or []

    def ensure_detail(self, cid, offline=False):
        """获取该英雄的强化统计和出装；任何一部分失败都会尽量降级，全部没有则返回 None。
        offline=True：只用本地缓存（不管多旧，不联网），用于先把上次的数据立刻显示出来。"""
        hid = str(cid)
        with self._lock:
            hit = self._detail.get(hid)
            if hit and (offline or not hit["stale"]):
                return hit
        aug, build = self._augment_entries(hid, offline), self._builds(hid, offline)
        items = self._item_stats(hid, offline)
        if not aug and not build:
            return None
        by_rarity = {k: [] for k in RARITY_KEYS}
        for e in (aug or {}).get("entries", []):
            a = self.augment(e["aid"])
            if not a or a.get("enabled") is False:
                continue
            if e["winRate"] is not None:
                q = e["winRate"]
            elif (aug or {}).get("source") == "opgg":
                q = 0.9 - 0.1 * int(e["tier"] or 5) + (e["perf"] or 0) / 1000  # 梯队为主，综合分为辅
            else:
                q = 0.45
            by_rarity[RARITY_KEYS[a["rarity"] if a["rarity"] in (0, 1, 2) else 0]].append({
                "augmentId": int(e["aid"]), "tier": e["tier"], "winRate": e["winRate"], "pickRate": e["pickRate"],
                "playCount": e["playCount"], "perf": e["perf"], "rank": e["rank"], "q": q})
        top = {}
        for k, lst in by_rarity.items():
            lst.sort(key=lambda x: -x["q"])
            top[k] = sorted(lst, key=lambda x: (int(x["tier"] or 9), x["rank"] if x["rank"] else 9999, -x["q"]))[:8]
        d = {"allAugmentsByRarity": by_rarity, "augmentsByRarity": top,
             "itemBuilds": (build or {}).get("groups", []),
             "itemStats": items, "trios": (aug or {}).get("trios") or [],
             "aug_source": (aug or {}).get("source"), "build_source": (build or {}).get("source"),
             "date": time.strftime("%Y-%m-%d", time.localtime((aug or build)["fetched_at"])), "_ts": time.time()}
        # 这张卡片的数据是什么时候联网拿到的：取各部分里最旧的那个；超过 12 小时算“旧数据”，需要后台更新
        d["fetched_at"] = min(p["fetched_at"] for p in (aug, build) if p)
        d["stale"] = time.time() - d["fetched_at"] >= DETAIL_TTL
        with self._lock:
            self._detail[hid] = d
        return d

    def detail_cached(self, cid):
        return self._detail.get(str(cid))

    def detail_stale(self, cid):
        """内存里没有这个英雄的数据，或者是超过 12 小时的旧数据。"""
        det = self._detail.get(str(cid))
        return det is None or det["stale"]

    def _boots_ids(self, cid):
        st = (self.detail_cached(cid) or {}).get("itemStats") or {}
        return {str(b["id"]) for b in st.get("boots", [])}

    def _no_boots(self, cid, iid):
        return str(iid) not in self._boots_ids(cid) and not self.is_boots(iid)

    def builds(self, cid, limit=3):
        """不含鞋子的核心三件套。优先 op.gg 的三件套统计（本身不含鞋）：样本够的里面按胜率排；
        拿不到时退回 aramgg 的核心出装并去掉其中的鞋子。"""
        st = (self.detail_cached(cid) or {}).get("itemStats") or {}
        cores = [c for c in st.get("cores", []) if all(self._no_boots(cid, i) and self.item(i) for i in c["ids"])]
        for min_pick, min_games in ((0.02, 30), (0.01, 15), (0, 0)):
            ok = [c for c in cores if c["pickRate"] >= min_pick and c["games"] >= min_games]
            if len(ok) >= limit:
                break
        if ok:
            ok.sort(key=lambda c: -self._adj_wr(cid, c))
            seen, uniq = set(), []
            for c in ok:  # 同样三件、只是购买顺序不同的算一套
                key = frozenset(c["ids"][:3])
                if key not in seen:
                    seen.add(key)
                    uniq.append(c)
            ok = uniq
            return [{"items": [{"itemId": str(i), "name": self.item(i)["name"], "icon": self.item(i).get("icon")}
                               for i in c["ids"][:3]],
                     "winRate": c["winRate"], "pickRate": c["pickRate"], "playCount": c["games"],
                     "label": "三件套 %d" % (k + 1), "source": "opgg"} for k, c in enumerate(ok[:limit])]
        out = []
        for b in self._aramgg_builds(cid):
            items = [i for i in b["items"] if self._no_boots(cid, i["itemId"])]
            if items:
                out.append(dict(b, items=items, label=(b.get("label") or "") + ("（已去掉鞋子）" if len(items) < len(b["items"]) else "")))
        seen, uniq = set(), []
        for b in out:
            sig = tuple(str(i["itemId"]) for i in b["items"])
            if sig not in seen:
                seen.add(sig)
                uniq.append(b)
        return uniq[:limit]

    def _aramgg_builds(self, cid):
        """aramgg 的核心出装（含鞋），按选取率降序。"""
        det = self.detail_cached(cid)
        out, seen = [], set()
        for g in (det or {}).get("itemBuilds", []):
            for b in g.get("coreBuilds", []):
                out.append(b)
        out.sort(key=lambda b: -(b.get("playCount") or b.get("pickRate") or 0))
        uniq = []
        for b in out:
            sig = tuple(str(i["itemId"]) for i in b["items"])
            if sig not in seen:
                seen.add(sig)
                uniq.append(b)
        return uniq
