"""把推荐出装写入英雄联盟客户端的“装备推荐页”（游戏内商店的推荐栏会直接显示）。

只会增删本程序自己写入的那一页（uid 以 hexhelper- 开头），玩家自己建的推荐页原样保留。
设置 import_item_set 默认开启，在控制台取消勾选“选人时导入出装到客户端”即可关闭。

页面结构和按 P 出现的出装面板一致（标题都很短，游戏内商店的推荐栏比较窄，长标题会被截断）：
  三件套1 68%   面板里的三套核心三件套，各占一栏、完整三件（不够三件用高胜率单件补齐）
  三件套2 …
  三件套3 …
  高胜率装备    单件胜率最高的装备里、三件套中没出现过的（不含鞋子；单件统计拿不到时用常用出装里出现最多的装备）
  鞋子          最常用的两双鞋
同一栏里不重复；三套三件套之间可以有相同装备（和面板一致）。
"""
import time

UID_PREFIX = "hexhelper-"


def _iid(i):
    return str(i.get("itemId") or i.get("id"))


def _pool(data, hid):
    """常用出装里出现过的非鞋装备，按出现次数排序（单件胜率统计拿不到时用）。"""
    cnt = {}
    for g in (data.detail_cached(hid) or {}).get("itemBuilds", []):
        for b in g.get("coreBuilds", []):
            for i in b.get("items", []):
                iid = _iid(i)
                if data.item(iid) and not data.is_boots(iid):
                    cnt[iid] = cnt.get(iid, 0) + 1
    return sorted(cnt, key=lambda k: -cnt[k])


def _boots(data, hid, n=2):
    st = (data.detail_cached(hid) or {}).get("itemStats") or {}
    ids = [str(b["id"]) for b in sorted(st.get("boots", []), key=lambda b: -(b.get("pickRate") or 0))]
    if not ids:
        cnt = {}
        for g in (data.detail_cached(hid) or {}).get("itemBuilds", []):
            for b in g.get("coreBuilds", []):
                for i in b.get("items", []):
                    if data.is_boots(_iid(i)) and _iid(i) != "1001":
                        cnt[_iid(i)] = cnt.get(_iid(i), 0) + 1
        ids = sorted(cnt, key=lambda k: -cnt[k])
    return [i for i in ids if data.item(i)][:n]


def build_item_set(data, hid):
    """按该英雄的真实统计生成一页推荐出装。没有数据返回 None。"""
    hero = data.hero(hid) or {}
    used, blocks = set(), []

    def block(title, ids, limit, share=False):
        out, seen = [], set()
        for i in ids:
            if i not in seen and (share or i not in used) and len(out) < limit:
                seen.add(i)
                out.append({"id": i, "count": 1})
        used.update(seen)
        if out:
            blocks.append({"type": title, "items": out, "hideIfSummonerSpell": "", "showIfSummonerSpell": ""})

    top = [_iid(i) for i in data.top_items(hid, 8)]
    pool = top + [i for i in _pool(data, hid) if i not in top]
    builds = data.builds(hid, 3)      # 与按 P 出现的出装面板里“核心三件套”是同一份数据、同样的顺序
    for n, b in enumerate(builds or [{}], 1):
        core = [_iid(i) for i in b.get("items", [])]
        core += [i for i in pool if i not in core][:max(0, 3 - len(core))]
        wr = b.get("winRate")
        block("三件套%d" % n + (" %.0f%%" % (wr * 100) if wr else ""), core, 3, share=True)
    block("高胜率装备", pool, 6)
    block("鞋子", _boots(data, hid), 2)
    if not blocks:
        return None
    return {"title": "Hex Helper · %s" % (hero.get("title") or hid),
            "uid": "%s%s" % (UID_PREFIX, hid), "type": "custom", "map": "any", "mode": "any",
            "associatedChampions": [int(hid)], "associatedMaps": [], "blocks": blocks,
            "preferredItemSlots": [], "sortrank": 0, "startedFrom": "blank"}


def merge_sets(existing, new_set):
    """去掉本程序以前写入的页，保留玩家自己的页，再放入新的一页。"""
    keep = [s for s in (existing or []) if not str(s.get("uid", "")).startswith(UID_PREFIX)]
    return keep + [new_set]


def import_item_set(lcu, data, hid):
    """写入客户端。返回 (成功与否, 说明)。"""
    page = build_item_set(data, hid)
    if not page:
        return False, "这个英雄还没有出装数据"
    me = lcu.get("/lol-summoner/v1/current-summoner") or {}
    sid = me.get("summonerId")
    if not sid:
        return False, "读不到当前召唤师"
    path = "/lol-item-sets/v1/item-sets/%s/sets" % sid
    cur = lcu.get(path) or {}
    body = {"accountId": cur.get("accountId") or me.get("accountId"),
            "itemSets": merge_sets(cur.get("itemSets"), page),
            "timestamp": int(time.time() * 1000)}
    lcu.put(path, body)
    n = sum(len(b["items"]) for b in page["blocks"])
    return True, "已把 %s 的推荐出装写入客户端（%s，共 %d 件，进游戏后在商店“推荐”里）" % (
        page["title"].split("· ", 1)[-1], " / ".join(b["type"] for b in page["blocks"]), n)
