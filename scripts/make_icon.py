#!/usr/bin/env python3
"""
make_icon.py — 用代码生成 AnyLoc 应用图标。
设计：白色圆角方块底 + 蓝色定位大头针 + 底部 "AnyLoc"（Any 深黑 / Loc 蓝）。
产物：
  web/icon-256.png   预览图（大图，给人看）
  web/icon.ico       多尺寸 Windows 图标（16/32/48/64/128/256）
运行： python scripts/make_icon.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

# this script lives in scripts/, so web/ is one level up (project root)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "web")
os.makedirs(OUT_DIR, exist_ok=True)

# 高分辨率画布，最后缩放到各尺寸（抗锯齿更好）
S = 1024
BLUE = (26, 115, 232, 255)       # #1a73e8  和 UI 一致
BLUE_DK = (21, 101, 213, 255)
WHITE = (255, 255, 255, 255)
INK = (32, 33, 36, 255)          # 近黑（Any 的颜色）
PANEL = (255, 255, 255, 255)     # 白色底板
PANEL_EDGE = (232, 234, 237, 255)  # 底板极淡描边（浅灰，任务栏上留个边界）

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# ---- 1) 白色圆角方块底 ----
radius = int(S * 0.22)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=radius, fill=PANEL,
                    outline=PANEL_EDGE, width=max(2, int(S * 0.006)))

# ---- 2) 蓝色定位大头针（pin）在上半部居中 ----
import math
cx = S // 2
pin_top = int(S * 0.15)
pin_r = int(S * 0.185)          # 头部圆半径
pin_cy = pin_top + pin_r         # 头部圆心 y
tip_y = int(S * 0.64)            # 针尖 y

# 竖直渐变的蓝（上亮下深），做个渐变蓝 pin
def blue_grad_shape(draw_fn):
    """在一个临时图上用蓝渐变填充由 draw_fn 描出的形状，返回该图。"""
    shape_mask = Image.new("L", (S, S), 0)
    draw_fn(ImageDraw.Draw(shape_mask))
    grad = Image.new("RGBA", (S, S), BLUE)
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        r = int(BLUE[0] + (BLUE_DK[0] - BLUE[0]) * t)
        g = int(BLUE[1] + (BLUE_DK[1] - BLUE[1]) * t)
        b = int(BLUE[2] + (BLUE_DK[2] - BLUE[2]) * t)
        gd.line([(0, y), (S, y)], fill=(r, g, b, 255))
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), shape_mask)
    return out

theta = math.radians(52)
lx = cx - pin_r * math.sin(theta)
ly = pin_cy + pin_r * math.cos(theta)
rx = cx + pin_r * math.sin(theta)
ry = pin_cy + pin_r * math.cos(theta)

def draw_pin(dr):
    dr.polygon([(lx, ly), (cx, tip_y), (rx, ry)], fill=255)
    dr.ellipse([cx - pin_r, pin_cy - pin_r, cx + pin_r, pin_cy + pin_r], fill=255)

pin_img = blue_grad_shape(draw_pin)
img.alpha_composite(pin_img)

d = ImageDraw.Draw(img)
# 中间白色圆孔（露出白底）
hole_r = int(pin_r * 0.42)
d.ellipse([cx - hole_r, pin_cy - hole_r, cx + hole_r, pin_cy + hole_r], fill=WHITE)

# ---- 3) 底部 "AnyLoc" 文字（Any 深黑 / Loc 蓝）----
def load_font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "seguisb.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()

fsize = int(S * 0.145)
font = load_font(fsize)
txt_any, txt_loc = "Any", "Loc"
def tw(s, f):
    b = d.textbbox((0, 0), s, font=f); return b[2] - b[0]
w_any, w_loc = tw(txt_any, font), tw(txt_loc, font)
total = w_any + w_loc
ty = int(S * 0.70)
sx = cx - total // 2
d.text((sx, ty), txt_any, font=font, fill=INK)
d.text((sx + w_any, ty), txt_loc, font=font, fill=BLUE)

# ---- 导出 ----
preview = img.resize((256, 256), Image.LANCZOS)
preview_path = os.path.join(OUT_DIR, "icon-256.png")
preview.save(preview_path)

ico_path = os.path.join(OUT_DIR, "icon.ico")
img.save(ico_path, format="ICO",
         sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

print("生成：")
print("  预览 PNG:", preview_path)
print("  图标 ICO:", ico_path)
