"""路径与用户设置。exe 同目录下的 data/ 保存缓存、设置和日志，整个文件夹拷走即可携带。"""
import json
import os
import sys

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)  # 打包进 exe 的只读资源


def _data_dir():
    for base in (APP_DIR, os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "HexHelper")):
        d = os.path.join(base, "data")
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".w")
            open(probe, "w").close()
            os.remove(probe)
            return d
        except OSError:
            continue
    return APP_DIR


DATA_DIR = _data_dir()
CACHE_DIR = os.path.join(DATA_DIR, "cache")
LOG_PATH = os.path.join(DATA_DIR, "helper.log")

DEFAULTS = {
    "dock_side": "right",        # 对局内面板停靠在游戏画面的 left / right
    "ui_scale": 1.0,             # 面板缩放
    "panel_opacity": 0.93,
    "overlay_safe": True,        # 防闪烁模式：面板不透明、不用“截屏排除”、三选一期间几乎不读屏幕
                                 #（开启显卡动态刷新率 G-Sync/FreeSync 时，半透明悬浮窗 + 频繁读屏容易闪屏甚至驱动重置）
    "offer_panel_secs": 25,
    "check_update": True,        # 启动时到 GitHub 查一次有没有新版本（只提示，不会自动下载）      # 三选一推荐面板显示多少秒后自动收起（0 = 一直显示）；刷新出新选项会重新显示
    "panel_top": 0.06,           # 对局内面板的竖直范围（占画面高度）：上沿留出比分栏，
    "panel_bottom": 0.58,        # 下沿留出小地图/装备栏/技能栏（小地图最大约占下方 42%，左右放哪边都不会被压到）
    "shop_extent": {},           # 各分辨率下测得的商店面板左右边缘（自动记录，商店被拖动后会自动更新）
    "shop_key": "P",             # 游戏里的“打开商店”键（只读取按键状态；你改过键位时在 settings.json 里改这一项）
    "augment_ocr": True,         # 用系统自带 OCR 识别海克斯三选一
    "include_teammates": False,  # 选人阶段把队友英雄也列出来
    "any_mode": False,           # 非大乱斗也显示
    # 组合键，避免和游戏内按键冲突（游戏里几乎没有 Ctrl+Alt 组合）
    "hotkeys": {"shop": "Ctrl+Alt+S", "augment": "Ctrl+Alt+A", "undo": "Ctrl+Alt+Z", "hide": "Ctrl+Alt+H",
                "bigger": "Ctrl+Alt+]", "smaller": "Ctrl+Alt+[", "dock_left": "Ctrl+Alt+,", "dock_right": "Ctrl+Alt+."},
}


class Settings:
    def __init__(self):
        self.path = os.path.join(DATA_DIR, "settings.json")
        self.v = json.loads(json.dumps(DEFAULTS))
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
            for k, val in saved.items():
                if k == "hotkeys" and isinstance(val, dict):
                    old = {"F6", "F7", "F8", "F9", "F10"}  # 旧版默认的单键快捷键与游戏按键冲突，自动换成新默认
                    self.v["hotkeys"].update({a: b for a, b in val.items() if a in self.v["hotkeys"] and b not in old})
                elif k in DEFAULTS:
                    self.v[k] = val
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.v[k]

    def set(self, k, val):
        self.v[k] = val
        self.save()

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.v, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
