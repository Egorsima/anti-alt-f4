# -*- coding: utf-8 -*-
"""
Готовит кадры скримера из assets/screamer_source.png.

Идея: «лицо висит в темноте» — двигается, дёргается, дышит, иногда наезжает.
Кадры заранее считаем и пакуем в один файл assets/frames.bin, чтобы:
  * в exe не тянуть numpy/scipy;
  * во время скримера не тратить CPU на обработку (просто достаём кадр и показываем).

Формат frames.bin:
  b"AAF4" | uint16 count | uint16 w | uint16 h | count * (uint32 len + jpeg bytes)
"""

import io
import json
import os
import struct

from PIL import Image, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "assets", "screamer_source.png")
OUT_BIN = os.path.join(ROOT, "assets", "frames.bin")
OUT_META = os.path.join(ROOT, "assets", "frames_meta.json")

W, H = 1600, 900          # базовое полотно (16:9); при показе растягиваем по экрану
JPEG_Q = 82


def vignette(w, h, inner=0.34, power=1.5):
    """Быстрая виньетка: считаем на 1/8 размера и растягиваем."""
    sw, sh = max(32, w // 8), max(32, h // 8)
    small = Image.new("L", (sw, sh), 0)
    px = small.load()
    cx, cy = (sw - 1) / 2.0, (sh - 1) / 2.0
    maxd = (cx * cx + cy * cy) ** 0.5
    for y in range(sh):
        for x in range(sw):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / maxd
            v = 0.0 if d < inner else min(1.0, ((d - inner) / (1.0 - inner)) ** power)
            px[x, y] = int(v * 255)
    return small.resize((w, h), Image.BILINEAR)


def build_frame(base, w, h, zoom=1.0, dx=0, dy=0, rot=0.0, bright=1.0,
                blur=0.0, jitter=0.0, seed=0):
    """Один кадр: зум/сдвиг/поворот лица + виньетка + зернистость."""
    # лицо чуть крупнее кадра: есть запас, чтобы двигать/дёргать без пустых краёв
    fw, fh = int(w * 1.10), int(h * 1.10)
    face = base.resize((fw, fh), Image.LANCZOS)
    if rot:
        face = face.rotate(rot, resample=Image.BICUBIC, expand=False)
    if blur:
        face = face.filter(ImageFilter.GaussianBlur(radius=blur))

    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    # «дыхание»: масштаб вокруг центра
    zw, zh = int(fw * zoom), int(fh * zoom)
    if (zw, zh) != (fw, fh):
        face = face.resize((zw, zh), Image.LANCZOS)
    ox = (w - face.width) // 2 + int(dx)
    oy = (h - face.height) // 2 + int(dy)
    canvas.paste(face, (ox, oy))

    # виньетка
    canvas = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), canvas,
                             vignette(w, h))
    if bright != 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(bright)

    # зерно «старой плёнки»
    if jitter:
        import random
        rnd = random.Random(seed)
        noise = Image.new("L", (w // 3, h // 3))
        np_data = bytes(rnd.getrandbits(8) for _ in range(w // 3 * h // 3))
        noise.frombytes(np_data)
        noise = noise.resize((w, h), Image.BILINEAR)
        grain = Image.merge("RGB", (noise, noise, noise))
        canvas = Image.blend(canvas, Image.blend(canvas, grain, 0.35), jitter)
    return canvas


def main():
    if not os.path.exists(SRC):
        print(f"нет исходника: {SRC}")
        return 1
    base = Image.open(SRC).convert("RGB")
    # центрируем и слегка «вытягиваем» контраст
    base = ImageEnhance.Contrast(base).enhance(1.06)

    w, h = W, H
    frames = []

    def add(**kw):
        frames.append(build_frame(base, w, h, **kw))

    # 1) базовый кадр — «он просто висит перед тобой»
    add(zoom=1.0, bright=1.0, jitter=0.35, seed=1)

    # 2) дыхание: 4 кадра туда-сюда
    add(zoom=1.045, bright=1.06, jitter=0.35, seed=2)
    add(zoom=1.075, dy=-6, bright=1.10, jitter=0.4, seed=3)
    add(zoom=1.03, dy=4, bright=1.02, jitter=0.35, seed=4)

    # 3) рывки: резкий наезд + смаз
    add(zoom=1.22, dy=-14, blur=2.0, bright=1.22, jitter=0.55, seed=5)
    add(zoom=1.10, dx=-16, rot=-0.8, bright=1.12, jitter=0.5, seed=6)
    add(zoom=1.10, dx=16, rot=0.8, bright=1.12, jitter=0.5, seed=7)

    # 4) «глитч»: тёмный кадр и пересвет
    add(zoom=1.02, bright=0.45, jitter=0.7, seed=8)
    add(zoom=1.13, bright=1.45, blur=1.0, jitter=0.65, seed=9)

    # 5) медленный отъезд в темноту (перед вспышкой)
    add(zoom=1.02, bright=0.7, jitter=0.3, seed=10)

    # пакуем
    buf = io.BytesIO()
    buf.write(b"AAF4")
    buf.write(struct.pack("<HHH", len(frames), w, h))
    for fr in frames:
        jb = io.BytesIO()
        fr.save(jb, format="JPEG", quality=JPEG_Q, optimize=True, progressive=True)
        data = jb.getvalue()
        buf.write(struct.pack("<I", len(data)))
        buf.write(data)
    blob = buf.getvalue()
    with open(OUT_BIN, "wb") as f:
        f.write(blob)

    meta = {
        "frames": len(frames),
        "w": w,
        "h": h,
        "index": {
            "base": 0,
            "breath": [1, 2, 3],
            "jerk": [4, 5, 6],
            "glitch": [7, 8],
            "fade": 9,
        },
        "timeline": [
            # (ms от старта, индекс кадра)
            [0, 0], [900, 1], [1700, 2], [2500, 3], [3300, 4], [3800, 0],
            [5200, 5], [5600, 6], [6000, 0], [7400, 7], [7600, 8], [7900, 0],
            [9500, 1], [10400, 2], [11400, 3], [12400, 4], [12900, 0],
            [14200, 5], [14800, 6], [15400, 0], [16800, 8], [17100, 0],
            [18500, 9],
        ],
    }
    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"{OUT_BIN}: {len(blob) // 1024} KB, кадров {len(frames)}, {w}x{h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
