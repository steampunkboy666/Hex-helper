"""界面：主题、图片加载、选人窗口、对局内点击穿透侧边面板、控制台窗口与托盘。"""
import hashlib
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRegion)
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                               QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget)

import advisor
import layout
import webutil
import winfx

FONT = "Microsoft YaHei UI"
GOLD, TXT, DIM, GREEN, RED = "#e6c068", "#eef3f6", "#8da3b0", "#5ee0a0", "#ff6b6b"
YELLOW = "#f0b44c"
BG = QColor(8, 16, 24)
RARITY_COL = {0: "#c9d1d8", 1: "#f0c24b", 2: "#ff7ad9"}
TIER_COL = {"0": "#f0c24b", "1": "#f0c24b", "2": "#5ee0a0", "3": "#6cc0ff", "4": "#9aa7b2", "5": "#7a7f87"}
TAG_TEXT = {"mine": "当前", "bench": "板凳", "team": "队友"}


def tier_text(t):
    return "OP" if str(t) == "0" else "T%s" % t


def pct(x, nd=1):
    return "—" if x is None else "%.*f%%" % (nd, x * 100)


def build_stats(b):
    """出装右侧说明：有场次显示场次，否则显示选取率。"""
    tail = "%d 场" % b["playCount"] if b.get("playCount") else "选取率 %s" % pct(b.get("pickRate"))
    return "胜率 %s" % pct(b.get("winRate")), tail


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- 图片加载（磁盘缓存 + 后台下载）
class ImageLoader(QObject):
    loaded = Signal(str)

    def __init__(self, cache_dir):
        super().__init__()
        self.dir = os.path.join(cache_dir, "img")
        os.makedirs(self.dir, exist_ok=True)
        self.pool, self.pending, self.mem = ThreadPoolExecutor(6), set(), {}

    def pixmap(self, url):
        if not url:
            return None
        if url in self.mem:
            return self.mem[url]
        path = os.path.join(self.dir, hashlib.sha1(url.encode()).hexdigest())
        if os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self.mem[url] = pm
                return pm
            os.remove(path)
        if url not in self.pending:
            self.pending.add(url)
            self.pool.submit(self._dl, url, path)
        return None

    def _dl(self, url, path):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": webutil.UA})
            with urllib.request.urlopen(req, timeout=15) as r, open(path + ".part", "wb") as f:
                f.write(r.read())
            os.replace(path + ".part", path)
            self.loaded.emit(url)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.pending.discard(url)


class Ctx:
    """界面上下文：数据、图片、缩放。auto 为随分辨率自动计算的缩放（每个窗口各用一个 Ctx）。"""

    def __init__(self, data, loader, settings):
        self.data, self.loader, self.settings = data, loader, settings
        self.auto = 1.0

    def S(self, x):
        return max(1, round(x * float(self.settings["ui_scale"]) * self.auto))


def screens_info():
    """所有显示器（主屏在前）：原点为物理像素，w/h 为逻辑尺寸，dpr 为显示缩放，avail 为去掉任务栏的可用区域。"""
    out = []
    primary = QGuiApplication.primaryScreen()
    for s in [primary] + [x for x in QGuiApplication.screens() if x is not primary]:
        g, a = s.geometry(), s.availableGeometry()
        out.append({"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(), "dpr": s.devicePixelRatio(),
                    "avail": (a.x(), a.y(), a.width(), a.height())})
    return out


def logical_game(rect):
    """游戏窗口（物理像素）→ (所在屏幕, 该屏幕逻辑坐标下的 (l,t,w,h))。多显示器、不同显示缩放下都正确。"""
    s = layout.pick_screen(rect, screens_info())
    return s, layout.to_logical(rect, s)


def auto_scale(logical_h):
    """以 2560x1440（100% 缩放）为基准：1080p 约 0.84、4K 约 1.27；用平缓曲线，小屏也保证字看得清。"""
    return max(0.8, min(1.6, (max(480, logical_h) / 1440.0) ** 0.6))


# ---------------------------------------------------------------- 基础控件
def lab(text, size=12, color=TXT, bold=False, wrap=False, width=None, align=None):
    w = QLabel(text)
    w.setStyleSheet("color:%s;font-family:'%s';font-size:%dpx;font-weight:%s;background:transparent;" % (
        color, FONT, size, "bold" if bold else "normal"))
    w.setTextFormat(Qt.RichText)
    w.setWordWrap(wrap)
    if width:
        w.setFixedWidth(width)
    if align:
        w.setAlignment(align)
    return w


class PixLabel(QWidget):
    """圆角图片：cover 填充；可加边框/变暗/角标。"""

    def __init__(self, ctx, url, w, h, radius=6, border=None, dim=False, badge=None, anchor=0.5):
        super().__init__()
        self.ctx, self.url, self.radius, self.border, self.dim, self.badge, self.anchor = \
            ctx, url, radius, border, dim, badge, anchor
        self.setFixedSize(w, h)
        ctx.loader.loaded.connect(self._on_loaded)

    def _on_loaded(self, url):
        if url == self.url:
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(20, 34, 44))
        pm = self.ctx.loader.pixmap(self.url)
        if pm:
            sc = pm.scaled(self.size() * self.devicePixelRatioF(), Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
            sc.setDevicePixelRatio(self.devicePixelRatioF())
            off = (sc.height() / self.devicePixelRatioF() - self.height()) * self.anchor
            p.drawPixmap(0, int(-off), sc)
        if self.dim:
            p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        p.setClipping(False)
        if self.border:
            p.setPen(QPen(QColor(self.border), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, self.radius, self.radius)
        if self.badge:
            d = max(14, self.width() // 3)
            p.setBrush(QColor(self.badge[1]))
            p.setPen(Qt.NoPen)
            p.drawEllipse(self.width() - d - 1, self.height() - d - 1, d, d)
            p.setPen(QColor("#06121a"))
            f = QFont(FONT, 1)
            f.setPixelSize(int(d * 0.72))
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(self.width() - d - 1, self.height() - d - 1, d, d), Qt.AlignCenter, self.badge[0])


class Banner(QWidget):
    """英雄卡片顶部：原画横幅 + 渐变 + 名字/梯队/排名。"""

    def __init__(self, ctx, hero, summ, tag, best, total, w, h):
        super().__init__()
        self.ctx, self.hero, self.summ, self.tag, self.best, self.total = ctx, hero, summ, tag, best, total
        self.setFixedSize(w, h)
        ctx.loader.loaded.connect(self._on_loaded)  # 绑定方法：卡片销毁时 Qt 会自动断开（lambda 不会，导致报错）

    def _on_loaded(self, url):
        if self.hero and url in (self.hero.get("splash"), self.hero.get("loading")):
            self.update()

    def paintEvent(self, _):
        S = self.ctx.S
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 8, 8)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(16, 28, 38))
        hero = self.hero
        pm = self.ctx.loader.pixmap(hero.get("splash") or hero.get("loading")) if hero else None
        if pm:
            sc = pm.scaled(self.size() * self.devicePixelRatioF(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            sc.setDevicePixelRatio(self.devicePixelRatioF())
            p.drawPixmap(0, int(-(sc.height() / self.devicePixelRatioF() - self.height()) * 0.18), sc)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, QColor(8, 16, 24, 235))
        g.setColorAt(0.55, QColor(8, 16, 24, 150))
        g.setColorAt(1, QColor(8, 16, 24, 70))
        p.fillRect(self.rect(), g)
        title = hero["title"] if hero else "英雄"
        f = QFont(FONT)
        f.setPixelSize(S(19))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(TXT))
        p.drawText(S(12), S(32), title)
        f.setPixelSize(S(11))
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(GOLD if self.best else DIM))
        p.drawText(S(12), S(52), "%s · %s%s" % (hero["name"] if hero else "", TAG_TEXT[self.tag],
                                                "   ★ 优先选" if self.best else ""))
        if self.summ:
            col = QColor(TIER_COL.get(self.summ["tier"], DIM))
            bw, bh = S(46), S(26)
            r = QRectF(self.width() - bw - S(12), S(12), bw, bh)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(r, 6, 6)
            f.setPixelSize(S(15))
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#0a1620"))
            p.drawText(r, Qt.AlignCenter, tier_text(self.summ["tier"]))
            f.setPixelSize(S(10))
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#c8d4dc"))
            p.drawText(QRectF(self.width() - S(120), S(42), S(108), S(16)), Qt.AlignRight,
                       "强度 %d/%d" % (self.summ["rank"], self.total))
        p.setClipping(False)
        p.setPen(QPen(QColor(GOLD if self.best else "#2c4a5a"), 2 if self.best else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)


class _Ctx0:
    """section_title 只用到 S()，控制台窗口不缩放。"""
    S = staticmethod(lambda x: x)


Ctx0 = _Ctx0()


def section_title(ctx, text):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, ctx.S(8), 0, ctx.S(2))
    lay.setSpacing(ctx.S(6))
    bar = QFrame()
    bar.setFixedSize(ctx.S(3), ctx.S(12))
    bar.setStyleSheet("background:%s;border-radius:1px;" % GOLD)
    lay.addWidget(bar)
    lay.addWidget(lab(text, ctx.S(11), GOLD, True))
    lay.addStretch()
    return w


def build_row(ctx, items, info_html, icon=40, name_w=60):
    """一行出装：图标+名称（含状态/价格）+右侧说明。items: [{name,icon,state,left}]"""
    S = ctx.S
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, S(2), 0, S(2))
    lay.setSpacing(S(4))
    for it in items:
        st = it.get("state")
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(S(1))
        border = GOLD if st == "afford" else None
        badge = ("✓", GREEN) if st == "owned" else None
        cl.addWidget(PixLabel(ctx, it.get("icon"), S(icon), S(icon), 6, border, st == "owned", badge), 0, Qt.AlignHCenter)
        nm = lab(esc(it.get("name", "")), S(10), DIM if st == "owned" else TXT, wrap=True, width=S(name_w),
                 align=Qt.AlignHCenter | Qt.AlignTop)
        cl.addWidget(nm)
        if st in ("afford", "todo"):
            cl.addWidget(lab("可购买" if st == "afford" else "还差 %d" % it.get("gap", it.get("left", 0)), S(10),
                             GREEN if st == "afford" else DIM, st == "afford", align=Qt.AlignHCenter), 0, Qt.AlignHCenter)
        lay.addWidget(col, 0, Qt.AlignTop)
    lay.addWidget(lab(info_html, S(11), DIM, wrap=False), 1, Qt.AlignVCenter)
    return w


def aug_line(ctx, rows):
    return "&nbsp;&nbsp;".join(
        "%s <span style='color:%s'>%s</span>" % (esc(x["name"]), DIM, "%.0f%%" % (x["wr"] * 100) if x["wr"] is not None
                                                 else tier_text(x["tier"])) for x in rows)


# ---------------------------------------------------------------- 选人阶段的英雄卡片
def age_text(ts, now=None):
    """时间戳 → “刚刚 / 25 分钟前 / 3 小时前 / 2 天前（11-07 21:30）”。"""
    if not ts:
        return "未知时间"
    secs = max(0, (now or time.time()) - ts)
    if secs < 120:
        rel = "刚刚"
    elif secs < 3600:
        rel = "%d 分钟前" % (secs // 60)
    elif secs < 86400:
        rel = "%d 小时前" % (secs // 3600)
    else:
        rel = "%d 天前" % (secs // 86400)
    return "%s（%s）" % (rel, time.strftime("%m-%d %H:%M", time.localtime(ts)))


def freshness(ctx, hid, short=False):
    """卡片上的“数据更新于 …”：绿色 = 12 小时内联网拿到的；黄色 = 更早的缓存；正在联网时标“更新中…”。"""
    d = ctx.data
    det = d.detail_cached(hid)
    ts = det["fetched_at"] if det else d.meta.get("fetched_at")
    busy = hid in d.refreshing or "meta" in d.refreshing
    stale = (det["stale"] if det else True) or d.meta_stale()
    col = YELLOW if stale else GREEN
    text = age_text(ts)
    if short:
        text = text.split("（")[0]
    tail = " · <span style='color:%s'>更新中…</span>" % GOLD if busy else (" · 旧数据" if stale else "")
    return lab("<span style='color:%s'>● 数据更新于 %s</span>%s" % (col, text, tail), ctx.S(10 if short else 11), DIM)


class HeroCard(QFrame):
    def __init__(self, ctx, hid, tag, best, compact):
        super().__init__()
        S, d = ctx.S, ctx.data
        hero, summ = d.hero(hid), d.summary(hid)
        self.setFixedWidth(S(346))
        self.setStyleSheet("HeroCard{background:rgba(14,26,36,255);border:1px solid %s;border-radius:10px;}" %
                           ("#a8863e" if best else "#25404f"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(S(8), S(8), S(8), S(8))
        lay.setSpacing(S(2))
        lay.addWidget(Banner(ctx, hero, summ, tag, best, d.meta["total"], S(330), S(72)))
        if summ and summ["winRate"] is not None:
            games = " &nbsp; %.1f万场" % (summ["games"] / 1e4) if summ.get("games") else ""
            lay.addWidget(lab("胜率 <b style='color:%s'>%.1f%%</b> &nbsp; 选取率 %s%s" % (
                GREEN if summ["winRate"] >= 0.5 else RED, summ["winRate"] * 100, pct(summ.get("pickRate"), 2), games),
                S(12), TXT))
        elif summ:
            lay.addWidget(lab("梯队 %s · 排名 %d（当前数据源 op.gg 不提供胜率）" % (tier_text(summ["tier"]), summ["rank"]),
                              S(12), DIM))
        else:
            lay.addWidget(lab("暂无该英雄的统计数据", S(12), DIM))
        det = d.detail_cached(hid)
        if not det:
            lay.addWidget(lab("正在获取出装与强化数据…", S(11), DIM))
            return
        lay.addWidget(freshness(ctx, hid))
        top = d.top_items(hid, 3)
        if top:
            lay.addWidget(section_title(ctx, "胜率最高的 3 件装备（不含鞋子）"))
            lay.addWidget(build_row(ctx, [{"name": i["name"], "icon": i["icon"]} for i in top], "<br>".join(
                "%.1f%% · %d场" % (i["winRate"] * 100, i["games"]) for i in top)))
        old_src = det.get("build_source") not in (None, "aramgg")
        lay.addWidget(section_title(ctx, "核心三件套（不含鞋子）" + ("（旧数据：douhunwiki.cn）" if old_src else "")))
        for b in d.builds(hid, 2 if compact else 3):
            items = [{"name": i["name"], "icon": i["icon"]} for i in b["items"]]
            wr, tail = build_stats(b)
            lay.addWidget(build_row(ctx, items, "%s<br><span style='color:%s'>%s</span><br>%s" % (
                esc(b.get("label", "")), GREEN if (b.get("winRate") or 0) >= .5 else DIM, wr, tail)))
        if not d.builds(hid, 1):
            lay.addWidget(lab("暂无出装数据（数据源暂不可用）", S(11), DIM))
        lay.addWidget(section_title(ctx, "推荐海克斯强化（该英雄胜率）"))
        n = 4 if compact else 5
        for key, name, rid in (("silver", "白银", 0), ("gold", "黄金", 1), ("prismatic", "棱彩", 2)):
            rows = [advisor.rate_augment(d, hid, e["augmentId"]) for e in det["augmentsByRarity"].get(key, [])[:n]]
            rows = [r for r in rows if r]
            if rows:
                lay.addWidget(lab("<b style='color:%s'>%s</b>&nbsp; %s" % (RARITY_COL[rid], name, aug_line(ctx, rows)),
                                  S(11), TXT, wrap=True))


class HeroRow(QFrame):
    """紧凑的一行：头像、梯队、胜率/排名、除鞋外胜率最高的 3 件装备、各稀有度最强的强化。客户端旁边空间小时使用。"""

    def __init__(self, ctx, hid, tag, best):
        super().__init__()
        S, d = ctx.S, ctx.data
        hero, summ = d.hero(hid), d.summary(hid)
        self.setFixedWidth(S(320))
        self.setStyleSheet("HeroRow{background:rgba(14,26,36,255);border:1px solid %s;border-radius:8px;}" %
                           ("#a8863e" if best else "#25404f"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(S(6), S(5), S(6), S(5))
        lay.setSpacing(S(6))
        badge = (tier_text(summ["tier"]), TIER_COL.get(summ["tier"], DIM)) if summ else None
        lay.addWidget(PixLabel(ctx, hero["avatar"] if hero else None, S(46), S(46), 8, GOLD if best else None,
                               badge=badge), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(S(1))
        if summ and summ["winRate"] is not None:
            stat = "<span style='color:%s'>%.1f%%</span> · 第%d" % (GREEN if summ["winRate"] >= .5 else RED,
                                                                  summ["winRate"] * 100, summ["rank"])
        elif summ:
            stat = "排名 %d" % summ["rank"]
        else:
            stat = "无数据"
        col.addWidget(lab("<b>%s</b> <span style='color:%s'>%s%s</span> &nbsp;%s" % (
            esc(hero["title"] if hero else hid), GOLD if best else DIM, TAG_TEXT[tag], " ★" if best else "", stat),
            S(12), TXT))
        det = d.detail_cached(hid)
        if not det:
            col.addWidget(lab("正在获取数据…", S(10), DIM))
        else:
            col.addWidget(freshness(ctx, hid, short=True))
            row = QHBoxLayout()
            row.setSpacing(S(2))
            for it in d.top_items(hid, 3):
                pl = PixLabel(ctx, it["icon"], S(22), S(22), 4)
                pl.setToolTip("%s 胜率 %.1f%%（%d 场）" % (it["name"], it["winRate"] * 100, it["games"]))
                row.addWidget(pl)
            row.addWidget(lab("除鞋胜率前三", S(9), DIM), 1)
            col.addLayout(row)
            augs = [rows[0] for _, rows in advisor.best_augments(d, hid, 1) if rows]
            col.addWidget(lab(" · ".join("<span style='color:%s'>%s</span>" % (RARITY_COL[a["rarity"]], esc(a["name"]))
                                         for a in augs), S(10), TXT, wrap=True, width=S(250)))
        lay.addLayout(col, 1)


class ChampSelectWindow(QWidget):
    """选人阶段：可拖动的悬浮窗，一排排英雄卡片。"""
    quit_requested = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.ctx, self.cards, self.collapsed, self.dismissed, self._drag = ctx, [], False, False, None
        self.report = None   # 由主程序设置：把摆放结果写进日志，方便排查“挡住客户端”之类的问题
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setObjectName("cs")
        self.frame.setStyleSheet("#cs{background:rgba(8,16,24,242);border:1px solid #a8863e;border-radius:12px;}")
        outer.addWidget(self.frame)
        self.lay = QVBoxLayout(self.frame)
        self.lay.setContentsMargins(10, 8, 10, 10)
        self.lay.setSpacing(6)
        self.head = QWidget()
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(4, 0, 4, 0)
        self.title = lab("选人参考", 13, TXT, True)
        self.info = lab("", 10, DIM)
        self.info.setWordWrap(True)
        hl.addWidget(self.title)
        hl.addStretch()
        for text, fn in (("收起", self.toggle), ("关闭", self.dismiss)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton{color:%s;background:rgba(255,255,255,0.06);border:none;border-radius:5px;"
                            "padding:3px 10px;font-family:'%s';font-size:11px;}QPushButton:hover{color:%s;"
                            "background:rgba(255,255,255,0.14);}" % (DIM, FONT, TXT))
            b.clicked.connect(fn)
            hl.addWidget(b)
        self.lay.addWidget(self.head)
        self.lay.addWidget(self.info)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.lay.addWidget(self.body)

    def set_cards(self, cards):
        self.cards = cards
        self.render()

    def render(self):
        """贴着客户端摆放：在客户端左侧、上方、右侧的空白里挑能放得最大的位置，按需缩小、调整列数；
        客户端占满屏幕没有空白时，才缩小后放在客户端左上角。用户拖动过就保持用户的位置。
        客户端在哪块显示器上就用哪块的可用区域和显示缩放。"""
        ctx, d = self.ctx, self.ctx.data
        screens = screens_info()
        cl = winfx.client_rect()
        scr = layout.pick_screen(cl, screens) if cl else screens[0]
        self._cl, self._avail = cl, scr["avail"]
        sx, sy, sw, sh = scr["avail"]
        n = max(1, len(self.cards))
        ctx.auto = auto_scale(sh) * 0.85 * getattr(self, "_shrink", 1.0)  # 选人窗口整体小一号
        compact = n > 2
        # 量出每张卡片的大小，取最大的（数据多的卡片更高，只量第一张会导致超出屏幕）
        cw, ch = ctx.S(354), ctx.S(300)
        if self.cards:
            sizes = []
            for c in self.cards:
                probe = HeroCard(ctx, c[0], c[1], c[2], compact)
                sizes.append(probe.sizeHint())
                probe.deleteLater()
            cw, ch = max(z.width() for z in sizes) + 8, max(z.height() for z in sizes) + 8
        head, pad = 64, 22
        rw_, rh_ = ctx.S(320) + 8, ctx.S(84) + 8   # 紧凑行的大小
        gap = 6
        if cl:
            l, t, w, h = layout.to_logical(cl, scr)
            regions = [("left", sx, sy, l - sx - gap, sh), ("top", sx, sy, sw, t - sy - gap),
                       ("right", l + w + gap, sy, sx + sw - (l + w + gap), sh)]
        else:
            l = t = w = h = None
            regions = [("left", sx, sy, sw * 0.5, sh)]

        def fit(cw_, ch_, regs):
            best = None
            for name, rx, ry, rw, rh in regs:
                if rw < 150 or rh < 120:
                    continue
                for cols in range(1, min(n, 6) + 1):
                    rows = -(-n // cols)
                    k = min(1.0, rw / (cols * cw_ + pad), rh / (rows * ch_ + head + pad))
                    if best is None or k > best[0] + 0.02:
                        best = (k, cols, name, rx, ry, rw, rh)
            return best

        # 优先完整卡片（缩小不超过 25%）；放不下就用紧凑行（缩小不超过 25%）；都不行才盖在客户端左上角
        best, rows_mode = fit(cw, ch, regions), False
        if best is None or best[0] < 0.75:
            alt = fit(rw_, rh_, regions)
            if alt and alt[0] >= 0.75:
                best, rows_mode = alt, True
            else:
                rx, ry = (l + 8, t + 48) if cl else (sx, sy)
                rw, rh = (w * 0.3, h - 60) if cl else (sw * 0.3, sh)
                alt = fit(rw_, rh_, [("overlay", rx, ry, rw, rh)])
                best, rows_mode = (max(alt[0], 0.7),) + alt[1:] if alt else (0.8, 1, "overlay", rx, ry, rw, rh), True
        k, cols, place, rx, ry, rw, rh = best
        ctx.auto *= k
        while self.grid.count():
            self.grid.takeAt(0).widget().deleteLater()
        for i, (cid, tag, top) in enumerate(self.cards):
            w_ = HeroRow(ctx, cid, tag, top) if rows_mode else HeroCard(ctx, cid, tag, top, compact)
            self.grid.addWidget(w_, i // cols, i % cols, Qt.AlignTop)
        self.grid.setSpacing(6 if rows_mode else 8)
        self.info.setText("%s 版 · %s · %s%s" % (d.meta.get("version"), d.meta.get("updated"), d.source_label(),
                                                "<br>悬停装备图标看胜率；空间大时显示完整卡片" if rows_mode else ""))
        self._place = (place, rx, ry, rw, rh, t, l)
        self.relayout()
        over = max(self.width() / max(1, sw - 10), self.height() / max(1, sh - 10))
        if over > 1.0 and getattr(self, "_shrink", 1.0) > 0.5:  # 仍超出屏幕（字体/图片撑大）：再整体缩小一次
            self._shrink = getattr(self, "_shrink", 1.0) / over * 0.97
            self.render()
            return
        self._shrink = 1.0
        if self.report:
            self.report("选人窗口：客户端 %s，放在“%s”，窗口 %dx%d，缩放 %.2f，%s" % (
                cl, place, self.width(), self.height(), ctx.auto, "紧凑列表" if rows_mode else "完整卡片"))

    def follow_client(self):
        """客户端被移动/改变大小后，窗口跟着重新摆放（用户手动拖过则不动）。"""
        if getattr(self, "_moved", False) or not self.isVisible():
            return
        cl = winfx.client_rect()
        old = getattr(self, "_cl", None)
        if (cl is None) != (old is None) or (cl and old and max(abs(a - b) for a, b in zip(cl, old)) > 12):
            self.render()

    def relayout(self):
        self.body.setVisible(not self.collapsed)
        self.adjustSize()
        if getattr(self, "_moved", False) or not getattr(self, "_place", None):
            return
        place, rx, ry, rw, rh, ct, cx = self._place
        W, H = self.width(), self.height()
        if place == "left":      # 右边缘贴住客户端左边缘，顶部与客户端对齐
            x, y = rx + rw - W, (ct if ct is not None else ry)
        elif place == "top":     # 下边缘贴住客户端上边缘，左侧与客户端对齐
            x, y = (cx if cx is not None else rx), ry + rh - H
        else:
            x, y = rx, (ct if ct is not None and place == "right" else ry)
        ax, ay, aw, ah = getattr(self, "_avail", None) or screens_info()[0]["avail"]
        x = min(max(ax, x), ax + aw - W)
        y = min(max(ay, y), ay + ah - H)
        self.move(int(x), int(y))

    def toggle(self):
        self.collapsed = not self.collapsed
        self.relayout()

    def dismiss(self):
        self.dismissed = True
        self.hide()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._moved = True

    def mouseReleaseEvent(self, _):
        self._drag = None


# ---------------------------------------------------------------- 对局内侧边面板（点击穿透）
class PanelFrame(QFrame):
    """面板底板：自己画圆角半透明底和金边（离屏渲染时样式表背景不可靠）。"""

    def __init__(self, alpha, opaque=False):
        super().__init__()
        self.alpha, self.opaque = alpha, opaque

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.opaque:  # 不透明：四角也填满底色，窗口不需要逐像素透明
            p.fillRect(self.rect(), QColor(8, 16, 24))
        p.setBrush(QColor(8, 16, 24, 255 if self.opaque else self.alpha))
        p.setPen(QPen(QColor(230, 192, 104, 150), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class SidePanel(QWidget):
    """停靠在游戏画面一侧；不抢焦点、鼠标穿透。

    性能要点（游戏里闪屏/掉帧的根源在这里）：内容在屏幕外的控件树里排版，渲染成一张图后，
    悬浮窗只负责把这张图画出来。窗口创建一次、位置/尺寸不变就不动它，内容没变就不重绘，
    因此不会出现“销毁重建控件 → 闪一下”，也不会反复 SetWindowPos 置顶。"""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus |
                            Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # 防闪烁模式下面板是不透明的普通窗口：逐像素透明的分层窗口盖在游戏上，
        # 在开启动态刷新率（G-Sync/FreeSync）时更容易让显卡在两种显示方式之间来回切换而闪屏
        self.safe = bool(ctx.settings["overlay_safe"])
        if not self.safe:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.frame, self._pm, self._digest, self._rect, self._pos = None, None, None, None, None
        self._styled = False
        self._fit, self._last, self._refit, self._mode = 1.0, None, False, "shop"
        self.extent, self._side, self._dpr = {}, "right", 1.0   # 实测的商店/卡片左右边缘；当前停靠侧；显示缩放
        self._scr, self._lg = screens_info()[0], (0, 0, 1920, 1080)
        self.phys_rect = None  # 面板在屏幕上的物理像素区域，画面识别时涂黑它
        self._later = QTimer(self, singleShot=True, interval=150)
        self._later.timeout.connect(self._present)
        ctx.loader.loaded.connect(lambda *_: self._later.start() if self.isVisible() else None)  # 图片陆续到达 → 合并重绘

    # 面板放大 1.3 倍，但宽度不超过“画面边缘到游戏界面之间的空白”。商店/三选一的左右边缘优先用实测值
    # （不同机器上商店可能被拖动过、界面缩放不同）；没测到时用默认值：三选一卡片外框约距画面中心 0.56 倍画面高度，
    # 商店面板约 0.52 倍（实测 2560x1440，界面按高度缩放）。
    BASE_W, BIGGER, MARGIN = 372, 1.3, 12
    CLEAR = {"offer": 0.56, "shop": 0.53, "ov": 0.53}

    def set_extent(self, mode, ext):
        """画面线程量到的左右边缘（占画面宽度的比例，某一侧可为 None）。变化明显时返回 True。"""
        old = self.extent.get(mode)
        if old and ext:   # 某一侧这次没量到就沿用上次的
            ext = tuple(n if n is not None else o for n, o in zip(ext, old))
        self.extent[mode] = ext
        if old is None or ext is None:
            return old != ext
        return any((a is None) != (b is None) or (a is not None and abs(a - b) > 0.012) for a, b in zip(old, ext))

    def _fresh(self, rect, mode="shop"):
        if self.frame is not None:
            self.frame.deleteLater()
        if not self._refit:
            self._fit = 1.0
        self._mode = mode
        scr, (l, t, w, h) = logical_game(rect)
        self._scr, self._lg = scr, (l, t, w, h)
        self._dpr = scr["dpr"]
        ext = self.extent.get(mode) or (None, None)
        half = self.CLEAR[mode] * h / w
        # 实测值只用来“扩大”避让范围（商店被拖动/界面缩放更大时），不用来缩小：
        # 面板很暗时前后对比可能只看出一部分，量小了会让面板压到商店上；量大了最多让面板窄一点。
        xl = min(ext[0], 0.5 - half) if ext[0] is not None else 0.5 - half
        xr = max(ext[1], 0.5 + half) if ext[1] is not None else 0.5 + half
        free = {"left": xl * w - self.MARGIN, "right": (1 - xr) * w - self.MARGIN}
        ui = float(self.ctx.settings["ui_scale"])
        want = auto_scale(h) * self.BIGGER
        wanted_w = self.BASE_W * ui * want
        pref = self.ctx.settings["dock_side"]
        other = "left" if pref == "right" else "right"
        # 偏好的一侧放得下就用它；放不下就换到空白更大的一侧（比如商店被拖到了偏好的那一侧）
        side = pref if free[pref] >= 0.8 * wanted_w or free[pref] >= free[other] else other
        self._side = side
        k = max(0.55, min(want, free[side] / (self.BASE_W * ui))) if free[side] > 0 else 0.55
        self.ctx.auto = k * self._fit
        S = self.ctx.S
        a = int(255 * float(self.ctx.settings["panel_opacity"]))
        self.frame = PanelFrame(a, self.safe)  # 无父控件、不显示：只用来排版和渲染
        self.frame.setFixedWidth(S(self.BASE_W))
        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(S(12), S(10), S(12), S(10))
        lay.setSpacing(S(3))
        return lay

    def paintEvent(self, _):
        if self._pm is not None:
            p = QPainter(self)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawPixmap(0, 0, self._pm)

    def _present(self):
        """把排好版的内容渲染成图；与上一张一致就什么都不做。"""
        if self.frame is None or self._rect is None:
            return
        fr = self.frame
        fr.layout().activate()
        fr.resize(fr.width(), fr.layout().sizeHint().height())
        dpr = self._dpr
        pm = QPixmap(int(fr.width() * dpr), int(fr.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(8, 16, 24) if self.safe else Qt.transparent)
        fr.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)  # 不画窗口底色：圆角外保持透明
        digest = hashlib.md5(bytes(pm.toImage().constBits())).digest() + bytes(self._side, "ascii")
        if digest == self._digest and self.isVisible():
            return
        self._digest, self._pm = digest, pm
        size = fr.size()
        if self.size() != size:
            self.setFixedSize(size)
        l, t, w, h = self._lg   # 游戏窗口在其所在屏幕上的逻辑坐标
        # 水平贴着游戏画面的左/右边缘；竖直方向固定在“比分栏下方”，不往下压到小地图/装备栏
        top = float(self.ctx.settings["panel_top"])
        x = l + w - size.width() - self.MARGIN if self._side == "right" else l + self.MARGIN
        y = t + h * top
        pos = (int(x), int(y))
        if pos != self._pos:
            self._pos = pos
            self.move(*pos)
        if not self.isVisible():
            if not self._styled:
                # 普通模式：不出现在截屏里（本程序截图时也会再涂黑一次）。防闪烁模式不用这个特殊显示属性，只靠涂黑
                winfx.overlay_style(int(self.winId()), click_through=True, exclude_capture=not self.safe)
                self._styled = True
            self.show()
            winfx.raise_topmost(int(self.winId()))
        nat = layout.to_native((pos[0], pos[1], size.width(), size.height()), self._scr)
        self.phys_rect = tuple(int(v) for v in nat)
        self.update()

    def dismiss(self):
        if self.isVisible():
            self.hide()
        self._digest, self.phys_rect = None, None

    def header(self, lay, hid, title, sub):
        S, d = self.ctx.S, self.ctx.data
        hero = d.hero(hid) if hid else None
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, S(2))
        rl.setSpacing(S(8))
        rl.addWidget(PixLabel(self.ctx, hero["avatar"] if hero else None, S(40), S(40), 8, GOLD))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(lab(title, S(15), TXT, True))
        col.addWidget(lab(sub, S(11), DIM))
        rl.addLayout(col, 1)
        lay.addWidget(row)

    # -- 商店
    def show_shop(self, hid, owned, gold, game_rect):
        self._last = lambda: self.show_shop(hid, owned, gold, game_rect)
        ctx, d, S = self.ctx, self.ctx.data, self.ctx.S
        lay = self._fresh(game_rect)
        hero = d.hero(hid) if hid else None
        summ = d.summary(hid) if hid else None
        if summ:
            sub = ("%s · 胜率 %s" % (tier_text(summ["tier"]), pct(summ["winRate"]))) if summ["winRate"] is not None \
                else "%s · 排名 %d/%d" % (tier_text(summ["tier"]), summ["rank"], d.meta["total"])
        else:
            sub = "数据 %s 版" % d.meta.get("version")
        self.header(lay, hid, "推荐出装 · %s" % (hero["title"] if hero else "未识别英雄"), "%s &nbsp;|&nbsp; 金币 %d" % (sub, gold))
        if not hid or not d.detail_cached(hid):
            lay.addWidget(lab("正在获取英雄数据…", S(12), DIM))
        else:
            top = advisor.top_items_model(d, hid, owned, gold)
            if top:
                lay.addWidget(section_title(ctx, "胜率最高的 3 件装备（不含鞋子）"))
                items = [dict(i, gap=max(0, i["left"] - gold)) for i in top]
                lay.addWidget(build_row(ctx, items, "<br>".join(
                    "<span style='color:%s'>%s</span> %.1f%% · %d场" % (GREEN if i["winRate"] >= .5 else RED,
                                                                       esc(i["name"]), i["winRate"] * 100, i["games"])
                    for i in top), icon=44, name_w=64))
                lay.addWidget(section_title(ctx, "核心三件套（不含鞋子）"))
            for b in advisor.shop_model(d, hid, owned, gold, 3):
                items = [dict(i, gap=max(0, i["left"] - gold)) for i in b["items"]]
                left = "全部已购" if b["left"] == 0 else "还需 %d 金币" % b["left"]
                wr, tail = build_stats({"winRate": b["winRate"], "playCount": b["play"], "pickRate": b["pick"]})
                lay.addWidget(build_row(ctx, items, "%s<br><span style='color:%s'>%s</span><br>%s<br>%s" % (
                    esc(b["label"]), GREEN if (b["winRate"] or 0) >= .5 else DIM, wr, tail, left), icon=44, name_w=64))
        det = d.detail_cached(hid) if hid else None
        old = det and det.get("build_source") not in (None, "aramgg")
        lay.addWidget(lab("绿色√ 已拥有 · 金边 当前买得起 · %s" % (
            "出装来自 douhunwiki.cn 的旧数据（主源对该英雄无出装）" if old else "装备与三件套胜率来自 op.gg（不含鞋子）"),
            S(10), DIM, wrap=True))
        self._place(game_rect)

    # -- 海克斯
    def show_offer(self, hid, ev, game_rect):
        self._last = lambda: self.show_offer(hid, ev, game_rect)
        ctx, d, S = self.ctx, self.ctx.data, self.ctx.S
        lay = self._fresh(game_rect, "offer")
        hero = d.hero(hid) if hid else None
        self.header(lay, hid, "海克斯强化 · %s" % (hero["title"] if hero else "未识别英雄"),
                    "已选 %d 个 &nbsp;|&nbsp; %s" % (len(ev["chosen"]), "评分取自该英雄的选取胜率" if d.meta.get("source") == "aramgg"
                                                  else "评分取自 %s 梯队（无胜率）" % d.source_label()))
        self._n_opts = len(ev["options"])
        if ev["options"]:
            rank = sorted(ev["options"], key=lambda o: o["order"])
            lay.addWidget(lab("推荐顺序：" + " &gt; ".join("<b style='color:%s'>%s</b>" % (GOLD if i == 0 else TXT, esc(o["name"]))
                                                       for i, o in enumerate(rank)), S(12), TXT, wrap=True))
        for i, o in enumerate(ev["options"]):
            lay.addWidget(self._option(o, i + 1))
        if ev["chosen"]:
            lay.addWidget(section_title(ctx, "已选强化"))
            lay.addWidget(lab("&nbsp;&nbsp;".join("<span style='color:%s'>%s</span> %s" % (
                RARITY_COL[c["rarity"]], esc(c["name"]),
                pct(c["wr"], 0) if c["wr"] is not None else tier_text(c["tier"]))
                for c in ev["chosen"]), S(11), TXT, wrap=True, width=S(346)))
        lay.addWidget(lab("综合 = 单项胜率 + 组合加分（该英雄高胜率三件组合，真实统计）+ 联动（关键词推断）"
                          + ("· 套路说明按强化描述推断，出装为该英雄真实统计" if ctx.settings["aug_notes"] else "")
                          + " · 选错了可按撤销键",
                          S(10), DIM, wrap=True))
        self._place(game_rect)

    def _option(self, o, idx):
        ctx, S = self.ctx, self.ctx.S
        box = QFrame()
        box.setObjectName("opt")
        best = o.get("best")
        box.setStyleSheet("#opt{background:%s;border:1px solid %s;border-radius:8px;}" % (
            "rgba(230,192,104,0.10)" if best else "rgba(255,255,255,0.04)", "#a8863e" if best else "#25404f"))
        bl = QHBoxLayout(box)
        bl.setContentsMargins(S(8), S(6), S(8), S(6))
        bl.setSpacing(S(8))
        bl.addWidget(PixLabel(ctx, o["icon"], S(46), S(46), 8, RARITY_COL[o["rarity"]]), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(S(1))
        tcol = TIER_COL.get(o["tier"], DIM)
        rank = "第%d/%d" % (o["rank"], o["total"]) if o["rank"] else ""
        pos = {1: "左", 2: "中", 3: "右"}.get(idx, str(idx)) if self._n_opts == 3 else str(idx)
        order = o.get("order")
        col.addWidget(lab("<span style='color:%s'>%s</span> &nbsp;<b>%s</b> &nbsp;<span style='color:%s;font-size:%dpx'>%s</span>%s" % (
            DIM, pos, esc(o["name"]), RARITY_COL[o["rarity"]], S(10), o["rname"],
            "&nbsp; <b style='color:%s'>%s</b>" % (GOLD if best else DIM, "★ 首选" if best else "第%d选" % order)
            if order else ""), S(14)))
        if o["source"] != "hero":
            stat = "<span style='color:%s'>暂无该英雄的统计</span>" % DIM
        elif o["wr"] is not None:
            stat = "胜率 <b style='color:%s'>%.1f%%</b> &nbsp; <span style='color:%s'>%s</span> &nbsp; %s" % (
                GREEN if o["wr"] >= .5 else RED, o["wr"] * 100, tcol, tier_text(o["tier"]), rank)
        else:
            stat = "梯队 <b style='color:%s'>%s</b> &nbsp; %s%s &nbsp;<span style='color:%s'>(op.gg 无胜率)</span>" % (
                tcol, tier_text(o["tier"]), rank, " &nbsp; 综合分 %.0f" % o["perf"] if o["perf"] else "", DIM)
        col.addWidget(lab(stat, S(12), TXT))
        if o.get("final") is not None:
            parts = ["单项 %.1f" % (o["score"] * 100)]
            if o.get("combo"):
                parts.append("<span style='color:%s'>组合 +%.1f</span>" % (GREEN, o["combo"] * 100))
            if o.get("tag_bonus"):
                parts.append("<span style='color:%s'>联动 %+.1f</span>" % (GREEN if o["tag_bonus"] > 0 else RED,
                                                                          o["tag_bonus"] * 100))
            col.addWidget(lab("综合 <b>%.1f</b> = %s" % (o["final"] * 100, " ".join(parts)), S(11), TXT))
        ci = o.get("combo_info")
        if ci:
            have = "已选 %s，" % "+".join(esc(x) for x in ci["have"]) if ci["have"] else ""
            need = [x for x in ci["others"] if x not in ci["have"]]
            col.addWidget(lab("<span style='color:%s'>★ 组合</span> %s%s → 胜率 <b>%.1f%%</b>（%d 场）" % (
                GOLD, have, ("再拿 " + "+".join(esc(x) for x in need)) if need else "已凑齐", ci["winRate"] * 100,
                ci["games"]), S(11), TXT, wrap=True, width=S(268)))
        fc = {"高": GREEN, "中": GOLD, "一般": DIM}[o["fit"]]
        note = o.get("note") if ctx.settings["aug_notes"] else None
        if note:
            style = "适合 <b style='color:%s'>%s</b>" % (GOLD, " / ".join(note["styles"])) if note["styles"] \
                else "<span style='color:%s'>通用效果，不挑套路</span>" % DIM
            col.addWidget(lab("适配 <b style='color:%s'>%s</b> &nbsp;%s" % (fc, o["fit"], style), S(11), TXT,
                              wrap=True, width=S(268)))
            for s in note["links"]:
                col.addWidget(lab("<span style='color:%s'>↔</span> %s" % (GREEN, esc(s)), S(11), TXT,
                                  wrap=True, width=S(268)))
            if note["warn"]:
                col.addWidget(lab("<span style='color:%s'>⚠ %s</span>" % (YELLOW, esc(note["warn"])), S(11), TXT,
                                  wrap=True, width=S(268)))
            b = note["build"]
            if b:
                col.addWidget(lab("<span style='color:%s'>出装</span> %s%s：%s" % (
                    DIM, "走%s路线" % b["label"],
                    "（胜率 %.0f%%）" % (b["winRate"] * 100) if b.get("winRate") else "",
                    " → ".join(esc(n) for n in b["items"])), S(11), TXT, wrap=True, width=S(268)))
            elif note.get("nobuild"):
                col.addWidget(lab("出装 这个英雄很少走对应路线，选了仍按常用出装", S(11), DIM, wrap=True, width=S(268)))
        else:
            col.addWidget(lab("适配 <b style='color:%s'>%s</b> <span style='color:%s'>%s</span>" % (
                fc, o["fit"], DIM, esc(o["fit_reason"])), S(11), TXT, wrap=True, width=S(268)))
        if o["follow"]:
            col.addWidget(lab("后续可搭配：" + " · ".join("<span style='color:%s'>%s%s</span>" % (
                RARITY_COL[f["rarity"]], esc(f["name"]), "(组合)" if f.get("combo_mate") else "")
                for f in o["follow"]), S(11), DIM, wrap=True, width=S(268)))
        bl.addLayout(col, 1)
        return box

    def show_overview(self, hid, game_rect):
        self._last = lambda: self.show_overview(hid, game_rect)
        ctx, d, S = self.ctx, self.ctx.data, self.ctx.S
        lay = self._fresh(game_rect, "ov")
        hero = d.hero(hid) if hid else None
        self.header(lay, hid, "强化速查 · %s" % (hero["title"] if hero else "未识别英雄"), "该英雄各稀有度胜率最高的海克斯")
        for name, rows in advisor.best_augments(d, hid, 5):
            if rows:
                rid = rows[0]["rarity"]
                lay.addWidget(lab("<b style='color:%s'>%s</b>" % (RARITY_COL[rid], name), S(12)))
                lay.addWidget(lab(aug_line(ctx, rows), S(11), TXT, wrap=True))
        self._place(game_rect)

    def _place(self, rect):
        self._rect = rect
        fr = self.frame
        fr.layout().activate()
        h = fr.layout().sizeHint().height()
        room = (float(self.ctx.settings["panel_bottom"]) - float(self.ctx.settings["panel_top"])) * self._lg[3]
        if h > room and self._fit > 0.55 and self._last:  # 内容比可用高度高：按比例缩小后重排一次
            self._fit *= max(0.55, room / h)
            self._refit = True
            try:
                self._last()
            finally:
                self._refit = False
            return
        self._present()


# ---------------------------------------------------------------- 控制台窗口与托盘
def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    import math
    path = QPainterPath()
    for i in range(6):
        a = math.radians(60 * i - 30)
        pt = QPoint(int(32 + 29 * math.cos(a)), int(32 + 29 * math.sin(a)))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor("#f3d98b"))
    g.setColorAt(1, QColor("#b08a3a"))
    p.setBrush(QBrush(g))
    p.setPen(QPen(QColor("#5c4518"), 2))
    p.drawPath(path)
    f = QFont(FONT)
    f.setPixelSize(30)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#10202a"))
    p.drawText(pm.rect(), Qt.AlignCenter, "海")
    p.end()
    return QIcon(pm)


class ControlWindow(QWidget):
    toggle_side = Signal()
    scale_changed = Signal(float)
    preview = Signal()
    refresh_data = Signal()
    run_check = Signal()
    export_diag = Signal()
    quit_requested = Signal()

    def __init__(self, settings, hotkeys):
        super().__init__()
        self.setWindowTitle("Hex Helper · 海克斯大乱斗助手")
        self.setWindowIcon(make_icon())
        self.setMinimumWidth(520)
        self.setStyleSheet("QWidget{background:#0c1a24;color:%s;font-family:'%s';font-size:13px;}"
                           "QPushButton{background:#17303e;border:1px solid #2c4a5a;border-radius:6px;padding:6px 12px;}"
                           "QPushButton:hover{background:#1f4152;border-color:%s;}"
                           "QPlainTextEdit{background:#08121a;border:1px solid #1d3441;border-radius:6px;color:#9fb6c3;"
                           "font-size:11px;}" % (TXT, FONT, GOLD))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        lay.addWidget(lab("Hex Helper <span style='font-size:13px;color:#8da3b0'>海克斯大乱斗助手</span>", 20, GOLD, True))
        lay.addWidget(lab("保持本程序运行即可：选人时自动弹出推荐，进游戏后打开商店/出现海克斯三选一时自动在画面一侧提示。", 12, DIM, wrap=True))
        about = lab("作者 steampunk boy · <a style='color:%s' href='https://github.com/steampunkboy666/Hex-helper'>github.com/steampunkboy666/Hex-helper</a>"
                    " · 许可 CC BY-NC-ND 4.0：可免费使用和原样分享，禁止商用，未经授权不得发布修改版" % GOLD, 11, DIM, wrap=True)
        about.setOpenExternalLinks(True)
        lay.addWidget(about)
        self.update_lab = lab("", 12, GOLD, wrap=True)   # 发现新版本时才显示
        self.update_lab.setOpenExternalLinks(True)
        self.update_lab.hide()
        lay.addWidget(self.update_lab)
        self.rows = {}
        for key, name in (("data", "数据"), ("client", "客户端"), ("game", "游戏"), ("shop", "商店面板"), ("ocr", "海克斯识别")):
            r = QHBoxLayout()
            r.addWidget(lab(name, 13, DIM, width=84))
            v = lab("…", 13, TXT, wrap=True)
            r.addWidget(v, 1)
            r.setAlignment(Qt.AlignTop)
            self.rows[key] = v
            lay.addLayout(r)
        hk = settings["hotkeys"]
        lay.addWidget(lab("商店面板：按游戏里的商店键（%s）打开商店时自动出现，再按 %s 或 Esc 关闭时消失（聊天打字时不算）。"
                          "用鼠标点商店按钮开关时，请用 <b>%s</b> 手动开关。" % (settings["shop_key"], settings["shop_key"], hk["shop"]),
                          12, TXT, wrap=True))
        lay.addWidget(lab("其他快捷键（都是组合键，不会和游戏按键冲突）：<b>%s</b> 强化速查 &nbsp; <b>%s</b> 撤销上一个已选强化 &nbsp; "
                          "<b>%s</b> 隐藏/显示全部面板" % (hk["augment"], hk["undo"], hk["hide"]), 12, DIM, wrap=True))
        lay.addWidget(lab("游戏里调整面板：<b>%s</b> 放大 &nbsp; <b>%s</b> 缩小 &nbsp; <b>%s</b> 放左边 &nbsp; <b>%s</b> 放右边。"
                          "面板会自动避开商店和三选一卡片，位置/大小在不同电脑上自动适应。" % (
                              hk["bigger"], hk["smaller"], hk["dock_left"], hk["dock_right"]), 12, DIM, wrap=True))
        btns = QHBoxLayout()
        for text, sig in (("预览效果", self.preview), ("面板换边", self.toggle_side), ("面板 −", None), ("面板 ＋", None),
                          ("更新数据", self.refresh_data)):
            b = QPushButton(text)
            if text == "面板 −":
                b.clicked.connect(lambda: self.scale_changed.emit(-0.1))
            elif text == "面板 ＋":
                b.clicked.connect(lambda: self.scale_changed.emit(0.1))
            else:
                b.clicked.connect(sig.emit)
            btns.addWidget(b)
        lay.addLayout(btns)
        lay.addWidget(section_title(Ctx0, "环境自检"))
        self.check_box = QVBoxLayout()
        self.check_box.setSpacing(2)
        lay.addLayout(self.check_box)
        self.check_box.addWidget(lab("正在自检…", 12, DIM))
        cb = QHBoxLayout()
        for text, sig in (("重新自检", self.run_check), ("导出诊断文件", self.export_diag)):
            b = QPushButton(text)
            b.clicked.connect(sig.emit)
            cb.addWidget(b)
        cb.addStretch()
        lay.addLayout(cb)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setFixedHeight(100)
        lay.addWidget(self.log)
        row = QHBoxLayout()
        b = QPushButton("退出程序")
        b.clicked.connect(self.quit_requested.emit)
        row.addStretch()
        row.addWidget(b)
        lay.addLayout(row)

    def set_checks(self, checks):
        while self.check_box.count():
            w = self.check_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        col = {"ok": GREEN, "warn": GOLD, "fail": RED, "info": DIM}
        for c in checks:
            html = "<span style='color:%s'>●</span> <b>%s</b>　%s" % (col[c["level"]], esc(c["name"]), esc(c["detail"]).replace(chr(92), chr(92) + chr(0x200b)))  # 允许在路径反斜杠处换行
            if c["fix"] and c["level"] in ("warn", "fail"):
                html += "<br><span style='color:%s'>　→ %s</span>" % (col[c["level"]], esc(c["fix"]))
            self.check_box.addWidget(lab(html, 12, TXT, wrap=True))
        bad = sum(1 for c in checks if c["level"] == "fail")
        warn = sum(1 for c in checks if c["level"] == "warn")
        self.adjustSize()
        return bad, warn

    def set_status(self, key, text, ok=None):
        if key == "update":   # text 形如 "v1.2|https://github.com/.../releases/tag/v1.2"
            tag, _, url = text.partition("|")
            self.update_lab.setText("发现新版本 %s，<a style='color:%s' href='%s'>点这里到 GitHub 下载</a>" % (
                esc(tag), GOLD, esc(url)))
            self.update_lab.show()
            return
        col = {True: GREEN, False: RED, None: TXT}[ok]
        self.rows[key].setText("<span style='color:%s'>●</span> %s" % (col, esc(text)) if ok is not None else esc(text))

    def add_log(self, line):
        self.log.appendPlainText(line)

    def closeEvent(self, e):  # 关闭窗口只是收到托盘，程序继续运行
        e.ignore()
        self.hide()


class Tray(QSystemTrayIcon):
    def __init__(self, control, app_quit):
        super().__init__(make_icon())
        self.setToolTip("Hex Helper · 海克斯大乱斗助手")
        menu = QMenu()
        menu.addAction("打开控制台", lambda: (control.show(), control.raise_(), control.activateWindow()))
        menu.addSeparator()
        menu.addAction("退出", app_quit)
        self.setContextMenu(menu)
        self.activated.connect(lambda r: (control.show(), control.raise_()) if r == QSystemTrayIcon.Trigger else None)
        self._menu = menu
