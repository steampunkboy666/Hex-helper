"""26.23 强化选择回归。真实引擎读取合成标题，验证简繁模式与第四个点击。"""
import os
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image, ImageDraw, ImageFont
import advisor
import app
import config
import vision
from data_source import Data

aids = ["3262", "3263", "3264", "3265"]
for region in ("CN", "TW"):
    data = Data(config.CACHE_DIR, region)
    assert data.load_cached() and all(data.augment(a) for a in aids), "需先更新本区强化名录"
    with tempfile.TemporaryDirectory() as directory:
        ocr = vision.OcrWorker(directory, region)
        with patch.object(ocr, "_start_windows", return_value=False):
            assert ocr.start(), ocr.error
        for width in (1280, 1920):
            height = width // 5
            img = Image.new("RGB", (width, height), "#241d30")
            draw = ImageDraw.Draw(img)
            font = ImageFont.truetype("C:/Windows/Fonts/msjh.ttc", width // 44)
            for idx, aid in enumerate(aids):
                draw.text((int((idx + .5)*width/4), height//2), data.augment_name(aid),
                          font=font, fill="white", anchor="mm")
            path = os.path.join(directory, "offer.bmp")
            img.save(path)
            hits = vision.find_augments(data, ocr.recognize(path), width, height)
            hits = vision.recover_missing(ocr, data, path, hits, width, height)
            found = [h["aid"] for h in hits]
            print(region, width, ocr.engine_label, [data.augment_name(a) for a in found], flush=True)
            assert found == aids, found
            partial = vision.recover_missing(ocr, data, path, hits[:3], width, height)
            assert [h["aid"] for h in partial] == aids, partial
        rec = advisor.evaluate_offer(data, None, aids, [])
        assert [x["aid"] for x in rec["options"]] == aids
        assert all(x.get("order") is None for x in rec["options"])
        state = SimpleNamespace(last_click=(1680, 480, time.time()), offer=hits, chosen=[],
                                CARD_Y=app.Helper.CARD_Y, REROLL_Y=app.Helper.REROLL_Y, HIDE_Y=app.Helper.HIDE_Y,
                                data=data, log=print, toast=SimpleNamespace(say=lambda *a: None))
        state.click_zone = lambda r, c: app.Helper.click_zone(state, r, c)
        with patch.object(app.winfx, "game_rect", return_value=(0, 0, 1920, 1080)):
            assert app.Helper.record_choice(state) == aids[-1]
        ocr.stop()
