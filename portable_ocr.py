"""离线中文 OCR。只在选定区服的系统识别引擎不可用时加载。"""
import os
from pathlib import Path

from PIL import Image, ImageOps

import config


class PortableOCR:
    def __init__(self, language):
        # 控制 OpenMP 线程数，避免在低配置电脑上与游戏争用 CPU。
        os.environ.setdefault("OMP_THREAD_LIMIT", "1")
        import tesserocr

        self.lib = tesserocr
        model = "chi_tra" if language == "zh-Hant-TW" else "chi_sim"
        self.api = tesserocr.PyTessBaseAPI(
            path=str(Path(config.RES_DIR) / "assets" / "tessdata"),
            lang=model, psm=tesserocr.PSM.SINGLE_BLOCK,
        )

    def read(self, path):
        with Image.open(path) as src:
            image = ImageOps.grayscale(src)
        # 标题通常是深色卡片上的亮字；单卡补认传入的是白底黑字。
        if image.resize((1, 1)).getpixel((0, 0)) < 128:
            image = ImageOps.invert(image)
        self.api.SetImage(image)
        lines = []
        for record in self.api.GetTSVText(0).splitlines():
            fields = record.split("\t", 11)
            if len(fields) != 12 or fields[0] != "5" or not fields[11].strip():
                continue
            key = tuple(fields[1:5])
            x, y, w, h = map(int, fields[6:10])
            previous = lines[-1][1][-1] if lines else None
            separated = previous and x - previous[1] - previous[3] > max(2 * h, image.width * 0.035)
            if not lines or lines[-1][0] != key or separated:
                lines.append((key, []))
            lines[-1][1].append((fields[11], x, y, w, h))
        return [words for _, words in lines]

    def close(self):
        self.api.End()
