"""Win32 小工具：游戏窗口定位、悬浮窗样式（点击穿透/不抢焦点/不被截屏）、按键状态。
只读取按键状态和屏幕像素，不注入、不读写游戏内存。"""
import ctypes
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80000, 0x80, 0x08000000
HWND_TOPMOST = wt.HWND(-1)
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x2, 0x1, 0x10
WDA_EXCLUDEFROMCAPTURE = 0x11  # Win10 2004+：窗口不出现在截屏里，避免面板被自己的画面识别看到


def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def game_hwnd():
    h = user32.FindWindowW("RiotWindowClass", None) or user32.FindWindowW(None, "League of Legends (TM) Client")
    return h if h and user32.IsWindowVisible(h) else None


def game_rect():
    """游戏画面（客户区）在屏幕上的 (left, top, width, height)；没找到游戏返回 None。"""
    h = game_hwnd()
    if not h or user32.IsIconic(h):
        return None
    r, p = wt.RECT(), wt.POINT(0, 0)
    user32.GetClientRect(h, ctypes.byref(r))
    user32.ClientToScreen(h, ctypes.byref(p))
    return (p.x, p.y, r.right - r.left, r.bottom - r.top) if r.right > 0 else None


def _class_name(h):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, buf, 64)
    return buf.value


def _visible_bounds(h):
    """窗口真正可见的范围（去掉 Win10 的隐形阴影边）；失败退回 GetWindowRect。"""
    r = wt.RECT()
    try:
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0 and r.right > r.left:
            return r
    except Exception:  # noqa: BLE001
        pass
    user32.GetWindowRect(h, ctypes.byref(r))
    return r


def _cloaked(h):
    v = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(h, 14, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:  # noqa: BLE001
        pass
    return bool(v.value)


_ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [_ENUMPROC, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


def client_rect():
    """英雄联盟客户端（大厅/选人界面）窗口在屏幕上的可见范围 (left, top, width, height)。
    客户端有好几个同类窗口（有的是隐藏的空壳），所以逐个枚举，取可见、没被最小化、没被隐藏的里面最大的那个。
    没开或最小化返回 None。"""
    best = []

    def cb(h, _):
        try:
            if _class_name(h) == "RCLIENT" and user32.IsWindowVisible(h) and not user32.IsIconic(h) and not _cloaked(h):
                r = _visible_bounds(h)
                if r.right - r.left > 300 and r.bottom - r.top > 200:
                    best.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        user32.EnumWindows(_ENUMPROC(cb), 0)
    except Exception:  # noqa: BLE001
        return None
    return max(best, key=lambda b: b[2] * b[3]) if best else None


def game_exe_path():
    """游戏进程的可执行文件路径（用来找到游戏自己的设置文件）；失败返回 None。"""
    h = game_hwnd()
    if not h:
        return None
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    hp = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        return buf.value if k.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(size)) else None
    finally:
        k.CloseHandle(hp)


def screen_rect():
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def foreground_is_game():
    h = game_hwnd()
    return bool(h) and user32.GetForegroundWindow() == h


def overlay_style(hwnd, click_through=True, exclude_capture=False):
    """悬浮窗：不抢焦点、不出现在任务栏；可选点击穿透、不被截屏。"""
    hwnd = wt.HWND(int(hwnd))
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
    if click_through:
        ex |= WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
    if exclude_capture:
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    raise_topmost(hwnd)


def raise_topmost(hwnd):
    h = hwnd if isinstance(hwnd, wt.HWND) else wt.HWND(int(hwnd))
    user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


_VK = {"ESC": 0x1B, "ENTER": 0x0D, "TAB": 0x09, "SPACE": 0x20, "LBUTTON": 0x01, "CTRL": 0x11, "SHIFT": 0x10,
       "ALT": 0x12, "[": 0xDB, "]": 0xDD, ",": 0xBC, ".": 0xBE, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28}


def vk(name):
    n = name.strip().upper()
    if n in _VK:
        return _VK[n]
    if n.startswith("F") and n[1:].isdigit():
        return 0x6F + int(n[1:])
    return ord(n[0]) if len(n) == 1 else 0


def key_down(code):
    return bool(code and user32.GetAsyncKeyState(code) & 0x8000)


def key_hit(code):
    """按着，或者自上次查询以来按过（很快的点击按下不到一个轮询周期，只看“按着”会漏掉）。"""
    return bool(code and user32.GetAsyncKeyState(code) & 0x8001)


def parse_hotkey(text):
    """'Ctrl+Alt+S' → (0x11, 0x12, 0x53)；识别失败返回空元组。"""
    codes = tuple(vk(p) for p in str(text).split("+") if p.strip())
    return codes if all(codes) else ()


def hotkey_down(codes):
    return bool(codes) and all(key_down(c) for c in codes)


def lower_priority():
    """本程序降到“低于正常”优先级：游戏永远优先拿 CPU。"""
    try:
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, wt.DWORD]
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:  # noqa: BLE001
        pass


def cursor_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y
