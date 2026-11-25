"""海斗榜公开快照。按版本和快照 ID 校验，避免混用不同批次。"""
import json
import re
import threading
import time
from functools import lru_cache

from webutil import http_get

BASE = "https://haidoubang.com/snapshots"
RARITIES = ("kSilver", "kGold", "kPrismatic")


def read(url):
    return json.loads(http_get(url, timeout=8, retries=0))


def snapshot(generation, patch, resource):
    data = read(f"{BASE}/{generation}/{patch}/{resource}")
    meta = data["meta"]
    if (meta["queue"], meta["patch"], meta["snapshotId"]) != (2400, patch, generation):
        raise ValueError("海斗榜版本与快照不一致")
    return data


def rank_rows(rows, identity):
    rows = sorted((r for r in rows if r["games"] > 0 and r.get("winRate") is not None),
                  key=lambda r: (-r["winRate"], -r["games"], int(r[identity])))
    for i, row in enumerate(rows, 1):
        # 海斗榜按两百分点展示胜率区间；映射为助手已有的 0–5 档。
        yield row, i, str(sum(row["winRate"] < edge for edge in (.56, .54, .52, .50, .48)))


class Haidou:
    def __init__(self):
        self._lock = threading.Lock()
        self._ref = None
        self._expires = 0

    def current(self, preferred=None):
        with self._lock:
            if self._ref is None or time.monotonic() >= self._expires:
                self._ref = read(BASE + "/current.json?schema=2")
                self._expires = time.monotonic() + 300
            manifest = self._ref
        current = manifest["defaultPatch"]
        patch = preferred if preferred and "." in preferred else current
        if patch.startswith("26."):
            patch = "16." + patch.split(".", 1)[1]
        entry = next((p for p in manifest["patches"] if p["patch"] == patch), None)
        if not entry:
            raise LookupError(f"海斗榜暂无 {patch} 快照")
        generation = entry["generation"]
        if not re.fullmatch(r"[\w-]+", generation) or not re.fullmatch(r"\d+\.\d+", patch):
            raise ValueError("无效的海斗榜快照")
        return generation, patch

    def summary(self):
        generation, patch = self.current()
        board = snapshot(generation, patch, "board.json?hero-ranking=all-v2&role-display=coverage-v1")
        total = board["meta"]["appearances"]
        rows = {str(r["championId"]): {"rank": rank, "tier": tier, "games": r["games"],
                                     "winRate": r["winRate"], "pickRate": r["games"] / total}
                for r, rank, tier in rank_rows(board["heroEntries"], "championId")}
        return rows, patch, board["meta"]["updatedAt"][:10]

    @staticmethod
    @lru_cache(maxsize=32)
    def _cohort(generation, patch, hid):
        data = snapshot(generation, patch, f"hero-cohorts/{int(hid)}/all.json")
        if str(data["championId"]) != hid or data.get("augmentId") or data.get("itemId"):
            raise ValueError("海斗榜英雄或筛选范围不符")
        return data["detail"]["groups"]

    def augments(self, hid, patch):
        groups = self._cohort(*self.current(patch), hid)
        return [{"aid": str(r["id"]), "tier": tier, "rank": rank,
                 "winRate": r.get("rawWinRate", r["winRate"]), "pickRate": r["pickRate"],
                 "playCount": r["games"], "perf": None}
                for rarity in RARITIES for r, rank, tier in rank_rows(groups.get(rarity, []), "id")]

    def items(self, hid, patch):
        groups = self._cohort(*self.current(patch), hid)
        return {kind: [{"id": str(r["id"]), "pickRate": r["pickRate"], "games": r["games"],
                        "winRate": r.get("rawWinRate", r["winRate"])}
                       for r in groups.get(kind, []) if str(r["id"]).isdigit() and r["games"] > 0
                       and r.get("winRate") is not None]
                for kind in ("items", "boots")}
