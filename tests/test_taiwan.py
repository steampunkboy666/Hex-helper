"""离线验证简繁强化的游戏 ID、评分、缓存和 OCR 匹配。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import advisor
import vision
from data_source import Data


class TaiwanTest(unittest.TestCase):
    def test_offer(self):
        with tempfile.TemporaryDirectory() as root:
            cn = Data(root)
            tw = Data(root, "TW")
            self.assertNotEqual(cn.meta_path, tw.meta_path)
            tw.meta["augments"] = {}
            lines = []
            for index, (name, rules_name) in enumerate([
                    ("泰坦的決意", "泰坦的坚决"), ("頂尖發明家", "尖端发明家"), ("劍舞之心", "利刃华尔兹")]):
                rules = {"name": rules_name, "desc": "增加攻击力"}
                tw.meta["augments"][str(index)] = {"name": name, "desc": "增加物理攻擊", "rules": rules, "rarity": 1}
                rated = advisor.rate_augment(tw, None, str(index))
                self.assertEqual(rated["name"], name)
                self.assertTrue(rated["tags"])
                self.assertEqual(rated["tags"], advisor.aug_tags(rules))
                lines.append([(name, 100 + index * 350, 100, 150, 30)])
            self.assertEqual([h["aid"] for h in vision.find_augments(tw, lines, 1100, 400)], ["0", "1", "2"])


if __name__ == "__main__":
    unittest.main()
