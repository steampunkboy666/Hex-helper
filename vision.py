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
    im = Image.frombuffer("RGBA", (ow, oh), arr.tobytes(), "raw", "BGRA", 0, 1).convert("RGB")
    if min_h and oh < min_h:
        k = min(2.0, min_h / oh, max_w / ow)
        if k > 1.05:
            im = im.resize((round(ow * k), round(oh * k)), Image.LANCZOS)
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

    def score(self, rect):
        """面板中央“商店底色”格子的占比（0~1）。"""
        a = grab(rect, self.GW, self.GH).astype(np.float32)
        return self.score_array(a[:, :, 2], a[:, :, 1], a[:, :, 0], self.regions(rect))

    def score_array(self, r, g, b, box):
        ys = slice(int(box[1] * r.shape[0]), int(box[3] * r.shape[0]))
        xs = slice(int(box[0] * r.shape[1]), int(box[2] * r.shape[1]))
        r, g, b = r[ys, xs], g[ys, xs], b[ys, xs]
        lum = (r + g + b) / 3
        like = (lum > 8) & (lum < 70) & (b < 0.75 * r + 6) & (g <= r + 8)
        self.last = float(like.mean())
        return self.last


# ---------------------------------------------------------------- 系统 OCR（Windows.Media.Ocr，经常驻 PowerShell 调用）
_OCR_PS = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Console]::InputEncoding = New-Object Text.UTF8Encoding $false
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language 'zh-Hans-CN')) }
if (-not $engine) { [Console]::Out.WriteLine('NOENGINE'); exit 2 }
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
    def __init__(self, work_dir):
        self.dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self.proc, self.q, self.ready, self.error, self.lang = None, queue.Queue(), False, None, None
        self.lock = threading.Lock()

    def start(self):
        script = os.path.join(self.dir, "ocr.ps1")
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(_OCR_PS)
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 | 0x4000)  # 无窗口 + 低于正常优先级（不和游戏抢 CPU）
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            first = self.q.get(timeout=25)
        except queue.Empty:
            self.error = "OCR 引擎启动超时"
            return False
        if first.startswith("READY"):
            self.ready, self.lang = True, first.split(" ", 1)[1]
            return True
        self.error = "系统没有可用的中文 OCR（设置→时间和语言→语言→添加简体中文，并勾选文字识别）"
        return False

    def _reader(self):
        for raw in self.proc.stdout:
            self.q.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        self.q.put("EOF")

    def recognize(self, png_path, timeout=10):
        """返回 [[(text,x,y,w,h), ...], ...]（每个内层列表是一行文字）。"""
        if not self.ready:
            return []
        with self.lock:
            while not self.q.empty():
                self.q.get_nowait()
            self.proc.stdin.write((png_path + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            lines, cur, end = [], None, time.time() + timeout
            self.last_error = None
            while time.time() < end:
                try:
                    s = self.q.get(timeout=max(0.05, end - time.time()))
                except queue.Empty:
                    break
                if s in ("END", "EOF"):
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
        for _ in range(4):  # 文件若仍被占用（杀毒软件扫描等）就换下一个文件名，不让整个识别线程因此崩溃
            self._n = (getattr(self, "_n", -1) + 1) % 4
            path = os.path.abspath(os.path.join(self.dir, "cap%d.png" % self._n))  # 必须是反斜杠的绝对路径
            try:
                w, h = save_png(rect, path, max_w, crop, min_h, masks)
                self.last_path = path
                break
            except OSError as e:
                self.last_error = "截图写入失败：%s" % e
        else:
            return [], 0, 0
        lines = self.recognize(path)
        try:
            with open(os.path.join(self.dir, "last_scan.txt"), "w", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S") + " 图 %dx%d 来自 %s\n" % (w, h, os.path.basename(path)))
                f.write("\n".join(" ".join(x[0] for x in ln) for ln in lines))
        except OSError:
            pass
        return lines, w, h

    def stop(self):
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _close_name(data, text):
    """短标题的容错匹配：Windows OCR 常把两三个字的短标题认错一个字（如“台风”认成“台虱”）。
    只在已知“这里有一张卡片”时使用：文字里含完整名称直接采用；否则找同长度、仅错一个字、且唯一的名称。"""
    names = data.augment_names()
    t = norm(text)
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


def recover_missing(ocr, data, path, hits, W, H):
    """三张卡片只认出两张时，按卡片等间距推算第三张的位置，把它的标题单独放大、二值化后再识别一次。"""
    if len(hits) != 2 or not path:
        return hits
    from PIL import Image, ImageOps
    a, b = sorted(hits, key=lambda h: h["cx"])
    d = b["cx"] - a["cx"]
    cands = [(a["cx"] + b["cx"]) / 2] if d > 0.3 else [a["cx"] - d, b["cx"] + d]
    cx = min(cands, key=lambda c: abs((a["cx"] + b["cx"] + c) / 3 - 0.5))
    if not 0.03 < cx < 0.97:
        return hits
    cy = (a["cy"] + b["cy"]) / 2
    aid = None
    try:
        g = ImageOps.grayscale(Image.open(path))
        p = os.path.join(os.path.dirname(path), "title.png")
        # 单次识别不稳定：换几种裁剪大小/阈值/放大倍数，哪次认出来就用哪次（每次约 0.03 秒）
        for hw, hh, thr, sc in ((0.05, 0.07, 120, 2), (0.05, 0.07, 150, 2), (0.11, 0.05, 150, 2), (0.07, 0.07, 120, 2),
                                (0.05, 0.05, 150, 3), (0.11, 0.07, 120, 3)):
            box = (int((cx - hw) * W), int((cy - hh) * H), int((cx + hw) * W), int((cy + hh) * H))
            t = g.crop(box).point(lambda v, thr=thr: 0 if v > thr else 255)  # 亮字 → 白底黑字
            t = t.resize((t.width * sc, t.height * sc), Image.LANCZOS)
            page = Image.new("L", (t.width + 200, t.height + 120), 255)
            page.paste(t, (100, 60))
            page.save(p)
            aid = _close_name(data, "".join(w[0] for ln in ocr.recognize(p) for w in ln))
            if aid and aid not in (a["aid"], b["aid"]):
                break
    except OSError:
        return hits
    if aid and aid not in (a["aid"], b["aid"]):
        return sorted(hits + [{"aid": aid, "cx": cx, "cy": cy}], key=lambda h: h["cx"])
    return hits


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
    return best[:3] if len(best) >= 2 else []
