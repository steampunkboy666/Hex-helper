"""画面识别：屏幕采样、商店开关感知（按键/点击触发，免校准）、系统自带 OCR 识别海克斯三选一。
全部是读取屏幕像素，不接触游戏进程。"""
import ctypes
import difflib
import os
import queue
import subprocess
import threading
import time
from ctypes import wintypes as wt
from itertools import combinations

import numpy as np

from data_source import norm

_gdi = ctypes.WinDLL("gdi32", use_last_error=True)
_usr = ctypes.WinDLL("user32", use_last_error=True)
_usr.GetDC.restype = ctypes.c_void_p
_usr.GetDC.argtypes = [ctypes.c_void_p]
_usr.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
_gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_gdi.SelectObject.restype = ctypes.c_void_p
_gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi.SetStretchBltMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
_gdi.StretchBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] + [ctypes.c_int] * 5
_gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
_gdi.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi.DeleteDC.argtypes = [ctypes.c_void_p]


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(rect, out_w, out_h):
    """把屏幕 rect=(l,t,w,h) 缩放采样为 out_w×out_h 的 BGRA 数组（GDI 半色调缩放，很快）。"""
    l, t, w, h = rect
    sdc = _usr.GetDC(None)
    mdc = _gdi.CreateCompatibleDC(sdc)
    bmp = _gdi.CreateCompatibleBitmap(sdc, out_w, out_h)
    old = _gdi.SelectObject(mdc, bmp)
    try:
        _gdi.SetStretchBltMode(mdc, 4)  # HALFTONE
        _gdi.StretchBlt(mdc, 0, 0, out_w, out_h, sdc, l, t, w, h, 0x00CC0020)  # SRCCOPY（不带 CAPTUREBLT：不截分层窗口，本程序的面板不会被自己看到）
        bmi = _BMIH(ctypes.sizeof(_BMIH), out_w, -out_h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(out_w * out_h * 4)
        _gdi.GetDIBits(mdc, bmp, 0, out_h, buf, ctypes.byref(bmi), 0)
        return np.frombuffer(buf, np.uint8).reshape(out_h, out_w, 4).copy()
    finally:
        _gdi.SelectObject(mdc, old)
        _gdi.DeleteObject(bmp)
        _gdi.DeleteDC(mdc)
        _usr.ReleaseDC(None, sdc)


def crop_rect(rect, crop):
    """crop=(x0,y0,x1,y1) 为占整个画面的比例；返回屏幕上的 (l,t,w,h)。"""
    l, t, w, h = rect
    if not crop:
        return rect
    return (l + int(w * crop[0]), t + int(h * crop[1]), max(1, int(w * (crop[2] - crop[0]))),
            max(1, int(h * (crop[3] - crop[1]))))


def save_png(rect, path, max_w=1920, crop=None, min_h=0, masks=()):
    """截图存 PNG。宽度超过 max_w 时缩小；高度不足 min_h（低分辨率下字太小，OCR 认不准）时放大，最多 2 倍。
    masks：屏幕上要涂黑的区域 (l,t,w,h)，即本程序自己的面板——否则 OCR 会读到面板里的强化名，误以为又弹出了三选一。"""
    from PIL import Image
    l, t, w, h = crop_rect(rect, crop)
    ow = min(w, max_w)
    oh = round(h * ow / w)
    arr = grab((l, t, w, h), ow, oh)
    for ml, mt, mw, mh in masks or ():
        x0, y0 = max(0, int((ml - l) * ow / w)), max(0, int((mt - t) * oh / h))
        x1, y1 = min(ow, int((ml + mw - l) * ow / w) + 1), min(oh, int((mt + mh - t) * oh / h) + 1)
        if x1 > x0 and y1 > y0:
            arr[y0:y1, x0:x1, :3] = 0
    gray = path.lower().endswith(".bmp")   # 给 OCR 的图存成灰度：文件只有彩色的 1/3，写盘和识别都更轻
    im = Image.frombuffer("RGBA", (ow, oh), arr.tobytes(), "raw", "BGRA", 0, 1).convert("L" if gray else "RGB")
    if min_h and oh < min_h:
        k = min(2.0, min_h / oh)   # 小分辨率放大不受 max_w 限制（限制了会认不出字）
        if k > 1.05:
            im = im.resize((round(ow * k), round(oh * k)), Image.LANCZOS)
    if path.lower().endswith(".bmp"):
        im.save(path, "BMP")      # 不压缩：写盘几乎不占 CPU（PNG 压缩一张 2K 图要几十毫秒，三选一期间会拖累游戏）
    else:
        im.save(path, "PNG", compress_level=1)
    return im.size


def center_crop(rect, half_h_units, y0, y1, max_half=0.47):
    """以画面水平中心为准、宽度按“画面高度”的倍数计算的裁剪框（比例坐标）。
    游戏界面按高度缩放，所以 16:9、16:10、21:9 超宽屏、4:3 下中间的卡片/商店都能对上。"""
    half = min(max_half, half_h_units * rect[3] / max(1, rect[2]))
    return (0.5 - half, y0, 0.5 + half, y1)


# ---------------------------------------------------------------- 商店开关感知（免校准）
class ShopSensor:
    """判断“现在商店是不是开着”（状态，而不是数开关次数）。

    商店面板的底色是很暗的棕褐色（实测 2560x1440 商店打开时，面板中央约 90% 的格子是这种颜色），
    而大乱斗地图、三选一卡片、Esc 设置菜单、退出对话框里这种颜色都不到 10%，橙色地图场景也只有约 40%。
    所以只看面板中央这种颜色的占比：≥0.55 认为打开，≤0.35 认为关闭，中间保持不变。
    在商店里点装备、看详情都不会让判断翻转；不需要校准。每次只截 64x36 的小图。"""
    GW, GH = 64, 36
    OPEN, CLOSE = 0.55, 0.35

    def regions(self, rect):
        """面板中央的判断区（比例坐标）。实测 2560x1440 商店外框 x≈0.19~0.78、y≈0.09~0.78；
        界面按高度缩放，换算成“画面高度”的倍数：取距水平中心 左 0.427H / 右 0.355H。"""
        r = rect[3] / max(1, rect[2])
        return (max(0.02, 0.5 - 0.427 * r), 0.16, min(0.98, 0.5 + 0.355 * r), 0.68)

    def score(self, rect, extent=None):
        """采样商店内侧的小块画面。黑屏或截图失败时没有有效分数。"""
        x0, y0, x1, y1 = self.regions(rect)
        if extent and None not in extent[:2]:
            left, right = extent[:2]
            if 0.18 <= right - left <= 0.92:
                pad = (right - left) * 0.14
                x0, x1 = left + pad, right - pad
        a = grab(crop_rect(rect, (x0, y0, x1, y1)), self.GW, self.GH)[:, :, :3]
        if not a.any():
            return None
        r, g, b = a[:, :, ::-1].astype(np.float32).transpose(2, 0, 1)
        return self.score_array(r, g, b, (0, 0, 1, 1))

    def score_array(self, r, g, b, box):
        ys = slice(int(box[1] * r.shape[0]), int(box[3] * r.shape[0]))
        xs = slice(int(box[0] * r.shape[1]), int(box[2] * r.shape[1]))
        r, g, b = r[ys, xs], g[ys, xs], b[ys, xs]
        lum = (r + g + b) / 3
        like = (lum > 8) & (lum < 70) & (b < 0.75 * r + 6) & (g <= r + 8)
        self.last = float(like.mean())
        return self.last


class ShopWatch:
    """按键或点击后复查。推荐显示时才低频巡检，两帧都关才自动收起。"""
    def __init__(self):
        self.revision = None
        self.due = 0.0
        self.low = 0

    def ready(self, now, revision, watching, *, click=False, open_request=False):
        if self.revision != revision:
            self.revision, self.low = revision, 0
            self.due = now + 0.45 if watching else 0.0
        if watching and click:
            self.due = min(self.due, now + 0.45)
        elif not watching and open_request:
            self.due = now + 0.45
        return bool(self.due and now >= self.due)

    def observe(self, now, score, watching):
        if not watching:
            self.due = 0.0
            return True if score is not None and score >= ShopSensor.OPEN else None
        self.low = self.low + 1 if score is not None and score <= ShopSensor.CLOSE else 0
        self.due = now + (0.45 if self.low else 2.0)
        return False if self.low >= 2 else None


# ---------------------------------------------------------------- 系统 OCR（Windows.Media.Ocr，经常驻 PowerShell 调用）
_OCR_PS = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = New-Object Text.UTF8Encoding $false
trap { [Console]::Out.WriteLine('INITERR ' + ($_.Exception.Message -replace "[\r\n]+", ' ')); exit 1 }
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$wanted = '__LANG__'
$installed = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
$language = $installed | Where-Object LanguageTag -eq $wanted | Select-Object -First 1
if (-not $language) {
  $pattern = if ($wanted -eq 'zh-Hant-TW') { '^zh-(Hant|TW|HK|MO)(-|$)' } else { '^zh-(Hans|CN|SG)(-|$)' }
  $language = $installed | Where-Object LanguageTag -match $pattern | Select-Object -First 1
}
if (-not $language) { [Console]::Out.WriteLine('NOENGINE'); exit 2 }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if (-not $engine) { throw "Cannot initialize OCR: $($language.LanguageTag)" }
[Console]::Out.WriteLine('READY ' + $engine.RecognizerLanguage.LanguageTag)
while (($line = [Console]::In.ReadLine()) -ne $null) {
  try {
    # 一次性读进内存后立刻释放文件：不再长时间占用图片文件（否则下一次截图写不进去）；中文路径也没问题
    $bytes = [System.IO.File]::ReadAllBytes($line)
    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream((New-Object System.IO.MemoryStream(,$bytes)))
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    foreach ($l in $res.Lines) {
      [Console]::Out.WriteLine('L')
      foreach ($w in $l.Words) { $r = $w.BoundingRect; [Console]::Out.WriteLine(('W' + [char]9 + $w.Text + [char]9 + [int]$r.X + [char]9 + [int]$r.Y + [char]9 + [int]$r.Width + [char]9 + [int]$r.Height)) }
    }
    $stream.Dispose()
    [Console]::Out.WriteLine('END')
  } catch { [Console]::Out.WriteLine('ERR ' + ($_.Exception.Message -replace "[\r\n]+", ' ')) ; [Console]::Out.WriteLine('END') }
}
'''


class OcrWorker:
    def __init__(self, work_dir, region="CN"):
        self.language = "zh-Hant-TW" if region == "TW" else "zh-Hans-CN"
        self.portable = None
        self.dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self.proc, self.q, self.ready, self.error, self.lang = None, queue.Queue(), False, None, None
        self.lock = threading.Lock()
        self.stats = {"n": 0, "cap": 0.0, "ocr": 0.0}   # 本局识别次数、截图/识别累计耗时（写进日志，方便按机器配置排查）
        self.last_error, self.t_start = None, 0.0

    def dead(self):
        """OCR 进程是否已经退出（被杀毒软件结束、系统回收等）。"""
        return self.portable is None and self.proc is not None and self.proc.poll() is not None

    @property
    def engine_label(self):
        return ("内置离线 OCR" if self.portable else "系统 OCR") + " · " + str(self.lang or self.language)

    def start(self):
        try:
            if self._start_windows():
                return True
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        windows_error = self.error
        self.stop()
        try:
            from portable_ocr import PortableOCR
            self.portable = PortableOCR(self.language)
        except Exception as exc:
            self.error = "%s；内置 OCR 初始化失败：%s" % (windows_error, exc)
            return False
        self.ready, self.lang, self.error = True, self.language, None
        return True

    def _start_windows(self):
        self.ready, self.error, self.q, self.t_start = False, None, queue.Queue(), time.time()
        if self.proc is not None:                 # 重启：先把旧进程的管道关掉
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        slow_pc = (os.cpu_count() or 8) <= 4      # 4 线程及以下的机器：OCR 用“空闲”优先级，只吃游戏用剩的 CPU
        prio = 0x40 if slow_pc else 0x4000
        script = os.path.join(self.dir, "ocr.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(_OCR_PS.replace("__LANG__", self.language))
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 | prio)  # 无窗口 + 低优先级（不和游戏抢 CPU）
        threading.Thread(target=self._reader, args=(self.proc, self.q), daemon=True).start()
        try:
            first = self.q.get(timeout=25)
        except queue.Empty:
            self.error = "OCR 引擎启动超时"
            return False
        if first.startswith("READY"):
            self.ready, self.lang = True, first.split(" ", 1)[1]
            return True
        if first != "NOENGINE":
            self.error = "OCR 引擎启动失败：%s" % first.removeprefix("INITERR ")
            return False
        self.error = "系统没有可用的中文 OCR（设置→时间和语言→语言→添加 %s，并勾选文字识别）" % self.language
        return False

    def _reader(self, proc, q):
        for raw in proc.stdout:
            q.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        q.put("EOF")

    def recognize(self, png_path, timeout=10):
        """返回 [[(text,x,y,w,h), ...], ...]（每个内层列表是一行文字）。"""
        if not self.ready:
            return []
        with self.lock:
            if self.portable:
                try:
                    self.last_error = None
                    return self.portable.read(png_path)
                except (OSError, RuntimeError) as exc:
                    self.last_error = str(exc)
                    return []
            while not self.q.empty():
                self.q.get_nowait()
            self.last_error = None
            try:
                self.proc.stdin.write((png_path + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:     # OCR 进程没了：标记为不可用，画面线程稍后会自动重启它
                self.ready, self.last_error = False, "OCR 进程已退出（%s）" % e
                return []
            lines, cur, end = [], None, time.time() + timeout
            while time.time() < end:
                try:
                    s = self.q.get(timeout=max(0.05, end - time.time()))
                except queue.Empty:
                    break
                if s == "EOF":
                    self.ready, self.last_error = False, "OCR 进程已退出"
                    break
                if s == "END":
                    break
                if s == "L":
                    cur = []
                    lines.append(cur)
                elif s.startswith("ERR"):
                    self.last_error = s[4:]  # 交给调用方写进日志（以前被静默丢弃，一直查不到原因）
                elif s.startswith("W\t") and cur is not None:
                    _, t, x, y, w, h = s.split("\t")
                    cur.append((t, int(x), int(y), int(w), int(h)))
            return lines

    def ocr_screen(self, rect, max_w=2048, crop=None, min_h=0, masks=()):
        """截取 rect（可只截中间一块、保持原始清晰度）并识别；轮换临时文件名（上一张可能仍被 OCR 进程占用）。
        返回 (lines, 图宽, 图高)。同时把识别到的文字写入 last_scan.txt，方便排查识别不到的情况。"""
        w = h = 0
        t0 = time.perf_counter()
        for _ in range(4):  # 文件若仍被占用（杀毒软件扫描等）就换下一个文件名，不让整个识别线程因此崩溃
            self._n = (getattr(self, "_n", -1) + 1) % 4
            path = os.path.abspath(os.path.join(self.dir, "cap%d.bmp" % self._n))  # 必须是反斜杠的绝对路径
            try:
                w, h = save_png(rect, path, max_w, crop, min_h, masks)
                self.last_path = path
                break
            except OSError as e:
                self.last_error = "截图写入失败：%s" % e
        else:
            return [], 0, 0
        t1 = time.perf_counter()
        lines = self.recognize(path)
        st = self.stats
        st["n"], st["cap"], st["ocr"] = st["n"] + 1, st["cap"] + t1 - t0, st["ocr"] + time.perf_counter() - t1
        try:
            with open(os.path.join(self.dir, "last_scan.txt"), "w", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S") + " 图 %dx%d 来自 %s\n" % (w, h, os.path.basename(path)))
                f.write("\n".join(" ".join(x[0] for x in ln) for ln in lines))
        except OSError:
            pass
        return lines, w, h

    def stop(self):
        self.ready = False
        with self.lock:
            if self.portable:
                self.portable.close()
                self.portable = None
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _close_name(data, text):
    """短标题的容错匹配：Windows OCR 常把两三个字的短标题认错一个字（如“台风”认成“台虱”）。
    只在已知“这里有一张卡片”时使用：文字里含完整名称直接采用；否则找同长度、仅错一个字、且唯一的名称。"""
    names = data.augment_names()
    t = norm(text)
    if t in names:
        return names[t]
    for nm in sorted(names, key=len, reverse=True):
        if len(nm) >= 2 and nm in t:
            return names[nm]
    best = []
    for seg_len in (2, 3, 4):
        for i in range(0, max(0, len(t) - seg_len) + 1):
            seg = t[i:i + seg_len]
            for nm, aid in names.items():
                if len(nm) == seg_len and sum(a == b for a, b in zip(nm, seg)) == seg_len - 1:
                    best.append(aid)
    best = list(dict.fromkeys(best))
    return best[0] if len(best) == 1 else None


def missing_title_centers(hits):
    """用居中的等距卡片拟合缺失标题位置，比较相对间距以适配宽高比。"""
    xs = sorted(h["cx"] for h in hits)
    if len(xs) not in (2, 3):
        return []
    candidates = []
    for count in range(max(3, len(xs)), 5):
        offsets = tuple(i - (count - 1) / 2 for i in range(count))
        for used in combinations(range(count), len(xs)):
            present = [offsets[i] for i in used]
            step = sum((x - 0.5) * v for x, v in zip(xs, present)) / sum(v*v for v in present)
            if step < 0.08 or offsets[-1] * step >= 0.47:
                continue
            error = max(abs(x - 0.5 - step*v) for x, v in zip(xs, present)) / step
            if error < 0.19:
                if count == len(xs):
                    return []
                missing = [0.5 + step*v for i, v in enumerate(offsets) if i not in used]
                candidates.append((round(error, 3), count, missing))
    return min(candidates)[2] if candidates else []


def recover_missing(ocr, data, path, hits, W, H):
    """放大可能漏读的官方名称，保留已识别卡片。"""
    if len(hits) not in (2, 3) or not path:
        return hits
    from PIL import Image, ImageOps
    centers = missing_title_centers(hits)
    if not centers:
        return hits
    known = {h["aid"] for h in hits}
    cy = sum(h["cy"] for h in hits) / len(hits)
    full = list(hits)
    try:
        with Image.open(path) as image:
            g = ImageOps.grayscale(image)
        p = os.path.join(os.path.dirname(path), "title.png")
        variants = ((0.05, 0.07, 120, 2), (0.05, 0.07, 150, 2), (0.11, 0.05, 150, 2),
                    (0.07, 0.07, 120, 2), (0.05, 0.05, 150, 3), (0.11, 0.07, 120, 3))
        positions = centers + [h["cx"] for h in hits]
        for cx in centers:
            separation = min(abs(cx - x) for x in positions if x != cx)
            for hw, hh, thr, sc in variants[:6 // len(centers)]:
                hw = min(hw, separation * 0.47)
                box = (int((cx - hw) * W), int((cy - hh) * H), int((cx + hw) * W), int((cy + hh) * H))
                t = g.crop(box).point(lambda v, thr=thr: 0 if v > thr else 255)
                t = t.resize((t.width * sc, t.height * sc), Image.LANCZOS)
                page = Image.new("L", (t.width + 200, t.height + 120), 255)
                page.paste(t, (100, 60))
                page.save(p)
                text = "".join(w[0] for ln in ocr.recognize(p) for w in ln)
                aid = _close_name(data, text)
                if not aid and getattr(data, "region", None) == "TW" and norm(text) == "法師":
                    aid = "tw-mage"
                if aid and aid not in known:
                    full.append({"aid": aid, "cx": cx, "cy": cy})
                    known.add(aid)
                    break
    except OSError:
        return hits
    return fix_taiwan_mage(data, sorted(full, key=lambda h: h["cx"]))


def find_augments(data, ocr_lines, W, H):
    """在 OCR 结果里找海克斯强化名称，并判断是否构成“三选一”（同一水平线上 ≥2 个不同强化）。
    返回按屏幕从左到右排列的 [{aid, cx, cy}]；不是三选一返回 []。"""
    names = data.augment_names()
    by_len = sorted(names, key=len, reverse=True)
    hits = []
    for words in ocr_lines:
        chars, owner = [], []
        for i, wd in enumerate(words):
            for ch in norm(wd[0]):
                chars.append(ch)
                owner.append(i)
        line = "".join(chars)
        if len(line) < 2:
            continue
        used = [False] * len(line)
        found = []
        for nm in by_len:
            if len(nm) < 2 or len(nm) > len(line) + 1:
                continue
            if len(nm) == 2:
                pos = 0 if line == nm else -1
            else:
                pos = line.find(nm)
            if pos >= 0 and not any(used[pos:pos + len(nm)]):
                found.append((pos, len(nm), names[nm]))
                for k in range(pos, pos + len(nm)):
                    used[k] = True
        if not found and 4 <= len(line) <= 16:  # OCR 偶有错字：滑窗模糊匹配
            for nm in by_len:
                if len(nm) < 4 or len(set(nm) & set(line)) < 0.7 * len(set(nm)):
                    continue
                for pos in range(0, max(1, len(line) - len(nm) + 2)):
                    win = line[pos:pos + len(nm)]
                    if len(win) >= len(nm) - 1 and difflib.SequenceMatcher(None, win, nm).ratio() >= 0.82:
                        if not any(used[pos:pos + len(nm)]):
                            found.append((pos, len(nm), names[nm]))
                            for k in range(pos, pos + len(nm)):
                                used[k] = True
                        break
        if not found and getattr(data, "region", None) == "TW" and line == "法師":
            found = [(0, 2, "tw-mage")]
        for pos, ln, aid in found:
            ws = [words[owner[k]] for k in range(pos, min(pos + ln, len(owner)))]
            x0, y0 = min(w[1] for w in ws), min(w[2] for w in ws)
            x1, y1 = max(w[1] + w[3] for w in ws), max(w[2] + w[4] for w in ws)
            hits.append({"aid": aid, "cx": (x0 + x1) / 2 / W, "cy": (y0 + y1) / 2 / H})
    best = []
    for h0 in hits:
        grp = {}
        for h in hits:
            if abs(h["cy"] - h0["cy"]) <= 0.04:
                grp.setdefault(h["aid"], h)
        cand = sorted(grp.values(), key=lambda h: h["cx"])
        sep = [c for i, c in enumerate(cand) if i == 0 or c["cx"] - cand[i - 1]["cx"] > 0.08]
        if len(sep) > len(best):
            best = sep
    if not 2 <= len(best) <= 4:
        return []
    fixed = fix_taiwan_mage(data, best)
    return fixed if len(fixed) >= 2 else []


def fix_taiwan_mage(data, hits):
    """台服 2018「純粹 - 法師」的短标题容易被 OCR 读成 2087「大法師」。
    只在同一行另外至少两张都是白银时纠正，保留正常棱彩轮次。
    """
    if getattr(data, "region", None) != "TW":
        return hits
    silver = data.augment("2018") or {}
    if silver.get("rarity") != 0 or "2018" in {h["aid"] for h in hits}:
        return [h for h in hits if h["aid"] != "tw-mage"]
    ambiguous = {"2087", "tw-mage"}
    peers = [data.augment(h["aid"]) or {} for h in hits if h["aid"] not in ambiguous]
    confirmed = len(peers) >= 2 and all(p.get("rarity") == 0 for p in peers)
    return [dict(h, aid="2018") if confirmed and h["aid"] in ambiguous else h
            for h in hits if h["aid"] != "tw-mage" or confirmed]
