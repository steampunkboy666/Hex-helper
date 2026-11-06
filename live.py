"""游戏内实时数据：官方 Live Client Data API（https://127.0.0.1:2999，只读，游戏自带）。"""
import http.client
import json
import re
import ssl
import time

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


class LiveClient:
    def __init__(self):
        self.conn = None

    def snapshot(self):
        """返回对局快照；游戏没在运行（连不上）返回 None。
        {time, alias, champ_name, items:[itemId str], gold, level, mode, events}
        复用同一条连接（每秒重新握手 TLS 是不小的开销）。"""
        for attempt in (0, 1):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection("127.0.0.1", 2999, timeout=1.5, context=_CTX)
                self.conn.request("GET", "/liveclientdata/allgamedata")
                r = self.conn.getresponse()
                body = r.read()
                if r.status != 200:
                    return None
                return self.parse(json.loads(body))
            except (OSError, ValueError, http.client.HTTPException):
                try:
                    self.conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self.conn = None  # 长连接可能已被对端关闭：重连一次再判断
        return None

    @staticmethod
    def parse(j):
        me = j.get("activePlayer") or {}
        events = [e.get("EventName") for e in ((j.get("events") or {}).get("Events") or []) if isinstance(e, dict)]
        ids = {x for x in (me.get("riotId"), me.get("summonerName"), me.get("riotIdGameName")) if x}
        mine = None
        for p in j.get("allPlayers") or []:
            if ids & {p.get("riotId"), p.get("summonerName"), p.get("riotIdGameName")}:
                mine = p
                break
        if mine is None:
            return {"time": (j.get("gameData") or {}).get("gameTime", 0), "alias": None, "champ_name": None,
                    "items": [], "gold": me.get("currentGold", 0), "level": me.get("level", 1),
                    "mode": (j.get("gameData") or {}).get("gameMode"), "events": events}
        raw = mine.get("rawChampionName") or ""
        alias = re.sub(r"^game_character_displayname_", "", raw) or None
        items = []
        for it in mine.get("items") or []:
            items += [str(it.get("itemID"))] * max(1, it.get("count", 1))
        return {"time": (j.get("gameData") or {}).get("gameTime", 0), "alias": alias,
                "champ_name": mine.get("championName"), "items": items,
                "gold": me.get("currentGold", 0), "level": me.get("level", 1),
                "mode": (j.get("gameData") or {}).get("gameMode"), "events": events}


class DemoLive:
    """演示用：模拟一局进行中的数据（金币随时间增长，偶尔买装备）。"""

    def __init__(self, alias, items=None):
        self.t0, self.alias, self.items = time.time(), alias, list(items or [])

    def snapshot(self):
        t = time.time() - self.t0
        return {"time": t, "alias": self.alias, "champ_name": self.alias, "items": self.items,
                "gold": int(500 + t * 40) % 3600, "level": min(18, 1 + int(t // 20)), "mode": "KIWI"}
