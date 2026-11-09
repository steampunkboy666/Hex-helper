"""三个数据源的取数与解析（都只读公开页面/数据文件，低频、按需、带缓存）。

- aramgg.com：主源。英雄统计/强化目录/每英雄强化统计是页面自己加载的 JSON；出装在英雄页 HTML 里。
- op.gg：备用源。只有梯队/排名/综合分/选取率，没有胜率数字，也不解析出装。
- douhunwiki.cn：最后兜底（数据可能很旧，仅在前两者都失败且没有任何缓存时使用）。
"""
import json
import re
import urllib.error
from datetime import date

from webutil import decode_rsc, http_get, parse_module

A_BASE = "https://aramgg.com"
A_CDN = "https://cdn.dtodo.cn/hextech"
O_BASE = "https://op.gg/lol/modes/aram-mayhem"
D_BASE = "http://www.douhunwiki.cn"
HERO_LIST = "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"
ITEMS_JS = "https://game.gtimg.cn/images/lol/act/img/js/items/items.js"
RARITY_KEYS = ("silver", "gold", "prismatic")


class NoData(Exception):
    """数据源正常，但它没有这个英雄的数据（样本不足/页面不存在）。不代表数据源故障。"""


def _get_hero(url, **kw):
    try:
        return http_get(url, **kw)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoData("404")
        raise


def _strip(t):
    return re.sub(r"<[^>]+>", "", t or "")


# ---------------------------------------------------------------- 腾讯官方：英雄 / 装备
def official_heroes():
    hs = json.loads(http_get(HERO_LIST))["hero"]
    return {h["heroId"]: {
        "name": h["name"], "title": h["title"], "alias": h["alias"],
        "avatar": "https://game.gtimg.cn/images/lol/act/img/champion/%s.png" % h["alias"],
        "splash": "https://game.gtimg.cn/images/lol/act/img/skin/big%s000.jpg" % h["heroId"]} for h in hs}


def official_items():
    raw = json.loads(http_get(ITEMS_JS)).get("items", [])
    return {str(i["itemId"]): {"name": i["name"], "price": int(i.get("total") or i.get("price") or 0),
                               "icon": i.get("iconPath"), "from": i.get("from") or []} for i in raw}


# ---------------------------------------------------------------- aramgg.com
def aramgg_summary():
    rows = json.loads(http_get(A_BASE + "/data/champions-stats.json"))
    rows = [r for r in rows if r.get("winRate") is not None]
    rows.sort(key=lambda r: (int(r["tier"]), -r["winRate"], -(r.get("pickRate") or 0)))
    summary = {str(r["championId"]): {"rank": i + 1, "tier": str(r["tier"]), "winRate": r["winRate"],
                                       "pickRate": r.get("pickRate"), "games": r.get("numGames")}
               for i, r in enumerate(rows)}
    if not summary:
        raise ValueError("aramgg 英雄统计为空")
    return summary, str(rows[0].get("version")), max(r.get("date") or "" for r in rows)


def aramgg_catalog():
    j = json.loads(http_get(A_BASE + "/data/aram-mayhem-augments.zh_cn.json"))
    out = {}
    for k, v in j.items():
        if not isinstance(v, dict) or not v.get("displayName"):
            continue
        icon = (v.get("iconLarge") or v.get("iconSmall") or "").lower()
        out[str(k)] = {"name": v["displayName"], "rarity": v.get("rarity", 0), "desc": _strip(v.get("description")),
                       "icon": "%s/augment-icons/%s" % (A_CDN, icon) if icon else None, "enabled": v.get("enabled", True)}
    if len(out) < 50:
        raise ValueError("aramgg 强化目录异常")
    return out


def aramgg_champion(hid):
    """aramgg 的英雄详情数据文件（强化统计 + 强化三件组合），解析后的 dict。"""
    j = json.loads(_get_hero("%s/champion-details/%s.json" % (A_CDN, hid)))
    ent = next((e for e in j.get("championAugments", []) if e and str(e[0]) == str(hid)), None)
    if not ent:
        raise NoData("英雄强化数据缺失")
    return json.loads(ent[1])


def aramgg_trios(champ):
    """该英雄胜率最高的强化三件组合（网站展示的 TOP 5）→ [{ids:[aid,aid,aid], winRate, games}]"""
    out = []
    for key, v in (champ.get("augment_trios") or {}).items():
        ids = [x for x in key.split(":") if x]
        if len(ids) >= 2 and v.get("win_rate"):
            out.append({"ids": ids, "winRate": float(v["win_rate"]), "games": int(v.get("num_games") or 0)})
    return sorted(out, key=lambda t: -t["winRate"])


def aramgg_augments(hid, champ=None):
    """该英雄各强化统计 → [{aid, tier, rank, winRate, pickRate, games, date}]"""
    augs = (champ or aramgg_champion(hid))["augments"]
    out = []
    for aid, v in augs.items():
        wr = v.get("win_rate")
        out.append({"aid": str(aid), "tier": str(v.get("tier")), "rank": int(v["rank"]) if v.get("rank") else None,
                    "winRate": float(wr) if wr not in (None, "") else None,
                    "pickRate": float(v.get("pick_rate") or 0), "games": int(v.get("num_games") or 0),
                    "date": v.get("win_rate_date")})
    return out


def aramgg_builds(hid):
    """英雄页 HTML 里的“装备配置”：按流派(标签)分组的核心三件套。"""
    html = _get_hero("%s/zh-CN/champion-stats/%s" % (A_BASE, hid))
    if "data-champion-augments" not in html:
        raise ValueError("英雄页结构变化（找不到强化表）")  # 页面本身不对 → 视为数据源异常
    start = html.find("data-build-tabs")
    if start < 0:
        raise NoData("该英雄样本不足，没有出装")
    html = html[start:]
    tabs = []
    for m in re.finditer(r'<button[^>]*data-build-index="(\d+)"[^>]*>(.*?)</button>', html, re.S):
        tags = [t for t in re.findall(r">\s*([A-Za-z][A-Za-z \-]*?)\s*</span>", m.group(2))]
        wr = re.search(r"([\d.]+)%", m.group(2))
        tabs.append({"idx": int(m.group(1)), "tags": tags, "winRate": float(wr.group(1)) / 100 if wr else None})
    pos = [(int(m.group(1)), m.start()) for m in re.finditer(r'data-build-panel="(\d+)"', html)]
    groups = []
    for n, (idx, p) in enumerate(pos):
        panel = html[p:pos[n + 1][1] if n + 1 < len(pos) else len(html)]
        h = panel.find("核心装备")
        e = panel.find("<h3", h + 10) if h >= 0 else -1
        core = panel[h:e if e > 0 else len(panel)] if h >= 0 else ""
        builds = []
        for part in re.split(r'#\s*(?=\d\s*</span>)', core)[1:]:
            items = [{"itemId": i, "name": nm, "icon": "%s/item-icons/%s.png" % (A_CDN, i)}
                     for i, nm in re.findall(r'item-icons/(\d+)\.png"\s+alt="([^"]*)"', part)]
            wr = re.search(r"胜率:\s*(?:<[^>]+>\s*)*([\d.]+)%", part)
            pr = re.search(r"选取率:\s*(?:<[^>]+>\s*)*([\d.]+)%", part)
            if items:
                builds.append({"items": items, "winRate": float(wr.group(1)) / 100 if wr else 0,
                               "pickRate": float(pr.group(1)) / 100 if pr else 0, "playCount": None,
                               "order": len(builds) + 1})
        tab = next((t for t in tabs if t["idx"] == idx), {"tags": [], "winRate": None})
        label = " / ".join(tab["tags"])
        for b in builds:
            b["label"], b["tags"] = label, tab["tags"]
        if builds:
            groups.append({"tags": tab["tags"], "winRate": tab["winRate"], "playCount": None,
                           "pickRate": sum(b["pickRate"] for b in builds), "coreBuilds": builds})
    if not groups:
        raise NoData("装备配置为空")
    return groups


# ---------------------------------------------------------------- op.gg
def opgg_summary():
    """梯队榜：返回 (summary, keys, version, updated)。op.gg 没有胜率数字，winRate=None。"""
    html = http_get(O_BASE + "?hl=zh_CN", timeout=40)
    t = decode_rsc(html)
    ver = re.search(r"/lol/(\d+\.\d+)\.\d+/", t)
    rows = re.findall(r'\{"key":"([a-z0-9]+)","name":"[^"]*","image_url":"[^"]*","champion_id":(\d+),'
                      r'"id":\d+,"tier":(\d+),"rank":(\d+)\}', t)
    if not rows or not ver:
        raise ValueError("op.gg 榜单解析为空")
    summary, keys = {}, {}
    for key, cid, tier, rank in rows:
        summary[cid] = {"rank": int(rank), "tier": tier, "winRate": None, "pickRate": None, "games": None}
        keys[cid] = key
    return summary, keys, ver.group(1), date.today().isoformat()


def _opgg_section(t, title, titles=("出门装", "核心装备", "鞋子", "装备")):
    """op.gg 装备页里某个小节（按标题切分）的条目：[(ids, 选用率%, 场次, 胜率%)]"""
    mark = '"children":"%s"}]}]' % title
    i = t.find(mark)
    if i < 0:
        return []
    seg = t[i + len(mark):]
    ends = [seg.find('"children":"%s"}]}]' % x) for x in titles]
    ends = [e for e in ends if e > 0]
    seg = seg[:min(ends)] if ends else seg
    return [([x for x in ids.split(",") if x], float(pr), int(play), float(wr)) for ids, pr, play, wr in re.findall(
        r'\{"ids":\[([\d,]+)\],"pick_rate":([\d.]+),"play":"?(\d+)"?,"win_rate":([\d.]+)', seg)]


def opgg_items(key):
    """该英雄在海克斯大乱斗里的单件装备统计（op.gg），鞋子单独一组。
    → {"items": [{id, pickRate, games, winRate}], "boots": [...]}"""
    t = decode_rsc(_get_hero("%s/%s/items?hl=zh_CN" % (O_BASE, key), timeout=40))
    conv = lambda rows: [{"id": ids[0], "pickRate": pr / 100, "games": play, "winRate": wr / 100}  # noqa: E731
                         for ids, pr, play, wr in rows if len(ids) == 1]
    items, boots = conv(_opgg_section(t, "装备")), conv(_opgg_section(t, "鞋子"))
    cores = [{"ids": ids, "pickRate": pr / 100, "games": play, "winRate": wr / 100}
             for ids, pr, play, wr in _opgg_section(t, "核心装备") if len(ids) >= 3]  # op.gg 的核心三件套本身不含鞋
    if not items:
        raise NoData("op.gg 没有该英雄的装备统计")
    return {"items": items, "boots": boots, "cores": cores}


def opgg_augments(key):
    """该英雄各强化：[{aid, tier, perf, pickRate}]（tier 0=OP，数字越大越弱；perf 为 op.gg 综合分）"""
    html = _get_hero("%s/%s/augments?hl=zh_CN" % (O_BASE, key), timeout=40)
    t = decode_rsc(html)
    seen = {}
    for aid, tier, perf, pop in re.findall(
            r'\{"id":(\d+),"tier":(\d+),"performance":([0-9.]+),"popular":([0-9.]+),"name":"', t):
        seen.setdefault(aid, {"aid": aid, "tier": tier, "perf": float(perf), "pickRate": float(pop) / 100,
                              "winRate": None, "games": None, "rank": None})
    if not seen:
        raise NoData("op.gg 没有该英雄的强化数据")
    return list(seen.values())


# ---------------------------------------------------------------- douhunwiki.cn（最后兜底）
def douhun_meta():
    """返回 (summary, catalog, version, updated, detail_files)"""
    html = http_get(D_BASE + "/")
    idx = http_get(D_BASE + re.search(r'src="(/assets/index-[^"]+\.js)"', html).group(1))

    def fetch(prefix):
        m = re.search(r"import\(`\./(%s-[\w-]+\.js)`\)" % re.escape(prefix), idx)
        if not m:
            raise RuntimeError("douhunwiki 站点结构变化：" + prefix)
        return http_get(D_BASE + "/assets/" + m.group(1))

    catalog = {str(a["id"]): {"name": a["name"], "rarity": a["rarity"], "desc": a.get("desc") or "",
                              "icon": a.get("iconLarge") or a.get("iconSmall"), "enabled": True}
               for a in parse_module(fetch("mayhem-augments"))}
    summ = parse_module(fetch("opgg-mayhem-summary"))
    rows = {r["heroId"]: {k: r[k] for k in ("rank", "tier", "winRate", "pickRate", "games")} for r in summ["rows"]}
    loaders = fetch("opgg-mayhem-detail-loaders")
    files = dict(re.findall(r"(\d+):\(\)=>\w+\(\(\)=>import\(`\./([\w.-]+\.js)`\)", loaders))
    return rows, catalog, str(summ.get("sourceVersion")), summ.get("updatedAt"), files


def douhun_files():
    """只取“英雄 → 详情数据文件名”的映射（3 个小请求），不下载完整榜单。"""
    html = http_get(D_BASE + "/")
    idx = http_get(D_BASE + re.search(r'src="(/assets/index-[^"]+\.js)"', html).group(1))
    m = re.search(r"import\(`\./(opgg-mayhem-detail-loaders-[\w-]+\.js)`\)", idx)
    if not m:
        raise RuntimeError("douhunwiki 站点结构变化")
    loaders = http_get(D_BASE + "/assets/" + m.group(1))
    return dict(re.findall(r"(\d+):\(\)=>\w+\(\(\)=>import\(`\./([\w.-]+\.js)`\)", loaders))


def douhun_detail(fn):
    raw = parse_module(http_get(D_BASE + "/assets/" + fn))
    return raw
