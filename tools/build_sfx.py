# -*- coding: utf-8 -*-
"""
Сборка звуковых ассетов для приложения из открытой библиотеки сэмплов uisfx.

Источник сэмплов: npm-пакет `uisfx` v0.4.0 (MIT, https://github.com/romainsimon/uisfx),
он лежит рядом с этим скриптом (по умолчанию /tmp/package/sounds).

Скрипт делает всю «тяжёлую» обработку заранее, чтобы в exe не тянуть numpy:
  * ресемпл в 22050 Гц / моно / 16 бит;
  * нормализация, обрезка тишины, микрофейды;
  * «глубокий» фон (замедленный техно-гул, бесшовная петля 3 с);
  * «ангел» (слои колокольного аккорда + хвост-реверб) ~6 с;
  * уведомления для реплик 1–9 (по нарастающей).

Запуск:  python3 tools/build_sfx.py [путь_к_распакованному_uisfx]
"""

import os
import sys
import wave

import numpy as np
import soundfile as sf

RATE = 22050
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "assets", "sfx")
SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/package/sounds"


# ---------------------------------------------------------------------------
def load(rel):
    path = os.path.join(SRC, rel)
    d, sr = sf.read(path, always_2d=True)
    d = d.mean(axis=1).astype(np.float64)
    if sr != RATE:
        n = int(round(len(d) * RATE / sr))
        d = np.interp(np.linspace(0, len(d) - 1, n), np.arange(len(d)), d)
    return d


def trim(d, thresh=0.004, pad=0.004):
    idx = np.where(np.abs(d) > thresh)[0]
    if len(idx) == 0:
        return d
    a = max(0, idx[0] - int(pad * RATE))
    b = min(len(d), idx[-1] + int(pad * RATE))
    return d[a:b]


def norm(d, peak=0.7):
    m = float(np.abs(d).max()) or 1.0
    return d / m * peak


def fade(d, ms_in=4, ms_out=8):
    d = d.copy()
    a = max(1, int(ms_in * RATE / 1000))
    b = max(1, int(ms_out * RATE / 1000))
    d[:a] *= np.linspace(0, 1, a)
    d[-b:] *= np.linspace(1, 0, b)
    return d


def write(name, d, peak=0.9):
    os.makedirs(OUT, exist_ok=True)
    d = np.clip(d, -1.0, 1.0)
    pcm = (d * peak * 32767).astype(np.int16)
    path = os.path.join(OUT, name)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    print(f"{name:22s} {len(d) / RATE:5.2f}s {os.path.getsize(path) // 1024:4d} KB")


# ---------------------------------------------------------------------------
def build_keys():
    """Удары «по клавишам» — настоящие механические клики."""
    for name, rel, peak in [
        ("key1.wav", "mechanical/press.ogg", 0.85),
        ("key2.wav", "mechanical/typing.ogg", 0.95),
        ("key3.wav", "mechanical/double-click.ogg", 0.8),
        ("key4.wav", "mechanical/lock.ogg", 0.75),
    ]:
        d = norm(trim(load(rel)), peak)
        write(name, fade(d, 1, 6), peak=1.0)


def build_accent():
    """Акцент на точках/концах строк — стеклянный «дзинь» с металлом."""
    d = load("glass/blocked.ogg")
    d = trim(d, thresh=0.006)
    # чуть замедлить — тяжелее и «страшнее»
    n = int(len(d) * 1.25)
    d = np.interp(np.linspace(0, len(d) - 1, n), np.arange(len(d)), d)
    d = norm(d, 0.72)
    write("accent.wav", fade(d, 2, 40), peak=1.0)


def build_bed():
    """Фон скримера: техно-гул, замедленный и растянутый в бесшовную петлю."""
    d = load("mechanical/processing.ogg")
    d = trim(d, thresh=0.002, pad=0)
    # замедление в 2.2 раза -> глубже и тревожнее
    n = int(len(d) * 2.2)
    d = np.interp(np.linspace(0, len(d) - 1, n), np.arange(len(d)), d)
    d = norm(d, 0.5)
    # соберём 3.5-секундную петлю с кроссфейдом
    target = int(3.5 * RATE)
    reps = int(np.ceil(target / len(d))) + 1
    loop = np.tile(d, reps)[: target + int(0.35 * RATE)]
    xfade = int(0.35 * RATE)
    tail = loop[target: target + xfade]
    out = loop[:target].copy()
    out[:xfade] = out[:xfade] * np.linspace(0, 1, xfade) + tail * np.linspace(1, 0, xfade)
    # низкая синусоида-саб для «мяса»
    t = np.arange(len(out)) / RATE
    sub = np.sin(2 * np.pi * 46.0 * t) * 0.10 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.12 * t))
    out = norm(out * 0.85 + sub, 0.62)
    write("bed.wav", fade(out, 60, 60), peak=1.0)


def build_angel():
    """«Возрождение»: колокольный аккорд в трёх слоях + длинный хвост."""
    src = trim(load("cinematic/level-up.ogg"), thresh=0.003)
    src = norm(src, 0.7)
    total = int(6.0 * RATE)
    out = np.zeros(total)

    def place(sig, at, gain, stretch=1.0):
        d = sig
        if stretch != 1.0:
            n = int(len(d) * stretch)
            d = np.interp(np.linspace(0, len(d) - 1, n), np.arange(len(d)), d)
        start = int(at * RATE)
        end = min(total, start + len(d))
        if end <= start:
            return
        out[start:end] += d[: end - start] * gain

    # основной аккорд, потом квинта сверху и октава — «хор»
    place(src, 0.0, 1.0, 1.35)
    place(src[::1], 0.55, 0.55, 1.0)
    place(src, 1.15, 0.42, 0.72)   # выше и короче (октава)
    place(src, 2.40, 0.30, 0.58)

    # простой реверб-хвост (несколько затухающих задержек)
    for delay_ms, gain in ((110, 0.35), (230, 0.22), (390, 0.14), (620, 0.08)):
        d = int(delay_ms * RATE / 1000)
        out[d:] += out[:-d] * gain

    # мягкое затухание в конце
    tail = int(1.8 * RATE)
    out[-tail:] *= np.linspace(1, 0, tail) ** 1.6
    out = norm(fade(out, 5, 300), 0.82)
    write("angel.wav", out, peak=1.0)


def build_notifications():
    """Звук появления окна с репликой: 1–9, по нарастающей злости."""
    plan = [
        ("notify1.wav", "mechanical/notification.ogg", 0.55),
        ("notify2.wav", "mechanical/mention.ogg", 0.60),
        ("notify3.wav", "mechanical/queued.ogg", 0.62),
        ("notify4.wav", "mechanical/warning.ogg", 0.66),
        ("notify5.wav", "mechanical/blocked.ogg", 0.68),
        ("notify6.wav", "mechanical/lock.ogg", 0.70),
        ("notify7.wav", "mechanical/error.ogg", 0.72),
        ("notify8.wav", "glass/blocked.ogg", 0.75),
        ("notify9.wav", "cinematic/notification.ogg", 0.80),
    ]
    for name, rel, peak in plan:
        d = norm(trim(load(rel), thresh=0.004), peak)
        write(name, fade(d, 3, 25), peak=1.0)


def main():
    if not os.path.isdir(SRC):
        print(f"нет папки с сэмплами: {SRC}")
        return 1
    print(f"источник: {SRC}\nвыход: {OUT}\n")
    build_keys()
    build_accent()
    build_bed()
    build_angel()
    build_notifications()
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print(f"\nвсего: {total // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
