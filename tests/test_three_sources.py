"""Validate alternate data source, OCR disambiguation, fixed panel, and local feedback."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication
import advisor
import config
from data_source import Data
import feedback
import ui
import vision


def verify(directory):
    data = Data(config.CACHE_DIR, "TW")
    assert data.load_cached()
    named = [(aid, row["name"]) for aid, row in data.meta["augments"].items()
             if row["rarity"] == 0 and aid != "2018"][:2]
    def read(name, peers):
        titles = [peers[0][1], name, peers[1][1]]
        return [h["aid"] for h in vision.find_augments(
            data, [[(text, x, 100, 36, 18)] for text, x in zip(titles, (100, 400, 700))], 900, 240)]
    for name in ("純粹 - 法師", "法師", "大法師"):
        assert read(name, named) == [named[0][0], "2018", named[1][0]]
    rainbow = [(aid, row["name"]) for aid, row in data.meta["augments"].items()
               if row["rarity"] == 2 and aid != "2087"][:2]
    assert read("大法師", rainbow) == [rainbow[0][0], "2087", rainbow[1][0]]
    print("PASS 台服白银和棱彩的歧义回归")

    cn = Data(config.CACHE_DIR)
    assert cn.load_cached()
    hero = str(next(iter(cn.meta["summary"])))
    seen = []
    def fail(name):
        def rejecting(*args):
            seen.append(name)
            raise OSError(name)
        return rejecting
    copy = Data(directory)
    with patch.object(copy, "_meta_aramgg", fail("aramgg")), patch.object(copy, "_meta_haidou", fail("haidou")), \
            patch.object(copy, "_meta_opgg", lambda cached: (seen.append("opgg"), cn.meta)[1]):
        copy.load()
    assert seen == ["aramgg", "haidou", "opgg"]
    print("PASS 三源降级顺序")

    q = QApplication([])
    settings = config.Settings()
    settings.v.update(offer_scale=1.0, ui_scale=1.0)
    ctl = ui.ControlWindow(settings, settings["hotkeys"])
    panel = ui.SidePanel(ui.Ctx(cn, ui.ImageLoader(config.CACHE_DIR), settings))
    hid = cn.hero_by_alias("Yone") or hero
    cn.ensure_detail(hid, offline=True)
    rows = advisor.best_augments(cn, hid)
    options = [entry["aid"] for _, group in rows for entry in group][:4]
    assert len(options) >= 3
    ev = advisor.evaluate_offer(cn, hid, options, [])
    rect = (0, 0, 2048, 1152)
    with patch.object(ui, "logical_game", return_value=({"dpr": 1.25, "native": rect, "logical": rect}, rect)), \
            patch.object(ui.SidePanel, "_present", lambda self: None):
        def size(value):
            settings.v["offer_scale"] = value
            panel.show_offer(hid, ev, rect)
            return panel.frame.width(), panel.ctx.S(14)
        small, large = size(.8), size(1.4)
        assert large[0] > small[0] and large[1] > small[1], (small, large)
        print("PASS 强化面板实际尺寸", small, large)
    helper = SimpleNamespace(control=ctl, data=cn, settings=settings, phase="None",
                             ocr=SimpleNamespace(engine_label="系统 OCR", ready=True), last_checks=[])
    wizard = feedback.FeedbackDialog(helper, str)
    assert not wizard.image_page.isComplete()
    path = Path(directory)
    Image.new("RGB", (400, 240), "black").save(path / "source.png")
    wizard.screenshot = feedback.read_screenshot(path / "source.png")
    assert wizard.image_page.isComplete()
    wizard.description.setPlainText("测试反馈")
    assert wizard.page(1).isComplete()
    feedback.save_package(path / "report.zip", wizard.screenshot, "测试反馈", wizard.technical)
    with zipfile.ZipFile(path / "report.zip") as bundle:
        assert sorted(bundle.namelist()) == ["description.txt", "diagnostics.txt", "screenshot.jpg"]
        assert bundle.read("description.txt").decode().strip() == "测试反馈"
        assert str(path) not in bundle.read("diagnostics.txt").decode()
    print("PASS 反馈向导与 ZIP")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        verify(directory)
