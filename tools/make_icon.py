# -*- coding: utf-8 -*-
"""
应用图标生成脚本（一次性工具，产物已提交入库，常规打包无需重新运行）。

用法：
    pip install -r requirements-build.txt   # 提供 Pillow
    python tools/make_icon.py

产出（确定性绘制，可重复生成）：
    assets/icon.png  — 256x256 PNG
    assets/icon.ico  — 多尺寸 ICO（16~256，含 256 PNG 压缩条目）

设计：蓝色渐变圆角底 + 白色清单板 + 三行条目 + 绿色对勾。
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT, "assets")

SIZE = 512  # 超采样绘制，缩小后更平滑


def _interp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 蓝色渐变圆角底（#3B82F6 -> #1D4ED8）
    top, bottom = (59, 130, 246), (29, 78, 216)
    for y in range(SIZE):
        color = _interp(top, bottom, y / (SIZE - 1)) + (255,)
        draw.line([(0, y), (SIZE, y)], fill=color)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE, SIZE), radius=110, fill=255)
    img.putalpha(mask)

    draw = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    row_color = (220, 227, 245, 255)

    # 清单板夹扣
    draw.rounded_rectangle((200, 64, 312, 116), radius=16, fill=white)
    # 清单板主体
    draw.rounded_rectangle((140, 88, 372, 424), radius=32, fill=white)
    # 三行条目（长短交错）
    draw.rounded_rectangle((172, 168, 300, 188), radius=10, fill=row_color)
    draw.rounded_rectangle((172, 232, 262, 252), radius=10, fill=row_color)
    draw.rounded_rectangle((172, 296, 322, 316), radius=10, fill=row_color)
    # 绿色对勾
    draw.line([(300, 330), (250, 380), (180, 310)], width=36, joint="curve", fill=(34, 197, 94, 255))
    return img


def main():
    os.makedirs(ASSETS_DIR, exist_ok=True)
    img = draw_icon()

    png_path = os.path.join(ASSETS_DIR, "icon.png")
    img.resize((256, 256), Image.LANCZOS).save(png_path)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    ico_path = os.path.join(ASSETS_DIR, "icon.ico")
    # Pillow 的 ICO 保存：sizes 指定多尺寸，由库内部按各尺寸缩放写入
    img.save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"生成完成: {png_path}")
    print(f"生成完成: {ico_path}")


if __name__ == "__main__":
    main()
