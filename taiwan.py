"""台服資料按遊戲 ID 與共用統計對接。"""
import ctypes
import json

from webutil import http_get

DD = "https://ddragon.leagueoflegends.com"
CD = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global"


def heroes():
    version = json.loads(http_get(DD + "/api/versions.json"))[0]
    cdn = DD + "/cdn/" + version
    rows = json.loads(http_get(cdn + "/data/zh_TW/champion.json"))["data"]
    return {h["key"]: {"title": h["name"], "name": h["title"], "alias": h["id"],
                       "avatar": cdn + "/img/champion/" + h["image"]["full"],
                       "splash": DD + "/cdn/img/champion/splash/" + h["id"] + "_0.jpg"}
            for h in rows.values()}


def items():
    rows = json.loads(http_get(CD + "/zh_tw/v1/items.json"))
    return {str(i["id"]): {"name": i["name"], "price": i["priceTotal"], "from": i.get("from") or [],
                           "icon": CD + "/default/" + i["iconPath"].lower().split("/assets/", 1)[-1]}
            for i in rows if i.get("name") and i.get("iconPath")}


def augments(catalog):
    tw = catalog("zh_tw")
    cn = catalog("zh_cn")
    for aid, row in tw.items():
        row["rules"] = cn.get(aid, {})
    return tw


def traditional(text):
    # Windows supplies this mapping even without the optional OCR language pack.
    if not text or text.isascii():
        return text
    mapper = ctypes.windll.kernel32.LCMapStringEx
    n = mapper("zh-TW", 0x04000000, text, -1, None, 0, None, None, 0)
    if n <= 0:
        return text
    out = ctypes.create_unicode_buffer(n)
    return out.value if mapper("zh-TW", 0x04000000, text, -1, out, n, None, None, 0) else text
