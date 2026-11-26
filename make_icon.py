# -*- coding: utf-8 -*-
"""Генерирует icon.ico для exe (запускается в GitHub Actions на Windows)."""
import traceback

from PIL import Image, ImageDraw, ImageFont

SIZES = [256, 128, 64, 48, 32, 16]


def get_font(fs):
    try:
        return ImageFont.truetype("arial.ttf", fs)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=fs)
    except Exception:
        pass
    return ImageFont.load_default()


def make(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.18)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=r, fill=(26, 26, 26, 255))
    d.rounded_rectangle(
        [2, 2, size - 2, size - 2],
        radius=r,
        outline=(220, 38, 38, 255),
        width=max(2, size // 16),
    )
    # текст F4 (не критично, если шрифт не встал)
    try:
        font = get_font(int(size * 0.42))
        text = "F4"
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(
            ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
            text,
            fill=(255, 255, 255, 255),
            font=font,
        )
    except Exception:
        traceback.print_exc()
    # слэш
    m = int(size * 0.15)
    d.line([(m, size - m), (size - m, m)], fill=(220, 38, 38, 255), width=max(2, size // 12))
    return img


def main():
    imgs = [make(s) for s in SIZES]
    imgs[0].save("icon.ico", sizes=[(s, s) for s in SIZES], append_images=imgs[1:])
    print("icon.ico создан")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
