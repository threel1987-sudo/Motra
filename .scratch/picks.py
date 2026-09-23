#!/usr/bin/env python3
"""生成选品清单图:按页面分组展示挑中的素材"""
from PIL import Image, ImageDraw, ImageFont

BG = (13, 9, 32); GOLD = (227, 194, 132); DIM = (167, 159, 202)
TILE = 220; PAD = 10; LABEL_W = 150; ROW_H = TILE + 46
FONT_LABEL = ImageFont.truetype("/workspace/.scratch/fusion-pixel.ttf", 22)
FONT_CAP = ImageFont.truetype("/workspace/.scratch/fusion-pixel.ttf", 15)

rows = [
    ("登录页", [
        ("walls/43f95eb74f9fe5910e2b999786715dee.jpg", "背景·罗盘垂月"),
        ("borders/奇迹素材52.png", "卡框·双星拱门"),
        ("night/gold-05.webp", "分隔·复用gold-05"),
        ("borders/奇迹素材60.png", "顶饰·弯月星辰"),
    ]),
    ("房间设置", [
        ("walls/5fc18d642de6e3037eb90efa4381f0f6.jpg", "背景·湖畔黄昏"),
        ("borders/奇迹素材71.png", "头饰·小星球"),
        ("night/gold-06.webp", "分隔·复用gold-06"),
    ]),
    ("个人信息/设置", [
        ("borders/奇迹素材17.png", "装饰·三星光"),
        ("borders/奇迹素材86.png", "分隔·星光线"),
    ]),
    ("备用席", [
        ("walls/6a2c9a8206b8be362f8698b8837095f1.jpg", "奶油金框卡面"),
        ("walls/a4fd0535604774d08470848a8ced835f.jpg", "奶油紫框卡面"),
        ("walls/dea764f07d4c8934d6938aff83245886.jpg", "星图"),
        ("cards/店铺;小木美工素材 (76).png", "塔罗·星爆框"),
        ("cards/店铺;小木美工素材 (68).png", "塔罗·罗盘垂链"),
    ]),
]

W = LABEL_W + 5 * (TILE + PAD) + PAD
H = len(rows) * ROW_H + PAD
sheet = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(sheet)

for r, (label, items) in enumerate(rows):
    y0 = PAD + r * ROW_H
    draw.text((16, y0 + ROW_H // 2 - 12), label, fill=GOLD, font=FONT_LABEL)
    draw.line([(LABEL_W - 8, y0), (LABEL_W - 8, y0 + ROW_H - 8)], fill=(60, 50, 100))
    for c, (path, cap) in enumerate(items):
        x0 = LABEL_W + PAD + c * (TILE + PAD)
        try:
            im = Image.open(path).convert("RGBA")
            im.thumbnail((TILE - 8, TILE - 34), Image.LANCZOS)
            sheet.paste(im, (x0 + (TILE - im.width) // 2, y0 + 24 + (TILE - 34 - im.height) // 2), im)
        except Exception as e:
            print("skip", path, e)
        draw.rectangle([x0, y0 + 4, x0 + TILE, y0 + ROW_H - 8], outline=(60, 50, 100))
        draw.text((x0 + 6, y0 + ROW_H - 28), cap, fill=DIM, font=FONT_CAP)

sheet.save("/workspace/素材/选品清单.jpg", quality=88)
print("OK -> /workspace/素材/选品清单.jpg")
