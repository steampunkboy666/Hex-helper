"""主控制器：把客户端(LCU)、对局实时数据、画面识别、快捷键和各个悬浮窗串起来。"""
import argparse
import logging
import os
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QLockFile, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

import advisor
import config
import gamecfg
import layout
import selfcheck
import ui
import vision
import winfx
from data_source import Data
from lcu import LCU, DemoLCU, LCUError
from live import DemoLive, LiveClient

ARAM_QUEUES = (450, 2400)
NON_ARAM_MODES = ("CLASSIC", "CHERRY", "TUTORIAL", "PRACTICETOOL", "TFT")  # 召唤师峡谷/斗魂/训练等不显示
logger = logging.getLogger("aram")


class Bus(QObject):
    phase = Signal(str)
    cards = Signal(list)
    live = Signal(object)
    offer = Signal(object)
    status = Signal(str, str, object)
    log = Signal(str)
    data_ready = Signal()
    checks = Signal(list)
    detail = Signal(str)
    shopv = Signal(str, bool)
    layout = Signal(str, object, bool)


class Toast(QWidget):
    """屏幕上方的短暂提示（点击穿透）。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus |
                            Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame()
        self.frame.setStyleSheet("QFrame{background:rgba(8,16,24,235);border:1px solid #a8863e;border-radius:10px;}")
        lay.addWidget(self.frame)
        fl = QVBoxLayout(self.frame)
        self.label = QLabel()
        self.label.setStyleSheet("color:#eef3f6;font-family:'%s';font-size:16px;background:transparent;border:none;"
                                 "padding:6px 14px;" % ui.FONT)
        fl.addWidget(self.label)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self.hide)

    def say(self, text, rect, ms=4000):
        self.label.setText(text)
        self.adjustSize()
        _, (l, t, w, h) = ui.logical_game(rect)
        self.move(int(l + (w - self.width()) / 2), int(t + 40))
        if not self.isVisible():
            self.show()
            winfx.overlay_style(int(self.winId()), click_through=True)
        self.timer.start(ms)


class Helper(QObject):
    def __init__(self, qapp, args):
        super().__init__()
        self.qapp, self.args = qapp, args
        self.settings = config.Settings()
        self.data = Data(config.CACHE_DIR)
        self.bus = Bus()
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(3)
        self._fetching = set()
        self.loader = ui.ImageLoader(config.CACHE_DIR)
        self.ctx = ui.Ctx(self.data, self.loader, self.settings)
        # 状态
        self.phase, self.live, self.champ_hero, self.hero_id = "None", None, None, None
        self.cards, self.offer, self.chosen = [], None, []
        self.shop_state, self.overview_manual = False, False   # 商店面板是否应显示 / 强化速查
        self.hidden_all, self.last_click, self.prev_time = False, None, 0
        self.demo_t0 = None
        self._sig = None
        self.vq = queue.Queue()      # 主线程 → 画面线程的事件（按键/点击）
        self.offer_end, self._fg, self._seen_events = 0.0, True, set()
        self.ocr = vision.OcrWorker(os.path.join(config.DATA_DIR, "ocr"))
        # 窗口
        hk = self.settings["hotkeys"]
        self.control = ui.ControlWindow(self.settings, hk)
        self.tray = ui.Tray(self.control, self.quit)
        self.tray.show()
        self.cs = ui.ChampSelectWindow(self.ctx)
        self.side = ui.SidePanel(ui.Ctx(self.data, self.loader, self.settings))  # 各窗口各自按分辨率缩放
        self.toast = Toast()
        # 信号
        b = self.bus
        b.phase.connect(self.on_phase)
        b.cards.connect(self.on_cards)
        b.live.connect(self.on_live)
        b.shopv.connect(self.on_shop_verdict)
        b.layout.connect(self.on_layout)
        b.offer.connect(self.on_offer)
        b.status.connect(self.control.set_status)
        b.log.connect(self.control.add_log)
        b.data_ready.connect(self.start_workers)
        b.checks.connect(self.on_checks)
        b.detail.connect(self.on_detail)
        c = self.control
        c.toggle_side.connect(self.toggle_side)
        c.scale_changed.connect(self.change_scale)
        c.preview.connect(self.preview)
        c.refresh_data.connect(lambda: self.load_data(force=True))
        c.quit_requested.connect(self.quit)
        c.run_check.connect(self.run_selfcheck)
        c.export_diag.connect(self.export_diag)
        self.last_checks = []
        self.loader.loaded.connect(lambda *_: None)
        # 按键轮询
        self.hk = {k: winfx.parse_hotkey(v) for k, v in hk.items()}
        self.vk_shop = winfx.vk(self.settings["shop_key"])
        self.vk_esc, self.vk_lb, self.vk_enter = winfx.vk("ESC"), winfx.vk("LBUTTON"), winfx.vk("ENTER")
        self.chatting = False
        self.prev_keys = {}
        t = QTimer(self)
        t.timeout.connect(self.poll_keys)
        t.start(40)
        self.layout_notes = []
        self.cs.report = self.log
        ct = QTimer(self)  # 客户端被移动/改变大小后，选人窗口跟着重新摆放
        ct.timeout.connect(self.cs.follow_client)
        ct.start(1000)
        fg = QTimer(self)  # 游戏不在前台（切到别的窗口）时收起面板，回来再显示
        fg.timeout.connect(self.check_foreground)
        fg.start(400)
        if os.environ.get("ARAM_SNAP"):  # 开发调试：把自己的窗口渲染成图片（不截全屏）
            self._snap_n = {}
            st = QTimer(self)
            st.timeout.connect(self._snap)
            st.start(1500)

    def _snap(self):
        d = os.environ["ARAM_SNAP"]
        for name, w in (("cs", self.cs), ("side", self.side), ("control", self.control)):
            if w.isVisible():
                n = self._snap_n[name] = self._snap_n.get(name, 0) + 1
                w.grab().save(os.path.join(d, "%s_%02d.png" % (name, n)))

    # ------------------------------------------------------------ 日志
    def log(self, msg):
        logger.info(msg)
        self.bus.log.emit(time.strftime("%H:%M:%S ") + str(msg))

    # ------------------------------------------------------------ 数据
    def load_data(self, force=False):
        self.bus.status.emit("data", "正在加载数据…", None)

        def work():
            try:
                self.data.load(force=force, log=self.log)
                self.bus.data_ready.emit()
            except Exception as e:  # noqa: BLE001
                self.log("数据加载失败：%s" % e)
                self.bus.status.emit("data", "加载失败：%s" % e, False)
                self.run_selfcheck()
        threading.Thread(target=work, daemon=True).start()

    def start_workers(self):
        m = self.data.meta
        self.bus.status.emit("data", "%s 版 · 更新于 %s · %d 个英雄 · 来源 %s%s" % (
            m["version"], m["updated"], m["total"], self.data.source_label(),
            "" if m.get("source") == "aramgg" else "（备用源，部分数据缺失）"), True if m.get("source") == "aramgg" else None)
        if getattr(self, "_started", False):
            self.cs.render() if self.cs.isVisible() else None
            return
        self._started = True
        for target in (self.client_loop, self.live_loop, self.vision_loop):
            threading.Thread(target=self._guard(target, restart=True), daemon=True).start()
        threading.Thread(target=self._guard(self.ocr_start), daemon=True).start()
        self.refresh_shop_status()

    def _guard(self, fn, restart=False):
        """常驻线程出异常时记日志并在 2 秒后重启（以前一次异常就让整局的识别彻底停掉）。"""
        def run():
            while True:
                try:
                    fn()
                    return
                except Exception:  # noqa: BLE001
                    self.log("线程 %s 异常%s：\n%s" % (fn.__name__, "，2 秒后自动重启" if restart else "",
                                                   traceback.format_exc()))
                if not restart or self.stop.wait(2):
                    return
        return run

    # ------------------------------------------------------------ 客户端轮询（选人阶段）
    def client_loop(self):
        lcu, phase, queue_id, last, fails = None, None, None, None, 0
        include_team = self.settings["include_teammates"]
        while not self.stop.is_set():
            if lcu is None:
                lcu = DemoLCU([int(i) for i in self.data.meta["summary"]]) if self.args.demo else LCU.discover()
                if lcu is None:
                    if phase != "offline":
                        phase = "offline"
                        self.bus.status.emit("client", "未检测到英雄联盟客户端（启动客户端后自动连接）", None)
                        self.bus.phase.emit("None")
                    self.stop.wait(20 if self.live is not None else 4)
                    continue
                phase, fails = None, 0
            try:
                new = lcu.get("/lol-gameflow/v1/gameflow-phase") or "None"
                fails = 0
                if new != phase:
                    phase = new
                    self.bus.status.emit("client", "已连接 · 当前阶段：%s" % new, True)
                    self.bus.phase.emit(new)
                    if new == "ChampSelect":
                        queue_id, last = None, None
                if phase == "ChampSelect":
                    if queue_id is None:
                        gs = lcu.get("/lol-gameflow/v1/session") or {}
                        queue_id = ((gs.get("gameData") or {}).get("queue") or {}).get("id")
                    sess = lcu.get("/lol-champ-select/v1/session")
                    if sess:
                        aram = self.settings["any_mode"] or sess.get("benchEnabled") or queue_id in ARAM_QUEUES \
                            or sess.get("benchChampions")
                        cands = candidates(sess, include_team) if aram else []
                        if cands and cands != last:
                            last = cands
                            self.bus.cards.emit(cands)  # 先显示卡片（榜单数据已在本地），详情在后台并发获取
                            for cid, _ in cands:
                                self.fetch_detail_async(cid)
            except LCUError as e:
                fails += 1
                if fails < 4:  # 客户端在游戏中偶尔响应慢：保留凭据直接重试，不要重新发现（会启动 PowerShell）
                    self.stop.wait(5 if self.live is not None else 2)
                    continue
                self.log("与客户端的连接断开：%s" % e)
                lcu, phase, fails = None, None, 0
                if self.live is None:
                    self.bus.phase.emit("None")
                self.stop.wait(10 if self.live is not None else 3)
                continue
            # 对局中只需要偶尔看一眼阶段；选人阶段才需要 1 秒一次
            self.stop.wait(1 if phase in ("ChampSelect", "Lobby", "Matchmaking", "ReadyCheck") or self.live is None else 5)

    def fetch_detail_async(self, cid):
        cid = str(cid)
        if self.data.detail_cached(cid) or cid in self._fetching:
            return
        self._fetching.add(cid)

        def work():
            try:
                self.data.ensure_detail(cid)
            except Exception as e:  # noqa: BLE001
                self.log("英雄 %s 详情获取失败：%s" % (cid, e))
            finally:
                self._fetching.discard(cid)
                self.bus.detail.emit(cid)
        self.pool.submit(self._guard(work))

    def on_detail(self, cid):
        """某个英雄的详情到了：合并刷新（图片陆续到达时也会走这里），避免频繁重建界面。"""
        if not getattr(self, "_render_pending", False):
            self._render_pending = True

            def go():
                self._render_pending = False
                if self.cs.isVisible():
                    self.cs.render()
                self._sig = None
                self.refresh()
            QTimer.singleShot(300, go)

    # ------------------------------------------------------------ 对局实时数据
    def live_loop(self):
        client = LiveClient()
        while not self.stop.is_set():
            if self.args.demo:
                snap = self.demo_live.snapshot() if self.phase == "InProgress" and hasattr(self, "demo_live") else None
            else:
                snap = client.snapshot()
            self.bus.live.emit(snap)
            self.stop.wait(1.0 if self.args.demo else (1.5 if snap else 3))

    # ------------------------------------------------------------ 画面识别线程
    def ocr_start(self):
        if not self.settings["augment_ocr"]:
            self.bus.status.emit("ocr", "已在设置中关闭", None)
            return
        self.bus.status.emit("ocr", "正在启动系统 OCR…", None)
        if self.ocr.start():
            self.bus.status.emit("ocr", "就绪（系统 OCR：%s）· 出现三选一时自动识别" % self.ocr.lang, True)
        else:
            self.bus.status.emit("ocr", self.ocr.error or "不可用", False)
        self.run_selfcheck()  # OCR 启动结果出来后做一次完整自检

    # 三选一卡片的标题所在横条（实测 2560x1440：标题在 y≈0.41）。整块大图交给 Windows OCR 会一个字都认不出，
    # 只截这条横带则又快（约 0.1 秒）又准；横带没认出来时再用缩小的整块中部兜底一次。
    # 横带上下范围按画面高度比例；左右按“画面高度的倍数”从中心算起（适配 16:10 / 21:9 / 4:3）
    @staticmethod
    def aug_crops(rect):
        return ((vision.center_crop(rect, 0.78, 0.28, 0.56), 2048, 300),   # 标题横带；低分辨率放大到 ≥300 像素高
                (vision.center_crop(rect, 0.78, 0.10, 0.90), 1280, 0))     # 兜底：缩小的整块中部

    # 三选一界面里的区域（纵向占画面高度的比例）：卡片本体 / 卡片下方的“刷新”按钮
    CARD_Y, REROLL_Y = (0.15, 0.67), (0.67, 0.78)

    def click_zone(self, rect, click):
        """点击落在三选一的哪里：'card'（选择）/ 'reroll'（刷新这张）/ None。"""
        if not click or not rect:
            return None
        ry = (click[1] - rect[1]) / max(1, rect[3])
        rx = (click[0] - rect[0]) / max(1, rect[2])
        if abs(rx - 0.5) > 0.62 * rect[3] / max(1, rect[2]):
            return None
        if self.CARD_Y[0] <= ry < self.CARD_Y[1]:
            return "card"
        if self.REROLL_Y[0] <= ry < self.REROLL_Y[1]:
            return "reroll"
        return None

    def small_frame(self, rect, mask=None):
        """画面缩小成 128x72 的 RGB 浮点图（本程序自己的面板区域涂掉）。返回 (图, 涂掉的区域)。"""
        a = vision.grab(rect, 128, 72).astype(np.float32)[:, :, 2::-1]
        m = mask
        if m is None and self.side.phys_rect:
            x, y, w, h = self.side.phys_rect
            m = (int((x - rect[0]) * 128 / rect[2]), int((y - rect[1]) * 72 / rect[3]),
                 int((x + w - rect[0]) * 128 / rect[2]) + 1, int((y + h - rect[1]) * 72 / rect[3]) + 1)
        if m:
            a[max(0, m[1]):max(0, m[3]), max(0, m[0]):max(0, m[2])] = 0
        return a, m

    def vision_loop(self):
        """画面线程（只读屏幕像素）。

        为了不拖累显卡：三选一出现并认全之后不再反复做 OCR，只每秒截一张 64x12 的小图看卡片区域有没有变化
        （刷新、选择、消失），有变化或点击了卡片/刷新按钮才重新识别；平时 5 秒看一眼，升级后/开局 1.5 秒一次。
        截图时把本程序自己的面板涂黑，避免 OCR 读到面板里的强化名。游戏不在前台时什么都不做。"""
        if self.args.demo:
            return
        scan_last, prev_thumb, cur, pend_key, pend_n, none_n = 0.0, None, None, None, 0, 0
        burst_until, last_level, rescan_at, ref, ref_t = 0.0, None, 0.0, None, 0.0
        shop_prev, shop_due, close_due, open_frame = False, 0.0, 0.0, None
        while not self.stop.is_set():
            rect = winfx.game_rect()
            if not rect or self.live is None or not winfx.foreground_is_game():
                try:
                    self.vq.get(timeout=0.5)
                except queue.Empty:
                    pass
                continue
            try:
                ev = self.vq.get(timeout=0.2)
            except queue.Empty:
                ev = None
            now = time.time()
            if ev == "click" and cur and self.click_zone(rect, self.last_click):
                rescan_at = now + 0.8        # 点了卡片（选择）或刷新按钮：0.8 秒后看结果
            # ---------------- 商店面板的位置（不同机器上商店位置/大小不同，所以从画面里量，不写死）
            #  打开后 0.5 秒：用“底色法”量一次，之后每 3 秒复查（商店被拖动也能跟上）；
            #  关闭后 0.55 秒：把“开着时的图”和“关了以后的图”对比，面板出现的位置两侧都能量准，并记到设置里下次直接用。
            if self.shop_state != shop_prev:
                shop_prev = self.shop_state
                if shop_prev:
                    shop_due, open_frame, close_due = now + 0.5, None, 0.0
                else:
                    shop_due, close_due = 0.0, (now + 0.55 if open_frame is not None else 0.0)
            try:
                if self.shop_state and shop_due and now >= shop_due:
                    shop_due = now + 3.0
                    fr, mask = self.small_frame(rect)
                    if open_frame is None:
                        open_frame = (fr, mask)
                    ext = layout.shop_extent_array(fr[:, :, 0], fr[:, :, 1], fr[:, :, 2])
                    self.bus.layout.emit("shop", layout.widen(ext), False)
                elif close_due and now >= close_due:
                    close_due = 0.0
                    fr, _ = self.small_frame(rect, open_frame[1])
                    ext = layout.shop_extent_diff(open_frame[0], fr)
                    open_frame = None
                    if ext:
                        self.bus.layout.emit("shop", layout.widen(ext), True)
            except Exception:  # noqa: BLE001
                self.log("测量商店位置出错：\n%s" % traceback.format_exc())
                open_frame, close_due = None, 0.0
            if not (self.ocr.ready and self.settings["augment_ocr"]) or self.shop_state:
                continue
            snap = self.live
            if snap is None:
                continue
            lvl = snap.get("level")
            if last_level is not None and lvl and lvl > last_level:
                burst_until = now + 45        # 升级后很快会出现强化选择
            last_level = lvl or last_level
            band = self.aug_crops(rect)[0][0]
            masks = [m for m in (self.side.phys_rect,) if m]
            if cur is not None and not rescan_at:
                if self.settings["overlay_safe"]:
                    # 防闪烁模式：三选一停留期间不持续读屏。选择/刷新都要点击（点击后 0.8 秒会复查），
                    # 只每 6 秒复查一次，防止超时自动选择后面板一直不消失
                    if now - scan_last < 6:
                        continue
                    ref = None
                # 普通模式：每秒看一眼卡片区域的小图有没有明显变化
                elif now - ref_t < 1.0:
                    continue
                if ref is not None:
                    ref_t = now
                    small = vision.grab(vision.crop_rect(rect, band), 64, 12)[:, :, :3].astype(np.int16)
                    if float(np.abs(small - ref).mean()) < 10 and now - scan_last < 8:
                        continue
            else:
                fast = now < burst_until or snap.get("time", 0) < 90 or pend_n > 0
                gap = 1.5 if fast else 5.0
                if now < scan_last + gap and not (rescan_at and now >= rescan_at):
                    continue
                if not fast and not rescan_at:    # 平时：画面几乎没变就不做 OCR
                    thumb = vision.grab(rect, 48, 27)[:, :, :3].astype(np.int16)
                    still = prev_thumb is not None and float(np.abs(thumb - prev_thumb).mean()) < 1.0
                    prev_thumb = thumb
                    if still and now - scan_last < 20:
                        scan_last = now
                        continue
            hits = []
            crops = self.aug_crops(rect)
            for i, (crop, mw, mh) in enumerate(crops):
                lines, w, h = self.ocr.ocr_screen(rect, max_w=mw, crop=crop, min_h=mh, masks=masks)
                if self.ocr.last_error and self.ocr.last_error != getattr(self, "_ocr_err", None):
                    self._ocr_err = self.ocr.last_error
                    self.log("海克斯 OCR 出错：%s" % self.ocr.last_error)
                hits = vision.find_augments(self.data, lines, w, h)
                if len(hits) == 2:                # 短标题（如“台风”）常被漏掉：单独放大第三张的标题再认一次
                    hits = vision.recover_missing(self.ocr, self.data, getattr(self.ocr, "last_path", None), hits, w, h)
                for x in hits:                    # 裁剪图内的比例 → 整个画面的比例
                    x["cx"] = crop[0] + x["cx"] * (crop[2] - crop[0])
                    x["cy"] = crop[1] + x["cy"] * (crop[3] - crop[1])
                if hits or not (i == 0 and now < burst_until):
                    break                         # 只有升级后横带什么都没认出时，才用整块中部兜底一次
            scan_last, rescan_at = time.time(), 0.0
            key = tuple(x["aid"] for x in hits)
            if key:
                none_n = 0
                pend_n = pend_n + 1 if key == pend_key else 1
                pend_key = key
                if (pend_n >= 2 or len(hits) >= 3) and key != cur:   # 三个都认出来就不用等第二次
                    cur = key
                    self.bus.layout.emit("offer", layout.cards_extent(hits), False)   # 实测卡片左右边缘，面板据此避让
                    self.bus.offer.emit(hits)
                if key == cur and not self.settings["overlay_safe"]:
                    ref = vision.grab(vision.crop_rect(rect, band), 64, 12)[:, :, :3].astype(np.int16)
                    ref_t = time.time()
            else:
                pend_key, pend_n = None, 0
                none_n += 1
                picked = self.last_click is not None and time.time() - self.last_click[2] < 3 and \
                    self.click_zone(rect, self.last_click) == "card"
                if cur and (none_n >= 2 or picked):
                    cur, ref = None, None
                    self.bus.offer.emit(None)

    # ------------------------------------------------------------ 状态更新（主线程）
    def set_shop(self, state, why=""):
        if state != self.shop_state:
            self.shop_state = state
            self.log("商店面板：%s%s" % ("显示" if state else "收起", "（%s）" % why if why else ""))
            self.refresh()

    def on_layout(self, mode, ext, from_diff):
        """画面线程量到的商店面板/三选一卡片左右边缘。"""
        if mode == "shop" and ext and from_diff and None not in ext[:2]:   # 打开前后对比量到的两侧都可信：记下来
            rect = winfx.game_rect()
            if rect:
                saved = dict(self.settings["shop_extent"])
                key = "%dx%d" % (rect[2], rect[3])
                if saved.get(key) != list(ext[:2]):
                    saved[key] = list(ext[:2])
                    self.settings.set("shop_extent", saved)
        if self.side.set_extent(mode, tuple(ext[:2]) if ext else None):
            self.log("%s位置：%s" % ("商店面板" if mode == "shop" else "强化卡片", "未测到" if not ext else
                                    "左 %s 右 %s（占画面宽度）" % tuple("?" if v is None else "%.2f" % v for v in ext[:2])))
            self._sig = None
            self.refresh()

    def on_shop_verdict(self, why, state):
        """其他线程（演示流程）要求设置商店面板状态。"""
        self.set_shop(state, why)

    def on_phase(self, phase):
        self.phase = phase
        if phase == "ChampSelect":
            self.cs.dismissed = False
            self.chosen.clear()
            if self.args.demo:
                self.demo_t0 = None
        else:
            self.cs.hide()
            if phase != "InProgress":
                self.cards = []
        if phase == "InProgress" and self.args.demo:
            self.demo_live = DemoLive(self.data.hero(self.champ_hero)["alias"] if self.champ_hero else "Jinx",
                                      ["1018"])
            self.demo_t0 = time.time()
            threading.Thread(target=self._guard(self.demo_timeline), daemon=True).start()
        self.refresh()

    def on_cards(self, cands):
        mine = [c for c, t in cands if t == "mine"]
        if mine:
            self.champ_hero = str(mine[0])
        self.cards = order_cards(cands, self.data)
        self.cs.set_cards(self.cards)
        self.refresh()

    def note_layout(self):
        """记录这台机器的分辨率/显示器/客户端位置，载入上次量到的商店位置；游戏设置文件在后台线程读。"""
        rect = winfx.game_rect()
        if not rect:
            return
        scr, lg = ui.logical_game(rect)
        saved = self.settings["shop_extent"].get("%dx%d" % (rect[2], rect[3]))
        if saved and "shop" not in self.side.extent:
            self.side.extent["shop"] = tuple(saved)
        notes = ["游戏窗口 %dx%d 位于 (%d,%d)，显示器原点 (%d,%d)，显示缩放 %d%%，共 %d 个显示器，已记住的商店位置 %s" % (
            rect[2], rect[3], rect[0], rect[1], scr["x"], scr["y"], round(scr["dpr"] * 100), len(ui.screens_info()),
            saved or "无")]
        self.layout_notes = notes
        self.log(notes[0])

        def read_cfg():
            cfg = gamecfg.read_game_settings(winfx.game_exe_path())
            line = "游戏设置：" + (" ".join("%s=%s" % kv for kv in cfg.items()) if cfg else "没读到（不影响使用）")
            self.layout_notes.append(line)
            self.log(line[:600])
        self.pool.submit(self._guard(read_cfg))

    def on_live(self, snap):
        was = self.live
        if snap and snap.get("mode") in NON_ARAM_MODES and not self.settings["any_mode"]:
            self.bus.status.emit("game", "当前对局不是大乱斗（%s），面板不显示" % snap["mode"], None)
            snap = None
        self.live = snap
        if snap is None:
            if was is not None:
                self.log("对局已结束")
                self.shop_state, self.offer = False, None
            if was is not None or not getattr(self, "_nongame", False):
                self.bus.status.emit("game", "未在对局中", None)
            if was is not None:
                self.refresh()
            return
        if was is None or snap["time"] < self.prev_time - 5:
            self.chosen.clear()
            self.log("检测到新对局")
            self.note_layout()
        self.prev_time = snap["time"]
        for name in snap.get("events") or []:   # 记录对局里出现过的事件类型（排查用）
            if name not in self._seen_events:
                self._seen_events.add(name)
                self.log("对局事件类型：%s" % name)
        hid = None
        if snap.get("alias"):
            hid = self.data.hero_by_alias(snap["alias"])
        if not hid and snap.get("champ_name"):
            hid = self.data.hero_by_name(snap["champ_name"])
        hid = hid or self.champ_hero
        if hid != self.hero_id:
            self.hero_id = hid
            self._sig = None
            if hid and not self.data.detail_cached(hid):
                self.pool.submit(self._guard(lambda: (self.data.ensure_detail(hid), self.bus.live.emit(self.live))))
        h = self.data.hero(hid) if hid else None
        self.bus.status.emit("game", "对局中 · %s · %d:%02d" % (h["title"] if h else "英雄未识别", snap["time"] // 60,
                                                              snap["time"] % 60), True)
        self.refresh()

    def on_offer(self, hits):
        if hits:
            if not self.offer:
                self.log("识别到海克斯三选一：" + " / ".join(self.data.augment_name(h["aid"]) for h in hits))
            if [h["aid"] for h in hits] != [h["aid"] for h in self.offer or []]:   # 新的一组选项（含刷新后）：重新显示
                self.offer_hidden = False
                self._offer_gen = getattr(self, "_offer_gen", 0) + 1
                secs = int(self.settings["offer_panel_secs"] or 0)
                if secs > 0:
                    gen = self._offer_gen
                    QTimer.singleShot(secs * 1000, lambda: self.hide_offer_panel(gen))
            self.offer = hits
        else:
            if self.offer:
                self.record_choice()
                self.offer_end = time.time()
            self.offer = None
        self.refresh()

    def hide_offer_panel(self, gen):
        """三选一推荐面板显示够久后收起，减少悬浮窗压在游戏画面上的时间（开启动态刷新率时长时间压着容易闪屏）。"""
        if self.offer and gen == getattr(self, "_offer_gen", 0):
            self.offer_hidden = True
            self.log("三选一推荐面板已自动收起（Ctrl+Alt+A 可再调出）")
            self._sig = None
            self.refresh()

    def record_choice(self):
        """三选一消失：用消失前最后一次鼠标点击的位置判断选了哪一个。"""
        lc = self.last_click
        rect = winfx.game_rect() or winfx.screen_rect()
        if not lc or time.time() - lc[2] > 8:
            self.log("没有捕捉到选择点击，未记录（可手动忽略）")
            return
        if self.click_zone(rect, lc) != "card":
            self.log("最后一次点击不在卡片上（可能是刷新按钮），未记录选择")
            return
        rel = (lc[0] - rect[0]) / rect[2]
        pick = min(self.offer, key=lambda h: abs(h["cx"] - rel))
        if abs(pick["cx"] - rel) < 0.2 and pick["aid"] not in self.chosen:
            self.chosen.append(pick["aid"])
            self.log("已记录选择：" + self.data.augment_name(pick["aid"]))
            self.toast.say("已记录选择：%s（可用撤销键撤销）" % self.data.augment_name(pick["aid"]), rect, 3000)

    # ------------------------------------------------------------ 刷新面板
    def check_foreground(self):
        fg = self.args.demo or winfx.foreground_is_game()
        if fg != self._fg:
            self._fg = fg
            self._sig = None
            self.refresh()

    def refresh(self):
        if self.args.demo:
            rect = winfx.screen_rect()
        else:
            rect = winfx.game_rect() or winfx.screen_rect()
        cs_show = self.phase == "ChampSelect" and self.cards and not self.cs.dismissed and not self.hidden_all
        if cs_show and not self.cs.isVisible():
            self.cs.show()
        elif not cs_show and self.cs.isVisible():
            self.cs.hide()
        if self.hidden_all or self.live is None or not self._fg:
            self.side.dismiss()
            self._sig = None
            return
        hid, snap = self.hero_id, self.live
        if self.offer and getattr(self, "offer_hidden", False) and not self.overview_manual:
            self._sig = None
            self.side.dismiss()
        elif self.offer:
            ids = [h["aid"] for h in self.offer]
            sig = ("offer", hid, tuple(ids), tuple(self.chosen))
            if sig != self._sig:
                self._sig = sig
                self.side.show_offer(hid, advisor.evaluate_offer(self.data, hid, ids, self.chosen), rect)
        elif self.overview_manual:
            sig = ("ov", hid)
            if sig != self._sig:
                self._sig = sig
                self.side.show_overview(hid, rect)
        elif self.shop_state:
            sig = ("shop", hid, tuple(snap["items"]), int(snap["gold"]) // 100)  # 金币每涨 100 才重画，面板不会一直闪
            if sig != self._sig:
                self._sig = sig
                self.side.show_shop(hid, snap["items"], int(snap["gold"]), rect)
        else:
            self._sig = None
            self.side.dismiss()

    # ------------------------------------------------------------ 快捷键 / 鼠标
    def poll_keys(self):
        down = {k: winfx.hotkey_down(c) for k, c in self.hk.items()}
        down["_p"], down["_esc"], down["_lb"], down["_enter"] = (
            winfx.key_down(self.vk_shop), winfx.key_down(self.vk_esc), winfx.key_down(self.vk_lb),
            winfx.key_down(self.vk_enter))
        edge = {k for k, v in down.items() if v and not self.prev_keys.get(k)}
        self.prev_keys = down
        if not edge:
            return
        if "shop" in edge:
            self.set_shop(not self.shop_state, "手动")
        if "augment" in edge:
            self.overview_manual = not self.overview_manual
            self._sig = None
            self.refresh()
        if "undo" in edge and self.chosen:
            gone = self.chosen.pop()
            self.toast.say("已撤销：%s" % self.data.augment_name(gone), winfx.game_rect() or winfx.screen_rect(), 2500)
            self._sig = None
            self.refresh()
        if "hide" in edge:
            self.hidden_all = not self.hidden_all
            self.refresh()
        if edge & {"bigger", "smaller", "dock_left", "dock_right"}:
            if "bigger" in edge or "smaller" in edge:
                v = round(min(1.8, max(0.6, float(self.settings["ui_scale"]) + (0.1 if "bigger" in edge else -0.1))), 2)
                self.settings.set("ui_scale", v)
                msg = "面板大小 %d%%" % round(v * 100)
            else:
                self.settings.set("dock_side", "left" if "dock_left" in edge else "right")
                msg = "面板放在画面%s侧" % ("左" if "dock_left" in edge else "右")
            self._sig = None
            self.side._digest = None
            if self.cs.isVisible():
                self.cs.render()
            self.refresh()
            self.toast.say(msg, winfx.game_rect() or winfx.screen_rect(), 1800)
        if (self.args.demo or winfx.foreground_is_game()) and self.live is not None:
            if "_enter" in edge:
                self.chatting = not self.chatting   # 回车打开/发送聊天
            if "_p" in edge and not any(k in edge for k in self.hk) and not self.chatting:
                self.set_shop(not self.shop_state, "%s 键" % self.settings["shop_key"])
            if "_esc" in edge:
                self.chatting = False
                if self.shop_state:
                    self.set_shop(False, "Esc")
            if "_lb" in edge:
                x, y = winfx.cursor_pos()
                self.last_click = (x, y, time.time())
                self.vq.put("click")

    def toast_async(self, text, rect):
        self.log(text)
        QTimer.singleShot(0, lambda: self.toast.say(text, rect, 5000))

    def refresh_shop_status(self):
        self.bus.status.emit("shop", "按 %s 打开商店时自动出现、再按 %s 或 Esc 关闭时消失（聊天打字时不算）；"
                                     "用鼠标点商店按钮开关时请用 %s 手动开关" % (
                                         self.settings["shop_key"], self.settings["shop_key"],
                                         self.settings["hotkeys"]["shop"]), True)

    # ------------------------------------------------------------ 环境自检
    def run_selfcheck(self):
        def work():
            try:
                self.bus.checks.emit(selfcheck.check_all(self))
            except Exception:  # noqa: BLE001
                self.log("自检异常：\n%s" % traceback.format_exc())
        threading.Thread(target=work, daemon=True).start()

    def on_checks(self, checks):
        self.last_checks = checks
        bad, warn = self.control.set_checks(checks)
        self.log("自检完成：%d 项失败，%d 项警告" % (bad, warn))
        if (bad or warn) and not getattr(self, "_check_notified", False):
            self._check_notified = True
            self.control.show()  # 有问题时自动把控制台带出来，让用户看到修复提示
            self.tray.showMessage("Hex Helper", "环境自检发现 %d 个问题，请查看控制台" % (bad + warn))

    def export_diag(self):
        p = selfcheck.write_diagnostic(self, self.last_checks)
        self.log("诊断文件已生成：%s" % p)
        try:
            os.startfile(os.path.dirname(p))
        except OSError:
            pass

    # ------------------------------------------------------------ 设置动作
    def toggle_side(self):
        self.settings.set("dock_side", "left" if self.settings["dock_side"] == "right" else "right")
        self._sig = None
        self.refresh()

    def change_scale(self, delta):
        v = round(min(1.6, max(0.8, float(self.settings["ui_scale"]) + delta)), 2)
        self.settings.set("ui_scale", v)
        self._sig = None
        if self.cs.isVisible():
            self.cs.render()
        self.refresh()

    def preview(self):
        """不开游戏也能看到对局内面板的样子：依次展示商店面板和海克斯面板。"""
        hid = self.hero_id or self.champ_hero or min(self.data.meta["summary"], key=lambda k: self.data.meta["summary"][k]["rank"])
        rect = winfx.screen_rect()

        def prep():
            self.data.ensure_detail(hid)
            QTimer.singleShot(0, show_shop)

        def show_shop():
            b = self.data.builds(hid, 1)
            own = [b[0]["items"][1]["itemId"]] if b else []
            self.side.show_shop(hid, own, 2200, rect)
            QTimer.singleShot(6000, show_offer)

        def show_offer():
            det = self.data.detail_cached(hid)
            ids = [e["augmentId"] for e in det["allAugmentsByRarity"]["gold"][:5:2]]
            chosen = [det["allAugmentsByRarity"]["silver"][0]["augmentId"]]
            self.side.show_offer(hid, advisor.evaluate_offer(self.data, hid, ids, chosen), rect)
            QTimer.singleShot(8000, lambda: (self.side.hide(), setattr(self, "_sig", None), self.refresh()))
        self.pool.submit(self._guard(prep))

    # ------------------------------------------------------------ 演示流程（--demo）
    def demo_timeline(self):
        d = self.data
        hid = self.champ_hero
        det = d.ensure_detail(hid)
        silver = [e["augmentId"] for e in det["allAugmentsByRarity"]["silver"]]
        steps = [(3, lambda: self.bus.shopv.emit("演示", not self.shop_state)), (10, lambda: self.bus.shopv.emit("演示", not self.shop_state)),
                 (12, lambda: self.bus.offer.emit([{"aid": str(a), "cx": x, "cy": .45} for a, x in
                                                   zip((silver[0], silver[4], silver[8]), (.3, .5, .7))])),
                 (20, lambda: (setattr(self, "last_click", (int(winfx.screen_rect()[2] * .3), 500, time.time())),
                               self.bus.offer.emit(None))),
                 (23, lambda: self.bus.shopv.emit("演示", not self.shop_state)), (30, lambda: self.bus.shopv.emit("演示", not self.shop_state))]
        t0 = time.time()
        for at, fn in steps:
            while time.time() - t0 < at:
                time.sleep(0.1)
            fn()

    def quit(self):
        self.stop.set()
        self.ocr.stop()
        self.qapp.quit()


def candidates(session, include_team):
    """从选人会话中取出候选英雄：[(championId, 'mine'|'bench'|'team')]，去重保序。"""
    out, seen = [], set()

    def add(cid, tag):
        if cid and cid not in seen:
            seen.add(cid)
            out.append((cid, tag))

    me = session.get("localPlayerCellId")
    team = session.get("myTeam") or []
    for p in team:
        if p.get("cellId") == me:
            add(p.get("championId") or p.get("championPickIntent"), "mine")
    bench = session.get("benchChampions")
    ids = [b.get("championId") for b in bench] if bench is not None else session.get("benchChampionIds") or []
    for cid in ids:
        add(cid, "bench")
    if include_team:
        for p in team:
            if p.get("cellId") != me:
                add(p.get("championId"), "team")
    return out


def order_cards(cands, data):
    def key(c):
        s = data.summary(c[0])
        return s["rank"] if s else 10 ** 6

    ranked = sorted(cands, key=key)
    best = ranked[0][0] if len(ranked) > 1 and data.summary(ranked[0][0]) else None
    return [(cid, tag, cid == best) for cid, tag in ranked]


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="不连客户端，模拟一局完整流程")
    ap.add_argument("--minimized", action="store_true", help="启动时不显示控制台窗口")
    args = ap.parse_args()
    winfx.set_dpi_aware()
    logging.basicConfig(filename=config.LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(message)s")
    sys.excepthook = lambda *a: logger.error("未处理异常：\n%s", "".join(traceback.format_exception(*a)))
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    lock = QLockFile(os.path.join(config.DATA_DIR, "app.lock"))
    lock.setStaleLockTime(5000)
    if not lock.tryLock(200):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "Hex Helper", "程序已经在运行（看看右下角托盘图标）。")
        return 0
    h = Helper(qapp, args)
    if not args.minimized:
        h.control.show()
    h.load_data(force="--refresh" in sys.argv)
    code = qapp.exec()
    lock.unlock()
    return code
