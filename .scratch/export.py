#!/usr/bin/env python3
"""导出选中素材到 web/assets/night/:压缩到网页尺寸,转 webp,命名沿用现有约定"""
import os
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # 素材是 1 亿像素级印刷原图,先解禁再压缩

OUT = "/workspace/web/assets/night"
WALLS = "/workspace/.scratch/walls"
BORDERS = "/workspace/.scratch/borders"
CARDS = "/workspace/.scratch/cards"

# 参考:现有素材的尺寸
print("── 现有素材参考 ──")
for f in ["gold-05.webp", "gold-08.webp", "gold-11.webp", "sky-cloud.webp", "tarot-01.webp"]:
    p = os.path.join(OUT, f)
    if os.path.exists(p):
        im = Image.open(p)
        print(f"  {f}: {im.size} {os.path.getsize(p)//1024}KB")

def save(im, name, max_w=None, max_h=None, q=85):
    w, h = im.size
    scale = 1.0
    if max_w and w > max_w: scale = min(scale, max_w / w)
    if max_h and h > max_h: scale = min(scale, max_h / h)
    if scale < 1.0:
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    out = os.path.join(OUT, name)
    im.save(out, "WEBP", quality=q, method=6)
    print(f"  {name}: {im.size} {os.path.getsize(out)//1024}KB")

print("── 导出 ──")
# 壁纸(无 alpha,转 RGB)
save(Image.open(f"{WALLS}/43f95eb74f9fe5910e2b999786715dee.jpg").convert("RGB"), "sky-compass.webp", max_h=1600, q=84)
save(Image.open(f"{WALLS}/5fc18d642de6e3037eb90efa4381f0f6.jpg").convert("RGB"), "sky-lake.webp", max_h=1600, q=84)
# 金框(保留 alpha)
save(Image.open(f"{BORDERS}/奇迹素材52.png"), "gold-13.webp", max_h=1400)   # 双星拱门
save(Image.open(f"{BORDERS}/奇迹素材60.png"), "gold-14.webp", max_w=900)    # 弯月星辰
save(Image.open(f"{BORDERS}/奇迹素材71.png"), "gold-15.webp", max_w=900)    # 小星球
save(Image.open(f"{BORDERS}/奇迹素材17.png"), "gold-16.webp", max_w=900)    # 三星光
save(Image.open(f"{BORDERS}/奇迹素材86.png"), "gold-17.webp", max_w=1200)   # 星光线
# 塔罗白描备用(保留 alpha)
save(Image.open(f"{CARDS}/店铺;小木美工素材 (76).png"), "tarot-09.webp", max_h=1400)  # 星爆框
save(Image.open(f"{CARDS}/店铺;小木美工素材 (68).png"), "tarot-10.webp", max_h=1400)  # 罗盘垂链
print("完成")
