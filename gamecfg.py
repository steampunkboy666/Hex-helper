"""读取游戏自己的界面设置（只读文件，不碰游戏进程）：小地图大小/位置、界面缩放、商店窗口位置等。
不同机器上这些设置不一样。目前只用来写进日志/诊断文件，方便核对“面板为什么挡住了某处”；
面板的避让不依赖它（见 ui.SidePanel：固定留出画面下方给小地图和装备栏，商店/卡片的位置从画面里实测）。"""
import json
import os
import re

_WANT = re.compile(r"minimap|scale|itemshop|hudflip|flip|swap|mirror|screenmode|windowmode|resolution", re.I)


def _from_ini(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.]+)\s*=\s*(\S.*?)\s*$", line)
        if m and _WANT.search(m.group(1)):
            out[m.group(1)] = m.group(2)
    return out


def _from_json(obj, out):
    """PersistedSettings.json：{"files":[{"name":..,"sections":[{"name":..,"settings":[{"name":..,"value":..}]}]}]}，
    结构不确定，递归找所有带 name/value 的项。"""
    if isinstance(obj, dict):
        if "name" in obj and "value" in obj and isinstance(obj["name"], str) and _WANT.search(obj["name"]):
            out[obj["name"]] = str(obj["value"])
        for v in obj.values():
            _from_json(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _from_json(v, out)


def read_game_settings(exe_path):
    """exe_path：游戏进程的路径（…\\Game\\League of Legends.exe）。返回 {设置名: 值}，读不到返回 {}。"""
    out = {}
    if not exe_path:
        return out
    d = os.path.dirname(exe_path)
    for base in (d, os.path.dirname(d), os.path.dirname(os.path.dirname(d))):
        cfg = os.path.join(base, "Config")
        if not os.path.isdir(cfg):
            continue
        for name in ("game.cfg", "PersistedSettings.json"):
            p = os.path.join(cfg, name)
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                if name.endswith(".json"):
                    _from_json(json.loads(text), out)
                else:
                    out.update(_from_ini(text))
            except (OSError, ValueError):
                continue
        if out:
            break
    return dict(sorted(out.items())[:40])
