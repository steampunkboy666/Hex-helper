"""推荐逻辑（与界面无关）：海克斯强化评分/适配度/后续搭配，商店出装模型。

强化评分 = 单项分（该英雄选此强化的胜率，小样本向均值收缩）+ 搭配分：
  · 组合数据：aramgg 统计的该英雄胜率最高的强化三件组合，候选与已选越能凑成这些组合，加分越多（真实统计）；
  · 关键词联动：与已选强化/英雄出装路线同流派加分，物理/法术方向冲突扣分（推断，分值较小）。
第一次选择时没有已选强化，主要看单项分，只给“能凑成高胜率组合”一点潜力分。
"""
import re

from data_source import RARITY_KEYS

RARITY_NAMES = ("白银", "黄金", "棱彩")

# 关键词 → 标签：用于判断强化之间/强化与英雄出装路线是否同流派
TAG_WORDS = {
    "暴击": ("暴击",),
    "攻速": ("攻击速度", "攻速"),
    "吸血": ("生命偷取", "全能吸血", "吸血"),
    "法强": ("法术强度", "法强"),
    "法伤": ("魔法伤害", "法术伤害"),
    "急速": ("技能急速", "冷却时间", "冷却"),
    "肉盾": ("最大生命值", "护甲", "魔法抗性", "魔抗", "坚韧", "减伤", "伤害减免"),
    "护盾治疗": ("治疗", "护盾", "回复"),
    "攻击力": ("攻击力", "物理伤害"),
    "命中": ("攻击特效", "命中时", "普通攻击", "普攻"),
    "位移": ("冲刺", "闪烁", "移动速度", "位移"),
    "控制": ("减速", "眩晕", "定身", "击飞", "晕"),
    "持续伤害": ("灼烧", "持续伤害", "中毒", "流血"),
    "金币": ("金币",),
    "大招": ("终极技能",),
}
# 出装流派标签 → 强化标签
ARCH_TAGS = {
    "AD": {"攻击力"}, "AP": {"法强", "法伤"}, "Tank": {"肉盾", "护盾治疗"}, "Crit": {"暴击", "攻速"},
    "On-Hit": {"命中", "攻速"}, "Lethality": {"攻击力"}, "Bruiser": {"攻击力", "肉盾", "吸血"},
    "Mage": {"法强", "法伤"}, "Support": {"护盾治疗", "急速"}, "Assassin": {"攻击力", "位移"},
}


_SCALING = re.compile(r"[（(]随[^）)]*[）)]")


def aug_tags(aug):
    # “（随法术强度变化）”之类只是说明数值随什么成长，不代表这张强化提供该属性，不参与判断
    text = (aug.get("name") or "") + _SCALING.sub("", aug.get("desc") or "")
    return {t for t, ws in TAG_WORDS.items() if any(w in text for w in ws)}


def hero_arch(data, hid):
    """英雄的主流出装流派 → 强化标签集合。"""
    det = data.ensure_detail(hid, offline=True) if hid else None  # 只读已有数据，界面线程里绝不联网
    groups = sorted((det or {}).get("itemBuilds", []), key=lambda g: -(g.get("playCount") or g.get("pickRate") or 0))
    tags = set()
    for t in (groups[0].get("tags", []) if groups else []):
        tags |= ARCH_TAGS.get(t, set())
    return tags


def _hero_list(data, hid, rk):
    det = data.ensure_detail(hid, offline=True) if hid else None  # 只读已有数据，界面线程里绝不联网
    lst = ((det or {}).get("allAugmentsByRarity") or {}).get(rk) or []
    return sorted(lst, key=lambda x: -x["q"])


def rate_augment(data, hid, aid):
    """单个强化对该英雄的评分。返回 None 表示数据库里没有这个强化。"""
    a = data.augment(aid)
    if not a:
        return None
    rk = RARITY_KEYS[a["rarity"]] if a["rarity"] in (0, 1, 2) else "silver"
    lst = _hero_list(data, hid, rk)
    r = {"aid": str(aid), "name": a["name"], "rarity": a["rarity"], "rname": RARITY_NAMES[min(a["rarity"], 2)],
         "icon": a.get("icon"), "desc": a.get("desc", ""), "tags": aug_tags(a),
         "wr": None, "perf": None, "tier": None, "rank": None, "total": len(lst), "pick": None, "play": None,
         "source": "none", "score": 0.45}
    for i, e in enumerate(lst):
        if str(e["augmentId"]) == str(aid):
            r.update(wr=e["winRate"], perf=e.get("perf"), tier=e["tier"], rank=i + 1, pick=e["pickRate"],
                     play=e["playCount"], source="hero", score=e["q"])
            break
    if r["source"] == "hero" and r["play"] is not None:
        mean = sum(x["q"] for x in lst) / len(lst)
        n = r["play"]
        r["score"] = (r["score"] * n + mean * 300) / (n + 300)  # 样本少的向均值收缩，避免被小样本刷高
    return r


# 判断物理/法术方向冲突只看“加什么属性”：“额外魔法伤害”多为攻击特效，物理英雄照样能用，不算法术方向
AD_TAGS, AP_TAGS = {"攻击力", "暴击"}, {"法强"}
COMBO_CAP, TAG_CAP = 0.06, 0.025


def _hero_base(data, hid):
    s = data.summary(hid) if hid else None
    return (s or {}).get("winRate") or 0.5


def combo_bonus(data, hid, cand, chosen):
    """候选强化与已选强化能否凑成该英雄的高胜率三件组合 → (加分, 说明或 None)。"""
    have = {c["aid"] for c in chosen}
    base = _hero_base(data, hid)
    best = (0.0, None)
    for t in data.trios(hid):
        ids = set(t["ids"])
        if cand["aid"] not in ids or len(have - ids) > max(0, 4 - len(ids)):  # 已选里不属于该组合的太多：凑不成
            continue
        overlap = len(have & ids)
        w = (0.25, 0.6, 1.0)[min(overlap, 2)]                       # 已经选中的组合成员越多，越可信
        wr = (t["winRate"] * t["games"] + base * 150) / (t["games"] + 150)  # 小样本收缩
        b = min(COMBO_CAP, max(0.0, w * (wr - base)))
        if b > best[0]:
            others = [data.augment_name(a) for a in t["ids"] if a != cand["aid"]]
            best = (b, {"others": others, "have": [data.augment_name(a) for a in t["ids"] if a in have],
                        "winRate": t["winRate"], "games": t["games"]})
    return best


def tag_bonus(cand, chosen, arch):
    """关键词联动（推断）：同流派加分，物理/法术方向冲突扣分。"""
    ctags = set()
    for c in chosen:
        ctags |= c["tags"]
    b = 0.008 * len(cand["tags"] & ctags) + 0.004 * len(cand["tags"] & arch)
    side = (ctags | arch)
    if (cand["tags"] & AP_TAGS and not cand["tags"] & AD_TAGS and side & AD_TAGS and not side & AP_TAGS) or \
            (cand["tags"] & AD_TAGS and not cand["tags"] & AP_TAGS and side & AP_TAGS and not side & AD_TAGS):
        b -= 0.012
    return max(-TAG_CAP, min(TAG_CAP, b))


def fit(cand, chosen, arch):
    """候选强化与已选强化、英雄流派的适配度 → (等级, 原因)。"""
    chosen_tags = set()
    for c in chosen:
        chosen_tags |= c["tags"]
    shared = cand["tags"] & chosen_tags
    arch_shared = cand["tags"] & arch
    score = len(shared) + 0.7 * len(arch_shared)
    reasons = []
    if shared and chosen:
        reasons.append("与已选同为" + "/".join(sorted(shared)))
    if arch_shared:
        reasons.append("契合出装路线(" + "/".join(sorted(arch_shared)) + ")")
    level = "高" if score >= 2 else "中" if score >= 1 else "一般"
    return level, "，".join(reasons) or "无明显联动"


# 强化标签 → 套路名（按优先级：前面的更能代表这张强化的打法）
STYLE_NAMES = (
    ("暴击", "暴击普攻流"), ("命中", "攻速特效流"), ("攻速", "攻速特效流"), ("法强", "法术爆发流"),
    ("急速", "技能轰炸流"), ("大招", "大招流"), ("持续伤害", "持续伤害流"),
    ("吸血", "吸血续航流"), ("护盾治疗", "护盾治疗流"), ("肉盾", "前排坦克流"), ("控制", "控制开团"),
    ("攻击力", "物理输出"), ("位移", "机动拉扯"), ("金币", "经济发育"), ("法伤", "附加魔法伤害"),
)
# 这些标签决定“出什么装”；急速、护盾治疗之类谁都能用，没有对应出装路线也不算问题
BUILD_DRIVING = {"暴击", "攻速", "命中", "法强", "肉盾", "攻击力"}
# 强化标签的口语说法（“都加 xx”）
TAG_SAY = {"暴击": "暴击", "攻速": "攻速", "吸血": "吸血", "法强": "法强", "法伤": "法术伤害", "急速": "技能冷却",
           "肉盾": "坦度", "护盾治疗": "护盾/治疗", "攻击力": "攻击力", "命中": "普攻特效", "位移": "机动性",
           "控制": "控制", "持续伤害": "持续伤害", "金币": "金币", "大招": "大招"}
BUILD_NAMES = {"AD": "物理", "AP": "法术", "Tank": "坦克", "Crit": "暴击", "On-Hit": "攻击特效", "Lethality": "穿甲",
               "Bruiser": "战士", "Mage": "法师", "Support": "辅助", "Assassin": "刺客"}


MOVE_WORDS = ("冲刺", "闪烁", "位移", "传送")


def style_of(tags, aug=None):
    """强化标签 → 套路名列表（最多两个，去重）。“移动速度”几乎每张强化都顺带有，只有冲刺/闪烁类才算机动套路。"""
    if aug is not None and "位移" in tags and not any(w in (aug.get("desc") or "") for w in MOVE_WORDS):
        tags = tags - {"位移"}
    out = []
    for t, name in STYLE_NAMES:
        if t in tags and name not in out:
            out.append(name)
    return out[:2]


def build_for(data, hid, cand):
    """该英雄的各套出装里，和这张强化最搭的一套（按出装流派标签与强化标签重合度，其次胜率）。
    返回 {"label", "winRate", "items":[名称], "main": 是否就是最常用的那套} 或 None。"""
    det = data.ensure_detail(hid, offline=True) if hid else None
    groups = [g for g in (det or {}).get("itemBuilds", []) if g.get("tags") and g.get("coreBuilds")]
    if not groups or not cand["tags"]:
        return None
    main = max(groups, key=lambda g: g.get("playCount") or g.get("pickRate") or 0)

    def match(g):
        return sum(1 for t in g["tags"] if ARCH_TAGS.get(t, set()) & cand["tags"])
    best = max(groups, key=lambda g: (match(g), g.get("winRate") or 0))
    if match(best) == 0:
        return None
    items = []
    for cb in best["coreBuilds"]:          # 第一套去掉鞋子后不够三件，就从这条路线的其他出装里补
        for it in cb.get("items", []):
            iid = str(it.get("itemId"))
            name = (data.item(iid) or {}).get("name") or it.get("name") or iid
            if not data.is_boots(iid) and name not in items:
                items.append(name)
        if len(items) >= 3:
            break
    if not items:
        return None
    return {"label": "/".join(BUILD_NAMES.get(t, t) for t in best["tags"]), "winRate": best.get("winRate"),
            "items": items[:3], "main": best is main}


def play_note(data, hid, cand, chosen, arch):
    """用人话说明这张强化：适合什么套路、和已选的哪张联动、和英雄出装是否冲突、选了之后出什么装。
    联动/套路是根据强化描述里的关键词推断的；出装是该英雄真实的出装统计。"""
    styles = style_of(cand["tags"], cand)
    links = []
    for c in chosen:
        common = cand["tags"] & c["tags"]
        if common:
            links.append("配合已选「%s」（都加%s）" % (c["name"], "、".join(TAG_SAY[t] for t in sorted(common))))
    warn = None
    side = set(arch)
    for c in chosen:
        side |= c["tags"]
    if cand["tags"] & AP_TAGS and not cand["tags"] & AD_TAGS and side & AD_TAGS and not side & AP_TAGS:
        warn = "偏法术，和这个英雄的物理出装方向相反"
    elif cand["tags"] & AD_TAGS and not cand["tags"] & AP_TAGS and side & AP_TAGS and not side & AD_TAGS:
        warn = "偏物理，和这个英雄的法术出装方向相反"
    elif cand["tags"] & arch and not links:
        links.append("契合这个英雄的常用出装（%s）" % "、".join(TAG_SAY[t] for t in sorted(cand["tags"] & arch)))
    build = build_for(data, hid, cand)
    return {"styles": styles, "links": links[:2], "warn": warn, "build": build,
            "nobuild": build is None and bool(cand["tags"] & BUILD_DRIVING) and not warn}


def followups(data, hid, cand, chosen, arch, n=3):
    """选了 cand 之后，后续稀有度里值得留意的强化（胜率 + 联动加成）。"""
    nxt = [r for r in range(cand["rarity"] + 1, 3)] or [2]
    have = {c["aid"] for c in chosen} | {cand["aid"]}
    ctags = set(cand["tags"])
    for c in chosen:
        ctags |= c["tags"]
    # 能与已选+候选凑成高胜率组合的强化优先（真实统计），其次按胜率与关键词联动
    trio_mates = {}
    for t in data.trios(hid):
        ids = set(t["ids"])
        if cand["aid"] in ids and not (have - {cand["aid"]}) - ids:
            for a in ids - have:
                trio_mates[a] = max(trio_mates.get(a, 0), t["winRate"])
    pool = []
    cands = [str(e["augmentId"]) for r in nxt for e in _hero_list(data, hid, RARITY_KEYS[r])[:25]]
    cands += [a for a in trio_mates if a not in cands]   # 组合成员不限稀有度
    for aid in cands:
        if aid in have:
            continue
        x = rate_augment(data, hid, aid)
        if x:
            bonus = 0.025 * len(x["tags"] & ctags) + 0.01 * len(x["tags"] & arch)
            if aid in trio_mates:
                x["combo_mate"] = trio_mates[aid]
                bonus += 0.05
            pool.append((x["score"] + bonus, x))
    pool.sort(key=lambda t: -t[0])
    return [x for _, x in pool[:n]]


def evaluate_offer(data, hid, offered_ids, chosen_ids):
    """海克斯三选一：每个选项的评分、适配度、后续搭配，并标出最推荐的一个。"""
    arch = hero_arch(data, hid)
    chosen = [x for x in (rate_augment(data, hid, a) for a in chosen_ids) if x]
    opts = []
    for aid in offered_ids:
        x = rate_augment(data, hid, aid)
        if not x:
            continue
        x["fit"], x["fit_reason"] = fit(x, chosen, arch)
        x["note"] = play_note(data, hid, x, chosen, arch)
        x["follow"] = followups(data, hid, x, chosen, arch)
        x["combo"], x["combo_info"] = combo_bonus(data, hid, x, chosen)
        x["tag_bonus"] = tag_bonus(x, chosen, arch) if chosen else 0.0
        x["final"] = x["score"] + x["combo"] + x["tag_bonus"]
        opts.append(x)
    if opts:
        max(opts, key=lambda o: o["final"])["best"] = True
        for k, o in enumerate(sorted(opts, key=lambda o: -o["final"])):
            o["order"] = k + 1  # 三个选项之间的推荐顺序（选项本身仍按屏幕上从左到右排列）
    return {"options": opts, "chosen": chosen}


def best_augments(data, hid, per=5):
    """没有三选一时的概览：该英雄各稀有度胜率最高的强化。"""
    out = []
    for r in range(3):
        rows = []
        for e in _hero_list(data, hid, RARITY_KEYS[r])[:per]:
            x = rate_augment(data, hid, e["augmentId"])
            if x:
                rows.append(x)
        out.append((RARITY_NAMES[r], rows))
    return out


def _item_states(data, items, owned, gold):
    """逐件标注 已拥有 / 买得起 / 还差多少（已有的合成部件抵扣价格）。返回 (items, 还需金币合计)。"""
    pool = list(owned)
    out, total_left = [], 0
    for it in items:
        iid = str(it["itemId"])
        info = data.item(iid) or {}
        price = info.get("price", 0)
        if iid in pool:
            pool.remove(iid)
            out.append({**it, "state": "owned", "price": price, "left": 0})
            continue
        left = price
        for comp in info.get("from", []):
            if comp in pool:
                pool.remove(comp)
                left -= (data.item(comp) or {}).get("price", 0)
        left = max(0, left)
        total_left += left
        out.append({**it, "state": "afford" if left <= gold else "todo", "price": price, "left": left})
    return out, total_left


def top_items_model(data, hid, owned, gold):
    """商店面板顶部：除鞋子外胜率最高的三件装备。"""
    return _item_states(data, data.top_items(hid, 3), owned, gold)[0]


def shop_model(data, hid, owned, gold, n_builds=3):
    """商店面板：每套核心出装逐件标注 已拥有 / 买得起 / 还差多少。owned 为 itemId 字符串列表。"""
    out = []
    for b in data.builds(hid, n_builds):
        items, total_left = _item_states(data, b["items"], owned, gold)
        out.append({"label": b.get("label", ""), "winRate": b.get("winRate", 0), "play": b.get("playCount"),
                    "pick": b.get("pickRate"), "items": items, "left": total_left})
    return out
