"""商店自动收起和海克斯部分识别的回归测试。"""
from pathlib import Path
import sys
from types import SimpleNamespace as State
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import vision


def hits(*positions):
    return [{"aid": str(i), "cx": x, "cy": 0.5} for i, x in enumerate(positions, 1)]


class ShopTests(unittest.TestCase):
    def test_closes_without_mouse_event_and_ignores_one_bad_frame(self):
        watch = vision.ShopWatch()
        self.assertFalse(watch.ready(100, 1, True))
        self.assertTrue(watch.ready(100.5, 1, True))
        self.assertIsNone(watch.observe(100.5, 0.8, True))
        self.assertTrue(watch.ready(102.5, 1, True))
        self.assertIsNone(watch.observe(102.5, 0.2, True))
        self.assertTrue(watch.ready(103, 1, True))
        self.assertIsNone(watch.observe(103, None, True))
        self.assertTrue(watch.ready(105, 1, True))
        self.assertIsNone(watch.observe(105, 0.15, True))
        self.assertTrue(watch.ready(105.5, 1, True))
        self.assertIs(watch.observe(105.5, 0.1, True), False)
        self.assertFalse(watch.ready(106, 2, False))

    def test_clicked_close_is_checked_early_and_reopen_requires_an_intent(self):
        watch = vision.ShopWatch()
        watch.ready(100, 1, True)
        watch.observe(100.5, 0.8, True)
        self.assertFalse(watch.ready(100.7, 1, True, click=True))
        self.assertTrue(watch.ready(101.2, 1, True))
        self.assertIsNone(watch.observe(101.2, 0.1, True))
        self.assertIs(watch.observe(101.8, 0.1, True), False)
        self.assertFalse(watch.ready(102, 2, False))
        self.assertFalse(watch.ready(103, 2, False, open_request=True))
        self.assertTrue(watch.ready(103.5, 2, False))
        self.assertIs(watch.observe(103.5, 0.8, False), True)

    def test_shop_snapshot_and_stale_verdict(self):
        sensor = vision.ShopSensor()
        rgb = np.load(Path(__file__).parent / "fixtures" / "shop_open_2560_96x54.npy")
        rect = (0, 0, 2560, 1440)
        sc = sensor.score_array(*rgb.transpose(2, 0, 1), sensor.regions(rect))
        self.assertGreaterEqual(sc, sensor.OPEN)
        screenshot = Image.fromarray(rgb.astype(np.uint8)).resize(rect[2:])

        def sample(region, width, height):
            x, y, w, h = region
            crop = screenshot.crop((x, y, x + w, y + h)).resize((width, height), Image.Resampling.BOX)
            return np.asarray(crop.convert("RGBA"))[:, :, [2, 1, 0, 3]]

        with patch.object(vision, "grab", side_effect=sample):
            self.assertGreaterEqual(sensor.score(rect), sensor.OPEN)
            self.assertGreaterEqual(sensor.score(rect, (0.19, 0.78)), sensor.OPEN)
        with patch.object(vision, "grab", return_value=np.zeros((36, 64, 4), dtype=np.uint8)):
            self.assertIsNone(sensor.score(rect))
        helper = State(shop_revision=3, live={}, set_shop=Mock())
        with patch.object(app.winfx, "foreground_is_game", return_value=True):
            app.Helper.on_shop_scan(helper, 2, "early capture", False)
            helper.set_shop.assert_not_called()
            app.Helper.on_shop_scan(helper, 3, "closed", False)
        helper.set_shop.assert_called_once_with(False, "closed")


class OfferTests(unittest.TestCase):
    def test_title_centers_normal_ultrawide_and_four_cards(self):
        for left, center, right in ((0.27, 0.50, 0.73), (0.38, 0.50, 0.62)):
            for present, expected in (((left, right), center), ((left, center), right), ((center, right), left)):
                (missing,) = vision.missing_title_centers(hits(*present))
                self.assertAlmostEqual(missing, expected)
            self.assertEqual(vision.missing_title_centers(hits(left, center, right)), [])
        four = (0.14, 0.38, 0.62, 0.86)
        for missing, expected in enumerate(four):
            (found,) = vision.missing_title_centers(hits(*(p for i, p in enumerate(four) if i != missing)))
            self.assertAlmostEqual(found, expected)

    def test_wider_scan_recovers_partial_and_later_dropout_keeps_full_offer(self):
        first, third = hits(0.26, 0.50, 0.74)[::2]
        full = hits(0.26, 0.50, 0.74)
        crop = (0, 0, 1, 1)
        now = 100.0

        def tick(timeout):
            nonlocal now
            now += 7.0

        ocr = State(ready=True, dead=lambda: False, last_error=None,
                    ocr_screen=Mock(side_effect=[([first, third], 300, 100), (full, 300, 100),
                                                 ([first, third], 300, 100), ([], 300, 100)]))
        helper = State(args=State(demo=False), stop=State(is_set=Mock(side_effect=[False, False, True])),
                       ocr=ocr, vq=State(get=tick), data=object(), live={"time": 3, "level": 3},
                       game_no=1, shop_revision=0, shop_state=False, shop_manual=False, last_click=None, log=Mock(),
                       settings={"augment_ocr": True, "overlay_safe": True}, side=State(phys_rect=None),
                       bus=State(offer=State(emit=Mock()), layout=State(emit=Mock())),
                       aug_crops=lambda rect: ((crop, 1920, 300), (crop, 1280, 0)))
        with (patch.object(app.winfx, "game_rect", return_value=(0, 0, 1920, 1080)),
              patch.object(app.winfx, "foreground_is_game", return_value=True),
              patch.object(app, "time", State(time=lambda: now)),
              patch.object(vision, "find_augments", side_effect=lambda _, lines, w, h: lines),
              patch.object(vision, "recover_missing", side_effect=lambda o, d, p, cards, w, h: cards)):
            app.Helper.vision_loop(helper)
        self.assertEqual(ocr.ocr_screen.call_count, 4)
        helper.bus.offer.emit.assert_called_once_with(full)


if __name__ == "__main__":
    unittest.main(verbosity=2)
