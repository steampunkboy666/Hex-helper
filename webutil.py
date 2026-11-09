"""网络与解析工具：限速+gzip 的 HTTP、JS 字面量解析、Next.js(RSC) 载荷解码。"""
import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request

# 如实标明是哪个程序、去哪里找到作者（已实测 aramgg/op.gg/腾讯/图标站都不拦这种标识）
VERSION = "1.1"
REPO = "steampunkboy666/Hex-helper"
UA = "HexHelper/%s (+https://github.com/%s)" % (VERSION, REPO)
MIN_GAP = {"aramgg.com": 0.4, "op.gg": 1.0, "www.douhunwiki.cn": 0.2}  # 同一主机两次请求的最小间隔（秒），避免高频抓取
_last, _lock = {}, threading.Lock()


def _throttle(url):
    host = url.split("/")[2]
    gap = MIN_GAP.get(host, 0)
    if not gap:
        return
    with _lock:
        wait = _last.get(host, 0) + gap - time.time()
        _last[host] = max(time.time(), _last.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def http_get(url, binary=False, timeout=25, retries=2, headers=None):
    """GET（跟随跳转、自动解压 gzip、按主机限速）。404 等 HTTP 错误不重试。"""
    last = None
    for i in range(retries + 1):
        try:
            _throttle(url)
            h = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept-Language": "zh-CN,zh;q=0.9"}
            h.update(headers or {})
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            return data if binary else data.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1 + i)
    raise last


# ---------------------------------------------------------------- JS 字面量解析
class _JS:
    """解析压缩后的 JS 数据模块里的字面量：对象/数组/反引号字符串/数字/JSON.parse(`..`)。"""

    NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
    KEY = re.compile(r"[A-Za-z_$][\w$]*")
    ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def __init__(self, s, i=0):
        self.s, self.i = s, i

    def ws(self):
        s = self.s
        while self.i < len(s) and s[self.i] in " \t\r\n":
            self.i += 1

    def value(self):
        self.ws()
        s, c = self.s, self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "`\"'":
            return self.string()
        if s.startswith("JSON.parse(", self.i):
            self.i += 11
            v = json.loads(self.string())
            self.ws()
            self.i += 1  # )
            return v
        m = self.NUM.match(s, self.i)
        if m:
            self.i = m.end()
            return float(m.group()) if re.search(r"[.eE]", m.group()) else int(m.group())
        for lit, val in (("!0", True), ("!1", False), ("true", True), ("false", False),
                         ("null", None), ("void 0", None), ("undefined", None)):
            if s.startswith(lit, self.i):
                self.i += len(lit)
                return val
        raise ValueError("无法解析 JS 字面量 @%d: %r" % (self.i, s[self.i:self.i + 40]))

    def string(self):
        s, q = self.s, self.s[self.i]
        self.i += 1
        out = []
        while s[self.i] != q:
            c = s[self.i]
            if c == "\\":
                self.i += 1
                c = s[self.i]
                if c == "u":
                    out.append(chr(int(s[self.i + 1:self.i + 5], 16)))
                    self.i += 4
                else:
                    out.append(self.ESC.get(c, c))
            else:
                out.append(c)
            self.i += 1
        self.i += 1
        return "".join(out)

    def obj(self):
        self.i += 1
        d = {}
        while True:
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                return d
            if self.s[self.i] in "`\"'":
                k = self.string()
            else:
                m = self.KEY.match(self.s, self.i) or self.NUM.match(self.s, self.i)
                k, self.i = m.group(), m.end()
            self.ws()
            self.i += 1  # :
            d[k] = self.value()
            self.ws()
            if self.s[self.i] == ",":
                self.i += 1

    def arr(self):
        self.i += 1
        a = []
        while True:
            self.ws()
            if self.s[self.i] == "]":
                self.i += 1
                return a
            a.append(self.value())
            self.ws()
            if self.s[self.i] == ",":
                self.i += 1


def parse_module(text):
    """解析形如 `var e=<字面量>;export{e as default};` 的数据模块。"""
    m = re.search(r"var\s+\w+\s*=\s*", text)
    return _JS(text, m.end()).value()


def decode_rsc(html):
    """还原 Next.js 页面里 self.__next_f.push([1,"..."]) 流式载荷拼成的文本。"""
    bs = chr(92)
    key = 'self.__next_f.push([1,"'
    out, i = [], 0
    while True:
        i = html.find(key, i)
        if i < 0:
            break
        j = k = i + len(key)
        while True:
            k = html.find('"])', k)
            if k < 0:
                break
            b = 0
            while html[k - 1 - b] == bs:
                b += 1
            if b % 2 == 0:
                break
            k += 1
        if k < 0:
            break
        try:
            out.append(json.loads('"' + html[j:k] + '"'))
        except ValueError:
            pass
        i = k
    return "".join(out)


# ---------------------------------------------------------------- 新版本检查
def _ver(tag):
    """'v1.2.3' -> (1, 2, 3)；解析不了返回 ()。"""
    return tuple(int(x) for x in re.findall(r"\d+", tag or "")[:4])


def latest_release(timeout=8):
    """查 GitHub 上最新发布的版本。比当前版本新才返回 (版本号, 下载页地址)，否则 None。"""
    data = json.loads(http_get("https://api.github.com/repos/%s/releases/latest" % REPO, timeout=timeout,
                               retries=0, headers={"Accept": "application/vnd.github+json"}))
    tag = data.get("tag_name", "")
    if _ver(tag) > _ver(VERSION):
        return tag, data.get("html_url") or "https://github.com/%s/releases" % REPO
    return None
