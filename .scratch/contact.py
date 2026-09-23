#!/usr/bin/env python3
"""拼缩略图全家福:把目录里的图片拼成带编号的大图,方便快速挑选素材"""
import sys, os
from PIL import Image, ImageDraw

src_dir, out_prefix = sys.argv[1], sys.argv[2]
cols, tile_w, tile_h, per_sheet = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
bg = (16, 12, 40)  # 深空底色,近似页面 #0d0920

exts = (".png", ".jpg", ".jpeg", ".webp")
files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith(exts))
print(f"共 {len(files)} 张")

mapping = []
for sheet_i in range(0, len(files), per_sheet):
    batch = files[sheet_i:sheet_i + per_sheet]
    rows = (len(batch) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), bg)
    draw = ImageDraw.Draw(sheet)
    for i, fname in enumerate(batch):
        idx = sheet_i + i
        mapping.append(f"{idx:03d} {fname}")
        try:
            im = Image.open(os.path.join(src_dir, fname)).convert("RGBA")
            im.thumbnail((tile_w - 12, tile_h - 30), Image.LANCZOS)
            x = (i % cols) * tile_w + (tile_w - im.width) // 2
            y = (i // cols) * tile_h + 22 + (tile_h - 30 - im.height) // 2
            sheet.paste(im, (x, y), im)
        except Exception as e:
            print(f"跳过 {fname}: {e}")
        draw.text(((i % cols) * tile_w + 6, (i // cols) * tile_h + 5), f"#{idx:03d}", fill=(227, 194, 132))
        draw.rectangle([(i % cols) * tile_w, (i // cols) * tile_h, (i % cols + 1) * tile_w - 1, (i // cols + 1) * tile_h - 1], outline=(60, 50, 100))
    out = f"{out_prefix}_{sheet_i // per_sheet}.jpg"
    sheet.save(out, quality=82)
    print("->", out)

with open(f"{out_prefix}_mapping.txt", "w") as f:
    f.write("\n".join(mapping))
