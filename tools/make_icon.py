"""生成应用图标 assets/icon.ico（海克斯六边形 + “海”字）。"""
import math, os
from PIL import Image, ImageDraw, ImageFont
S = 256
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
pts = [(S / 2 + 120 * math.cos(math.radians(60 * i - 30)), S / 2 + 120 * math.sin(math.radians(60 * i - 30))) for i in range(6)]
d.polygon(pts, fill=(176, 138, 58, 255))
inner = [(S / 2 + 106 * math.cos(math.radians(60 * i - 30)), S / 2 + 106 * math.sin(math.radians(60 * i - 30))) for i in range(6)]
for k in range(40):  # 简单的纵向金色渐变
    c = (int(243 - k * 1.7), int(217 - k * 2.0), int(139 - k * 1.5), 255)
    d.polygon([(x, y) for x, y in inner], fill=c) if k == 0 else None
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    gd.line([(0, y), (S, y)], fill=(int(246 - 70 * t), int(220 - 82 * t), int(140 - 82 * t), 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).polygon(inner, fill=255)
im.paste(grad, (0, 0), mask)
font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 130)
d.text((S / 2, S / 2 + 4), "海", font=font, fill=(16, 32, 42, 255), anchor="mm")
os.makedirs("assets", exist_ok=True)
im.save("assets/icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
im.save("assets/icon.png")
