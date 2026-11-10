"""赛后战报：读取客户端的赛后统计（/lol-end-of-game/v1/eog-stats-block），给每个人算“大腿分”。

大腿分（0~100，只在这一局的 10 个人之间比较）：
  输出 40% · 参团（击杀+助攻）25% · 承伤 15% · 治疗/护盾队友 10% · 少死 10%
各项都按本局最高的那个人归一化。胜方最高分 = MVP，败方最高分 = 虽败犹荣。
"""
import time

WEIGHTS = (("dmg", 0.40), ("ka", 0.25), ("taken", 0.15), ("heal", 0.10), ("alive", 0.10))


def _stat(st, *keys):
    for k in keys:
        v = st.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _champ_id(data, p):
    cid = p.get("championId")
    if cid:
        return str(cid)
    h = data.hero_by_alias(p.get("championName")) if data and p.get("championName") else None
    return str(h) if h else None


def _is_local(p, local):
    if p.get("isLocalPlayer"):
        return True
    for k in ("puuid", "summonerId"):
        if local.get(k) and p.get(k) == local.get(k):
            return True
    return False


def parse_eog(block, data=None):
    """把客户端的赛后统计整理成战报；数据不完整返回 None。"""
    if not block or not block.get("teams"):
        return None
    local = block.get("localPlayer") or {}
    teams, everyone = [], []
    for t in block["teams"]:
        players = []
        for p in t.get("players") or []:
            st = p.get("stats") or {}
            heal_team = _stat(st, "TOTAL_HEAL_ON_TEAMMATES") + _stat(st, "TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES")
            row = {
                "cid": _champ_id(data, p),
                "name": p.get("riotIdGameName") or p.get("gameName") or p.get("summonerName") or "",
                "k": int(_stat(st, "CHAMPIONS_KILLED", "kills")),
                "d": int(_stat(st, "NUM_DEATHS", "deaths")),
                "a": int(_stat(st, "ASSISTS", "assists")),
                "dmg": _stat(st, "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", "totalDamageDealtToChampions"),
                "taken": _stat(st, "TOTAL_DAMAGE_TAKEN", "totalDamageTaken"),
                "heal": heal_team or _stat(st, "TOTAL_HEAL", "totalHeal"),
                "gold": _stat(st, "GOLD_EARNED", "goldEarned"),
                "local": _is_local(p, local),
            }
            players.append(row)
            everyone.append(row)
        if players:
            teams.append({"win": bool(t.get("isWinningTeam")), "players": players,
                          "mine": bool(t.get("isPlayerTeam")) or any(r["local"] for r in players)})
    if not everyone:
        return None
    score_all(everyone)
    for t in teams:
        t["players"].sort(key=lambda r: -r["score"])
        for i, r in enumerate(t["players"]):
            r["rank"] = i + 1
        top = t["players"][0]
        top["badge"] = "MVP" if t["win"] else "虽败犹荣"
    for key, name in (("dmg", "输出最高"), ("taken", "承伤最高"), ("heal", "奶量最高")):
        best = max(everyone, key=lambda r: r[key])
        if best[key] > 0:
            best.setdefault("titles", []).append(name)
    teams.sort(key=lambda t: not t["mine"])          # 我方在前
    me = next((r for r in everyone if r["local"]), None)
    mine = teams[0] if teams[0]["mine"] else None
    return {"teams": teams, "me": me, "win": mine["win"] if mine else None,
            "length": int(_stat(block, "gameLength")), "game_id": block.get("gameId"),
            "mode": block.get("gameMode") or "", "time": time.time()}


def score_all(rows):
    mx = {k: max([r[k] for r in rows] + [0]) for k in ("dmg", "taken", "heal")}
    mx["ka"] = max([r["k"] + r["a"] for r in rows] + [0])
    max_d = max([r["d"] for r in rows] + [0])
    for r in rows:
        part = {"dmg": r["dmg"] / mx["dmg"] if mx["dmg"] else 0,
                "ka": (r["k"] + r["a"]) / mx["ka"] if mx["ka"] else 0,
                "taken": r["taken"] / mx["taken"] if mx["taken"] else 0,
                "heal": r["heal"] / mx["heal"] if mx["heal"] else 0,
                "alive": 1 - r["d"] / max_d if max_d else 1}
        r["score"] = round(100 * sum(part[k] * w for k, w in WEIGHTS), 1)
    return rows


def verdict(rep):
    """一句话总结：我是不是大腿。"""
    me = rep.get("me")
    if not me:
        return "本局战报"
    if me.get("badge") == "MVP":
        return "这把你就是大腿！"
    if me.get("badge") == "虽败犹荣":
        return "虽败犹荣，你已经尽力了"
    if me["rank"] <= 2:
        return "队内第 %d，稳定发挥" % me["rank"]
    return "队内第 %d，下把加油" % me["rank"]
