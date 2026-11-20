"""通过控制台按钮、Qt 事件循环和后台线程验证对局外预览与缓存过期。"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea
import app
from data_source import DETAIL_TTL

q = QApplication([])
h = app.Helper(q, SimpleNamespace(demo=False, minimized=False))
button = next(b for b in h.control.findChildren(QPushButton) if b.text() == "预览效果")
button.click()
assert "尚未就绪" in h.control.log.toPlainText()
assert h.data.load_cached()
hid = h.data.hero_by_alias("Yone")
det = h.data.ensure_detail(hid, offline=True)
with patch("data_source.time.time", return_value=det["fetched_at"] + DETAIL_TTL + 1):
    assert h.data.detail_stale(hid)
h.data.meta["summary"] = {hid: h.data.summary(hid)}
h.control.resize(580, 480)
h.control.show()
q.processEvents()
assert h.control.findChild(QScrollArea).verticalScrollBar().maximum() > 0
calls = []
for name in ("show_shop", "show_offer", "dismiss"):
    original = getattr(h.side, name)
    def record(*args, fn=original, key=name):
        calls.append(key)
        return fn(*args)
    setattr(h.side, name, record)
button.click()
QTimer.singleShot(16500, q.quit)
q.exec()
assert "show_shop" in calls and "show_offer" in calls, calls
assert "dismiss" not in calls[calls.index("show_shop"):calls.index("show_offer")], calls
assert not h.side.isVisible()
print("PASS preview, expiry, scroll; panel calls:", calls)
h.quit()
