"""环境自检：换电脑后逐项检查运行条件，给出红/黄/绿状态和具体修复办法；并可导出诊断文件。"""
import ctypes
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request

import config
import winfx
from lcu import LCU

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
APP_VERSION = "1.0"


def _item(name, level, detail, fix=""):
    return {"name": name, "level": level, "detail": detail, "fix": fix}


def _ping(url, timeout=6):
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(1)
        return True, "%.1f 秒" % (time.time() - t)
    except Exception as e:  # noqa: BLE001
        return False, str(getattr(e, "reason", e))[:60]


def check_all(helper):
    out = []
    # 系统版本
    b = sys.getwindowsversion().build
    name = "Windows 11" if b >= 22000 else "Windows 10"
    if b < 10240:
        out.append(_item("系统版本", FAIL, "%s（build %d）过旧" % (platform.system(), b), "需要 Windows 10 或更新"))
    elif b < 19041:
        out.append(_item("系统版本", WARN, "%s build %d" % (name, b),
                         "低于 2004 版：面板无法从截屏中隐藏，商店画面识别可能受面板干扰。建议升级系统"))
    else:
        out.append(_item("系统版本", OK, "%s build %d" % (name, b)))
    # 管理员权限
    try:
        admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        admin = False
    out.append(_item("管理员权限", OK if admin else WARN, "已以管理员运行" if admin else "未以管理员运行",
                     "" if admin else "游戏/客户端以管理员运行时，本程序也需要管理员：右键 exe → 以管理员身份运行"))
    # PowerShell（读客户端端口、系统 OCR 都依赖它）
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.Major"],
                           capture_output=True, timeout=20, creationflags=0x08000000)
        ver = r.stdout.decode("utf-8", "ignore").strip()
        out.append(_item("PowerShell", OK if r.returncode == 0 and ver else FAIL,
                         "版本 %s" % ver if ver else "无法运行",
                         "" if ver else "本程序用它读取客户端端口和调用系统 OCR；请检查是否被组策略/安全软件禁用"))
    except Exception as e:  # noqa: BLE001
        out.append(_item("PowerShell", FAIL, "无法运行（%s）" % e, "请确认系统自带 PowerShell 未被卸载或禁用"))
    # 中文 OCR
    ocr = helper.ocr
    if not helper.settings["augment_ocr"]:
        out.append(_item("海克斯识别(OCR)", INFO, "已在设置中关闭"))
    elif ocr.ready:
        lang = ocr.lang or ""
        if lang.lower().startswith("zh"):
            out.append(_item("海克斯识别(OCR)", OK, "系统 OCR 就绪（%s）" % lang))
        else:
            out.append(_item("海克斯识别(OCR)", WARN, "OCR 语言是 %s，不是中文" % lang,
                             "设置 → 时间和语言 → 语言 → 添加“中文(简体)”并勾选“文字识别”，然后重启本程序"))
    elif ocr.error:
        out.append(_item("海克斯识别(OCR)", FAIL, ocr.error,
                         "设置 → 时间和语言 → 语言 → 添加“中文(简体)”并勾选“文字识别”；商店和选人功能不受影响"))
    else:
        out.append(_item("海克斯识别(OCR)", INFO, "尚未启动"))
    # 网络（并行检测各数据来源）
    hosts = [("主数据源 aramgg.com", "https://aramgg.com/data/champions-stats.json", "main"),
             ("强化详情/图标 cdn.dtodo.cn", "https://cdn.dtodo.cn/hextech/champion-details/875.json", "cdn"),
             ("英雄/装备 game.gtimg.cn", "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js", "base"),
             ("备用数据源 op.gg", "https://op.gg/lol/modes/aram-mayhem", "backup")]
    res = {}
    ths = [threading.Thread(target=lambda n=n, u=u: res.__setitem__(n, _ping(u)), daemon=True) for n, u, _ in hosts]
    [t.start() for t in ths]
    [t.join(12) for t in ths]
    has_cache = bool(helper.data.meta)
    ok_of = {k: res.get(n, (False, ""))[0] for n, _, k in hosts}
    for n, u, kind in hosts:
        ok, info = res.get(n, (False, "超时"))
        if ok:
            out.append(_item("网络 · " + n, OK, "可访问（%s）" % info))
            continue
        if kind == "main":
            lvl = WARN if (has_cache or ok_of["backup"]) else FAIL
            tip = "会自动改用备用数据源 op.gg 或本地缓存，但备用数据没有胜率" if lvl == WARN else "首次启动必须能访问其中一个数据源"
        elif kind == "base":
            lvl = WARN if has_cache else FAIL
            tip = "英雄名、头像、装备价格依赖它；有本地缓存时仍可使用"
        elif kind == "cdn":
            lvl, tip = WARN, "强化详情会改用备用源，图标可能显示为空"
        else:
            lvl, tip = INFO, "仅作备用，不影响正常使用"
        out.append(_item("网络 · " + n, lvl, "无法访问：" + info,
                         tip + "；若使用代理/加速器，请对相关域名放行"))
    # 数据
    m = helper.data.meta
    if not m:
        out.append(_item("推荐数据", FAIL, "尚未加载", "检查上面的网络项；联网后点“更新数据”"))
    else:
        age = (time.time() - m.get("fetched_at", 0)) / 3600
        upd = m.get("updated") or "?"
        try:
            days = (time.time() - time.mktime(time.strptime(upd, "%Y-%m-%d"))) / 86400
        except ValueError:
            days = 0
        src = helper.data.source_label()
        if m.get("source") != "aramgg":
            out.append(_item("推荐数据", WARN, "%s 版 · 当前使用备用源 %s · %.0f 小时前拉取" % (m.get("version"), src, age),
                             "主数据源不可用时只有梯队和综合分、没有胜率；恢复后点“更新数据”切回 aramgg.com"))
        elif days > 10:
            out.append(_item("推荐数据", WARN, "%s 版 · 数据日期 %s · 来源 %s" % (m.get("version"), upd, src),
                             "数据已超过 %d 天没更新，推荐可能过时" % days))
        else:
            out.append(_item("推荐数据", OK, "%s 版 · 数据日期 %s · 来源 %s · 本地缓存 %.0f 小时前拉取" % (
                m.get("version"), upd, src, age)))
    # 数据目录
    try:
        p = os.path.join(config.DATA_DIR, ".w")
        open(p, "w").close()
        os.remove(p)
        out.append(_item("数据目录", OK, config.DATA_DIR))
    except OSError as e:
        out.append(_item("数据目录", FAIL, "不可写：%s" % e, "把 exe 放到有写权限的文件夹（如桌面、D 盘）"))
    # 显示器 / DPI
    try:
        n = ctypes.windll.user32.GetSystemMetrics(80)
        w, h = winfx.screen_rect()[2:]
        dpi = ctypes.windll.user32.GetDpiForSystem() / 96
        out.append(_item("显示器", WARN if n > 1 else OK, "主屏 %dx%d · 缩放 %.0f%% · %d 个显示器" % (w, h, dpi * 100, n),
                         "有多个显示器时，请把游戏放在【主显示器】上，否则面板位置和画面识别会错位" if n > 1 else ""))
    except Exception:  # noqa: BLE001
        pass
    # 客户端 / 游戏（仅提示）
    out.append(_item("英雄联盟客户端", INFO, "已运行" if LCU.discover() else "未运行（启动客户端后会自动连接）"))
    rect = winfx.game_rect()
    if rect:
        out.append(_item("游戏窗口", OK, "找到（%dx%d）" % (rect[2], rect[3])))
    else:
        out.append(_item("游戏窗口", INFO, "未检测到对局（进入对局后自动识别；请使用窗口化/无边框模式，独占全屏无法识别画面）"))
    return out


def write_diagnostic(helper, checks):
    """导出诊断文件（不含客户端令牌等敏感信息），返回路径。"""
    p = os.path.join(config.DATA_DIR, "diagnostic.txt")
    lines = ["Hex Helper 诊断 v%s  %s" % (APP_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")),
             "打包运行: %s | Python %s | %s" % (config.FROZEN, platform.python_version(), platform.platform()),
             "数据目录: %s" % config.DATA_DIR, ""]
    for c in checks or []:
        lines.append("[%s] %s: %s%s" % (c["level"].upper(), c["name"], c["detail"], (" -> " + c["fix"]) if c["fix"] else ""))
    lines += ["", "---- 本机布局 ----"] + list(getattr(helper, "layout_notes", []) or ["（还没进过对局）"])
    lines += ["", "设置: %s" % helper.settings.v]
    try:
        with open(os.path.join(config.DATA_DIR, "ocr", "last_scan.txt"), encoding="utf-8", errors="ignore") as f:
            lines += ["", "---- 最近一次海克斯 OCR 识别到的文字 ----", f.read()[:1500]]
    except OSError:
        pass
    lines += ["", "---- 最近日志 ----"]
    try:
        with open(config.LOG_PATH, encoding="utf-8", errors="ignore") as f:
            lines += f.read().splitlines()[-120:]
    except OSError:
        lines.append("(无日志)")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return p
