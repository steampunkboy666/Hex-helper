"""英雄联盟客户端本地接口（LCU）。仅在本机 127.0.0.1 通信，只读。"""
import base64
import http.client
import json
import random
import re
import ssl
import subprocess
import time

_NOWIN = 0x08000000  # CREATE_NO_WINDOW


class LCUError(Exception):
    pass


class LCU:
    def __init__(self, port, token):
        self.port = port
        self.auth = "Basic " + base64.b64encode(("riot:" + token).encode()).decode()
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    @staticmethod
    def discover():
        """从 LeagueClientUx.exe 的命令行读取端口和令牌；客户端没开则返回 None。"""
        ps = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\" "
              "| Select-Object -First 1 -ExpandProperty CommandLine")
        try:
            # 不使用 text=True：拿原始字节，自己解码，避免中文系统下 GBK 字节导致读取线程崩溃
            raw = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, timeout=15,
                                 creationflags=_NOWIN).stdout
        except Exception:  # noqa: BLE001
            return None
        try:
            out = (raw or b"").decode("utf-8")
        except UnicodeDecodeError:  # 中文系统的 PowerShell 可能仍输出 GBK
            out = (raw or b"").decode("gbk", errors="ignore")
        port = re.search(r"--app-port=(\d+)", out)
        token = re.search(r"--remoting-auth-token=([\w-]+)", out)
        return LCU(int(port.group(1)), token.group(1)) if port and token else None

    def get(self, path):
        """返回解析后的 JSON；404（如不在选人阶段）返回 None；连不上抛 LCUError。"""
        try:
            c = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=2.5, context=self.ctx)
            c.request("GET", path, headers={"Authorization": self.auth, "Accept": "application/json"})
            r = c.getresponse()
            body = r.read()
            c.close()
        except Exception as e:  # noqa: BLE001
            raise LCUError(str(e))
        if r.status == 404:
            return None
        if r.status != 200:
            raise LCUError("HTTP %s %s" % (r.status, path))
        return json.loads(body) if body else None


class DemoLCU:
    """不依赖游戏客户端的演示：模拟 空闲 → 选人（含一次重roll）→ 游戏开始。"""

    def __init__(self, hero_ids):
        rnd = random.Random()
        pool = rnd.sample(hero_ids, 12)
        self.mine, self.bench1, self.bench2 = pool[0], pool[1:6], pool[1:5] + [pool[6]]
        self.t0 = time.time()

    def _t(self):
        return time.time() - self.t0

    def get(self, path):
        t = self._t()
        if path.endswith("gameflow-phase"):
            return "None" if t < 2 else "ChampSelect" if t < 30 else "InProgress"
        if path.endswith("/session") and "champ-select" in path:
            bench = self.bench1 if t < 14 else self.bench2
            return {"benchEnabled": True, "localPlayerCellId": 0,
                    "myTeam": [{"cellId": 0, "championId": self.mine}],
                    "benchChampions": [{"championId": int(c)} for c in bench]}
        if path.endswith("gameflow/v1/session"):
            return {"gameData": {"queue": {"id": 2400}}}
        return None
