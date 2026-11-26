# -*- coding: utf-8 -*-
"""
Скример (10-я «реплика») + «возрождение» после него.

Скример:
  * на весь экран (все мониторы) картинка без рамок и кнопок, поверх всего;
  * виньетка + эффект объёма (лицо «выпирает» из темноты, экран медленно дышит);
  * по центру по одной букве печатается текст — со злым звуком и низким гулом;
  * глушит звук всех остальных приложений (Discord в том числе), кроме своего;
  * блокирует клавиатуру, мышь, переключение окон; курсор заморожен и не виден;
  * после последней строки — белая вспышка и «ангельский» звук: возрождение.

Ограничение: Ctrl+Alt+Del перехватить невозможно (это защита самой Windows).
"""

import ctypes
import math
import os
import random
import sys
import tempfile
import threading
import time
import wave
from array import array

try:
    import tkinter as tk
except Exception:  # без tkinter модуль всё равно импортируется (тесты/логи)
    tk = None

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except Exception:  # pragma: no cover
    Image = None
try:
    from PIL import ImageTk
except Exception:  # ImageTk требует tkinter; без него просто не покажем кадры
    ImageTk = None

# ---------------------------------------------------------------------------
# Текст скримера
# ---------------------------------------------------------------------------
SCREAMER_LINES = [
    "тебе говорили...",
    "тебя предупреждали...",
    "но ты не слушался и всеравно тыкал ALT+F4",
    "поверь.. твоим тиммейтам это точно не нравится...",
]

SCREAMER_IMAGE = "assets/screamer.jpg"

RATE = 22050
CHAR_DELAY_MIN = 0.055
CHAR_DELAY_MAX = 0.115
LINE_PAUSE = 0.95
HOLD_AFTER_LAST = 1.1
ANGEL_DUR = 5.2
MAX_DURATION_MS = 120_000  # предохранитель: дольше скример не живёт


# ---------------------------------------------------------------------------
# утилиты
# ---------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def _user32():
    return ctypes.windll.user32 if os.name == "nt" else None


def virtual_screen():
    """Прямоугольник всех мониторов: (x, y, w, h)."""
    u = _user32()
    if u is None:
        return 0, 0, 1280, 720
    try:
        x = u.GetSystemMetrics(76)
        y = u.GetSystemMetrics(77)
        w = u.GetSystemMetrics(78)
        h = u.GetSystemMetrics(79)
        if w <= 0 or h <= 0:
            raise ValueError("no virtual screen")
        return x, y, w, h
    except Exception:
        return 0, 0, u.GetSystemMetrics(0), u.GetSystemMetrics(1)


# ---------------------------------------------------------------------------
# Синтез звука
# ---------------------------------------------------------------------------
def _clip(v, peak=0.95):
    if v > 1.0:
        v = 1.0
    elif v < -1.0:
        v = -1.0
    return int(v * 32767 * peak)


def _shatter():
    """Злой «удар-щелчок»: низкий толчок + треск + металлический скрежет."""
    rnd = random.Random(1337)
    dur = 0.19
    n = int(RATE * dur)
    out = []
    prev = 0.0
    for i in range(n):
        t = i / RATE
        hit = math.exp(-t * 26.0)                     # общая атака
        thump = math.sin(2 * math.pi * (52 - 24 * min(t / dur, 1)) * t) * math.exp(-t * 11.0)
        metal = (
            math.sin(2 * math.pi * 2270 * t + 1.7 * math.sin(2 * math.pi * 13 * t))
            + 0.65 * math.sin(2 * math.pi * 3310 * t)
            + 0.4 * math.sin(2 * math.pi * 4210 * t)
        ) * math.exp(-t * 38.0)
        noise = rnd.uniform(-1.0, 1.0)
        hp = noise - prev                             # хайпас: сухое шипение
        prev = noise
        v = thump * 0.95 + hp * 0.85 * hit + metal * 0.42 + noise * 0.10 * hit
        out.append(_clip(v, 0.92))
    return out


def _drone():
    """Низкий гул (3 с, бесшовный цикл) — «что-то не так»."""
    dur = 3.0
    n = int(RATE * dur)
    rnd = random.Random(99)
    out = []
    for i in range(n):
        t = i / RATE
        a = math.sin(2 * math.pi * 38.5 * t)
        b = math.sin(2 * math.pi * 57.7 * t + 1.2 * math.sin(2 * math.pi * 0.19 * t))
        c = math.sin(2 * math.pi * 92.0 * t) * 0.35
        breath = 0.5 + 0.5 * math.sin(2 * math.pi * 0.14 * t)
        air = rnd.uniform(-1.0, 1.0) * 0.03 * breath
        v = (a * 0.55 + b * 0.35 + c) * 0.45 * breath + air
        fade = min(1.0, i / (0.06 * RATE), (n - i) / (0.06 * RATE))
        out.append(v * fade)
    return out


def _angel():
    """Возрождение: хор чистых квинт/октав + колокольчики + подъём."""
    n = int(RATE * ANGEL_DUR)
    notes = [523.25, 659.25, 783.99, 1046.5, 1318.5]
    bells = (0.05, 0.7, 1.5, 2.4, 3.4, 4.3)
    out = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, t / 0.45)
        rel = max(0.0, t - (ANGEL_DUR - 1.6))
        env *= math.exp(-rel * 2.0)
        v = 0.0
        for k, f in enumerate(notes):
            vib = 1.0 + 0.0045 * math.sin(2 * math.pi * (4.2 + k * 0.5) * t)
            v += math.sin(2 * math.pi * f * vib * t) * (0.5 ** k)
        bell = 0.0
        for bt in bells:
            dt = t - bt
            if dt > 0:
                bell += math.sin(2 * math.pi * 2093 * dt) * math.exp(-dt * 2.6) * 0.16
                bell += math.sin(2 * math.pi * 3136 * dt) * math.exp(-dt * 4.4) * 0.07
        # «свет»: лёгкий восходящий шум-воздух
        air = math.sin(2 * math.pi * 523.25 * t * 0.5) * 0.05 * min(1.0, t / 1.5)
        v = (v * 0.42 + bell + air) * env
        out.append(v)
    return out


# --- таймлайн печати и общий трек ------------------------------------------
_track = None
_track_lock = threading.Lock()
_track_path = None


def build_timeline(seed=424242):
    """Один раз считаем, когда какая буква печатается — звук и окно идут синхронно."""
    rnd = random.Random(seed)
    t = 1.0  # даём окну появиться
    events = []
    lines = []
    for li, line in enumerate(SCREAMER_LINES):
        start = t
        for ch in line:
            events.append((t, ch))
            d = rnd.uniform(CHAR_DELAY_MIN, CHAR_DELAY_MAX)
            if ch in ".…":
                d += 0.4
            elif ch == ",":
                d += 0.2
            t += d
        lines.append((start, t, line))
        if li < len(SCREAMER_LINES) - 1:
            t += LINE_PAUSE
    return events, lines, t


def _build_track():
    """Склеиваем ОДИН wav: гул + удары по буквам + ангел в конце.

    winsound умеет играть только один звук за раз, поэтому всё микшируем заранее.
    """
    events, lines, t_end = build_timeline()
    t_angel = t_end + HOLD_AFTER_LAST
    total = t_angel + ANGEL_DUR + 0.4
    n = int(total * RATE)
    buf = [0.0] * n

    drone = _drone()
    dn = len(drone)
    # гул на всю длину, с затуханием к моменту ангела
    for i in range(min(n, int(t_angel * RATE))):
        buf[i] = drone[i % dn] * 0.9
    # плавно убираем гул на последних 0.6 с перед ангелом
    fade_from = max(0, int(t_angel * RATE) - int(0.6 * RATE))
    for i in range(fade_from, min(n, int(t_angel * RATE))):
        k = (int(t_angel * RATE) - i) / (0.6 * RATE)
        buf[i] *= k

    # удары по буквам
    click = _shatter()
    cn = len(click)
    for (t, ch) in events:
        if ch.isspace():
            continue
        off = int(t * RATE)
        for j in range(cn):
            k = off + j
            if k >= n:
                break
            buf[k] += click[j] / 32767.0 * 1.6

    # ангел
    angel = _angel()
    off = int(t_angel * RATE)
    for j in range(min(len(angel), n - off)):
        buf[off + j] += angel[j] * 1.25

    # мягкое ограничение, чтобы не хрипело
    frames = array("h")
    for v in buf:
        if v > 0.92:
            v = 0.92 - (v - 0.92) * 0.5
        elif v < -0.92:
            v = -0.92 - (v + 0.92) * 0.5
        frames.append(_clip(v, 1.0))

    path = os.path.join(tempfile.gettempdir(), "anti_alt_f4_screamer.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames.tobytes())
    return path, events, lines, t_end, t_angel


def get_track(log=print):
    """Готовый трек (при первом вызове синтезируется)."""
    global _track, _track_path
    with _track_lock:
        if _track is None:
            started = time.time()
            _track_path, events, lines, t_end, t_angel = _build_track()
            _track = (events, lines, t_end, t_angel)
            log(f"трек скримера готов за {time.time() - started:.1f}s: {_track_path}")
        return _track_path, _track


def warmup(log=print):
    """Заранее (в фоне) синтезируем звук, чтобы скример стартовал мгновенно."""
    def _worker():
        try:
            get_track(log)
        except Exception as e:
            log(f"warmup failed: {e}")

    t = threading.Thread(target=_worker, daemon=True, name="screamer-warmup")
    t.start()
    return t


def play_track(path=None, log=print):
    if os.name != "nt":
        return
    try:
        import winsound

        if path is None:
            path = get_track(log)[0]
        winsound.PlaySound(
            path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )
    except Exception as e:
        log(f"play track error: {e}")


def stop_sound(log=print):
    if os.name != "nt":
        return
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception as e:
        log(f"stop sound error: {e}")


# ---------------------------------------------------------------------------
# Глушилка чужих звуков (Discord и всё остальное)
# ---------------------------------------------------------------------------
class SessionMuter:
    def __init__(self, log=print):
        self.log = log
        self._saved = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.available = False
        self._utils = None
        self._coinit_done = False
        try:
            from pycaw.pycaw import AudioUtilities

            self._utils = AudioUtilities
            self.available = True
        except Exception as e:
            self.log(f"pycaw недоступен, чужой звук глушить не смогу: {e}")

    def _coinit(self):
        """В рабочем потоке COM нужно поднять заново, иначе вызовы молча падают."""
        if self._coinit_done or os.name != "nt":
            return
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED
        except Exception:
            pass
        self._coinit_done = True

    @staticmethod
    def _key(session):
        for attr in ("Identifier", "InstanceIdentifier"):
            try:
                v = getattr(session, attr)
            except Exception:
                v = None
            if v:
                return str(v)
        return None

    @staticmethod
    def _pid(session):
        try:
            p = session.Process
            return p.pid if p is not None else None
        except Exception:
            return None

    def _sessions(self):
        if not self._utils:
            return []
        try:
            return self._utils.GetAllSessions()
        except Exception as e:
            self.log(f"GetAllSessions failed: {e}")
            return []

    def mute_others(self):
        if not self.available:
            return 0
        self._coinit()
        mypid = os.getpid()
        muted = 0
        for s in self._sessions():
            vol = getattr(s, "SimpleAudioVolume", None)
            if vol is None:
                continue
            pid = self._pid(s)
            try:
                if pid == mypid:
                    vol.SetMute(False, None)  # свои звуки должны быть слышны
                    continue
                key = self._key(s) or f"pid:{pid}"
                with self._lock:
                    prev = self._saved.get(key)
                    if prev is None:
                        prev = bool(vol.GetMute())
                        self._saved[key] = prev
                vol.SetMute(True, None)
                muted += 1
            except Exception:
                continue
        return muted

    def _watch(self):
        while not self._stop.wait(0.2):
            try:
                self.mute_others()
            except Exception as e:
                self.log(f"mute watch error: {e}")

    def start(self):
        if not self.available:
            return False
        try:
            n = self.mute_others()
        except Exception as e:
            self.log(f"mute failed: {e}")
            return False
        self._thread = threading.Thread(target=self._watch, daemon=True, name="mute-watch")
        self._thread.start()
        self.log(f"звук чужих приложений заглушен (сессий: {n})")
        return True

    def restore(self):
        self._stop.set()
        if not self.available:
            return
        self._coinit()
        mypid = os.getpid()
        restored = 0
        for s in self._sessions():
            vol = getattr(s, "SimpleAudioVolume", None)
            if vol is None or self._pid(s) == mypid:
                continue
            key = self._key(s) or f"pid:{self._pid(s)}"
            with self._lock:
                prev = self._saved.get(key)
            if prev is None:
                continue
            try:
                vol.SetMute(prev, None)
                restored += 1
            except Exception:
                continue
        with self._lock:
            self._saved.clear()
        self.log(f"звук возвращён (сессий: {restored})")

    def panic_unmute(self):
        if not self.available:
            return
        self._coinit()
        mypid = os.getpid()
        for s in self._sessions():
            vol = getattr(s, "SimpleAudioVolume", None)
            if vol is None or self._pid(s) == mypid:
                continue
            try:
                vol.SetMute(False, None)
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Курсор
# ---------------------------------------------------------------------------
class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CursorLock:
    def __init__(self, log=print):
        self.log = log
        self.locked = False

    def lock(self):
        u = _user32()
        if u is None:
            return
        try:
            pt = _POINT()
            u.GetCursorPos(ctypes.byref(pt))
            r = _RECT(pt.x - 1, pt.y - 1, pt.x + 1, pt.y + 1)
            u.ClipCursor(ctypes.byref(r))
            self.locked = True
            self.log("курсор заблокирован")
        except Exception as e:
            self.log(f"cursor lock failed: {e}")

    def unlock(self):
        u = _user32()
        if u is None:
            return
        try:
            if self.locked:
                u.ClipCursor(None)
                self.locked = False
            self.log("курсор освобождён")
        except Exception as e:
            self.log(f"cursor unlock failed: {e}")


# ---------------------------------------------------------------------------
# Оконные хелперы
# ---------------------------------------------------------------------------
def _hwnd(win):
    u = _user32()
    if u is None:
        return None
    try:
        return u.GetParent(win.winfo_id()) or win.winfo_id()
    except Exception:
        return None


def _topmost_fullscreen(win, x, y, w, h, log=print):
    if os.name != "nt":
        return
    try:
        u = ctypes.windll.user32
        u.SetWindowPos.restype = ctypes.c_bool
        hwnd = _hwnd(win)
        if not hwnd:
            return
        u.SetWindowPos(hwnd, -1, int(x), int(y), int(w), int(h), 0x0040)
    except Exception as e:
        log(f"SetWindowPos failed: {e}")


def _become_foreground(win, log=print):
    if os.name != "nt":
        return
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        hwnd = _hwnd(win)
        if not hwnd:
            return
        fg = u.GetForegroundWindow()
        tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_my = k.GetCurrentThreadId()
        attached = False
        if tid_fg and tid_fg != tid_my:
            attached = bool(u.AttachThreadInput(tid_fg, tid_my, True))
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetFocus(hwnd)
        if attached:
            u.AttachThreadInput(tid_fg, tid_my, False)
    except Exception as e:
        log(f"foreground failed: {e}")


# ---------------------------------------------------------------------------
# Картинка: виньетка + объём
# ---------------------------------------------------------------------------
def _vignette_mask(w, h, inner=0.40, power=1.35):
    """Маска виньетки: 0 в центре, 255 по краям (считаем на малом размере)."""
    sw = 160
    sh = max(48, int(sw * h / max(1, w)))
    small = Image.new("L", (sw, sh), 0)
    px = small.load()
    cx, cy = (sw - 1) / 2.0, (sh - 1) / 2.0
    maxd = math.hypot(cx, cy)
    for yy in range(sh):
        for xx in range(sw):
            d = math.hypot(xx - cx, yy - cy) / maxd
            v = 0.0 if d < inner else min(1.0, ((d - inner) / (1.0 - inner)) ** power)
            px[xx, yy] = int(v * 255)
    return small.resize((w, h), Image.BILINEAR)


def make_stage(w, h, path=None, log=print):
    """Кадр скримера: крупное лицо + тёмные края + подсветка центра (объём)."""
    path = path or resource_path(SCREAMER_IMAGE)
    if Image is None or not os.path.exists(path):
        log(f"картинка скримера не найдена: {path}")
        return None
    base = Image.open(path).convert("RGB")

    # 1) подложка: размытая копия на весь экран
    bg = ImageOps.fit(base, (w, h), method=Image.LANCZOS, centering=(0.5, 0.45))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(8, w // 70)))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)

    # 2) лицо крупнее экрана — эффект «он прямо перед тобой»
    face_h = int(h * 1.08)
    face_w = max(1, int(base.width * face_h / base.height))
    face = base.resize((face_w, face_h), Image.LANCZOS)
    canvas = bg.copy()
    canvas.paste(face, ((w - face_w) // 2, int(-h * 0.035)))

    # 3) виньетка
    mask = _vignette_mask(w, h, inner=0.40, power=1.45)
    canvas = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), canvas, mask)

    # 4) объём: центр чуть ярче и резче, края остаются в темноте
    center_mask = ImageOps.invert(mask).filter(ImageFilter.GaussianBlur(radius=w // 24))
    center_mask = center_mask.point(lambda v: int(v * 0.9))
    lit = ImageEnhance.Brightness(canvas).enhance(1.28)
    sharp = canvas.filter(ImageFilter.UnsharpMask(radius=4, percent=110, threshold=2))
    canvas = Image.composite(sharp, canvas, center_mask)
    canvas = Image.composite(lit, canvas, center_mask.point(lambda v: int(v * 0.55)))
    return canvas


def _zoom(img, w, h, k):
    """Кадр «дыхания»: лёгкий зум с центрированием."""
    nw, nh = int(w * k), int(h * k)
    im = img.resize((nw, nh), Image.BILINEAR)
    left = max(0, (nw - w) // 2)
    top = max(0, (nh - h) // 2)
    return im.crop((left, top, left + w, top + h))


# ---------------------------------------------------------------------------
# Скример
# ---------------------------------------------------------------------------
class ScreamerOverlay:
    def __init__(self, root, log=print, set_input_blocked=None, on_done=None):
        self.root = root
        self.log = log
        self.set_input_blocked = set_input_blocked or (lambda flag: None)
        self.on_done = on_done

        self.win = None
        self._stage_img = None
        self._stage_label = None
        self._text_label = None

        self._base_stage = None
        self._frames = {}
        self._frames_ready = False
        self._lines_done = []
        self._cur = ""
        self._phase_i = 0
        self._cursor_on = True
        self._size = (1280, 720)
        self._finished = False
        self._after_ids = []

        self.muter = SessionMuter(log=log)
        self.cursor = CursorLock(log=log)

    # -- утилиты -------------------------------------------------------------
    def _after(self, ms, fn):
        try:
            aid = self.root.after(max(0, int(ms)), fn)
            self._after_ids.append(aid)
            return aid
        except Exception:
            return None

    def _cancel_afters(self):
        for aid in self._after_ids:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._after_ids = []

    # -- кадры ---------------------------------------------------------------
    def _prepare_frames(self):
        """Тяжёлую графику считаем в фоне, чтобы окно не подвисало."""
        w, h = self._size
        try:
            base = make_stage(w, h, log=self.log)
            if base is None:
                return
            self._base_stage = base
            self._frames["start"] = base
            self._frames["in"] = _zoom(base, w, h, 1.035)
            self._frames["out"] = _zoom(base, w, h, 0.985)
        except Exception as e:
            self.log(f"stage build error: {e}")
            return
        self._frames_ready = True
        self.log("кадры скримера готовы")
        self._apply_frame("start")

    def _apply_frame(self, phase):
        if not self._frames_ready or self._stage_label is None:
            return
        img = self._frames.get(phase) or self._base_stage
        if img is None or ImageTk is None:
            return
        try:
            self._stage_img = ImageTk.PhotoImage(img)
            self._stage_label.config(image=self._stage_img)
        except Exception as e:
            self.log(f"frame apply error: {e}")

    def _breathe(self):
        if self._finished:
            return
        if self._frames_ready:
            self._phase_i += 1
            self._apply_frame("in" if self._phase_i % 2 else "out")
        self._after(1600, self._breathe)

    # -- окно ---------------------------------------------------------------
    def _build_window(self):
        x, y, w, h = virtual_screen()
        self._size = (w, h)
        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.configure(bg="black", cursor="none")
        win.attributes("-topmost", True)

        self._stage_label = tk.Label(win, bd=0, bg="black")
        self._stage_label.place(x=0, y=0, width=w, height=h)

        self._text_label = tk.Label(
            win,
            text="",
            bg="black",
            fg="#ececec",
            font=("Consolas", 26, "bold"),
            justify="center",
            wraplength=int(w * 0.70),
        )
        self._text_label.place(relx=0.5, rely=0.52, anchor="center")

        win.bind("<Alt-F4>", lambda e: "break")
        win.bind("<Escape>", lambda e: "break")
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        try:
            win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
            win.update_idletasks()
        except Exception:
            pass
        _topmost_fullscreen(win, x, y, w, h, log=self.log)
        win.lift()
        try:
            win.grab_set()
        except Exception:
            pass
        _become_foreground(win, log=self.log)
        self._keep_focus()

    def _keep_focus(self):
        if self._finished:
            return
        try:
            if self.win is not None:
                self.win.attributes("-topmost", True)
                hwnd = _hwnd(self.win)
                if hwnd and os.name == "nt":
                    u = ctypes.windll.user32
                    if u.GetForegroundWindow() != hwnd:
                        u.SetForegroundWindow(hwnd)
                self.win.lift()
        except Exception:
            pass
        self._after(600, self._keep_focus)

    # -- печать --------------------------------------------------------------
    def _render(self):
        parts = list(self._lines_done)
        parts.append(self._cur + ("█" if self._cursor_on else ""))
        try:
            self._text_label.config(text="\n".join(parts))
        except Exception:
            pass

    def _blink(self):
        if self._finished:
            return
        self._cursor_on = not self._cursor_on
        shades = ["#ececec", "#c9c9c9", "#ffffff", "#a9a9a9", "#ececec", "#f7f7f7"]
        try:
            self._text_label.config(fg=shades[self._phase_i % len(shades)])
        except Exception:
            pass
        self._render()
        self._after(340, self._blink)

    def _schedule_typing(self, events, lines):
        """Печать строго по предрассчитанному таймлайну (совпадает со звуком)."""
        cur = {"i": 0, "text": ""}

        def step():
            if self._finished or cur["i"] >= len(events):
                return
            t, ch = events[cur["i"]]
            cur["i"] += 1
            cur["text"] += ch
            self._cur = cur["text"]
            self._cursor_on = True
            self._render()
            if cur["i"] < len(events):
                nxt = events[cur["i"]][0]
                self._after((nxt - t) * 1000, step)

        if events:
            self._after(events[0][0] * 1000, step)

        for (start, end, line) in lines:
            def done(line=line, end=end):
                if self._finished:
                    return
                self._lines_done.append(line)
                self._cur = ""
                self._render()
                self.log(f"строка допечатана: {line[:24]}...")

            self._after(end * 1000 + 60, done)

    # -- возрождение ---------------------------------------------------------
    def _revive(self):
        if self._finished:
            return
        self.log("скример: вспышка возрождения")
        self._flash_step(0)

    def _flash_step(self, i):
        if self._finished:
            return
        steps = [
            ("#ffffff", 80),
            ("#ffeccc", 110),
            ("#f3efff", 140),
            ("#b7aedb", 160),
            ("#3a3448", 220),
            ("#08080c", 260),
        ]
        if i >= len(steps):
            self._finish("возрождение")
            return
        color, delay = steps[i]
        try:
            self._stage_label.config(image="", bg=color)
            self._stage_img = None
            self._text_label.config(text="", bg=color)
            self.win.configure(bg=color)
        except Exception:
            pass
        self._after(delay, lambda: self._flash_step(i + 1))

    # -- старт/финиш ---------------------------------------------------------
    def show(self):
        if tk is None:
            self.log("скример: нет tkinter — показать окно невозможно")
            return
        self.log("скример: старт")
        try:
            self.set_input_blocked(True)
        except Exception as e:
            self.log(f"input block failed: {e}")
        self.cursor.lock()
        self.muter.start()

        try:
            path, (events, lines, t_end, t_angel) = get_track(self.log)
        except Exception as e:
            self.log(f"трек не собрался: {e}")
            path, events, lines, t_angel = None, [], [], 8.0

        try:
            self._build_window()
            self._render()
            self._after(400, self._blink)
            threading.Thread(target=self._prepare_frames, daemon=True, name="stage").start()
            self._after(700, self._breathe)
            # звук: гул + печать + ангел одним файлом, ровно по таймлайну
            play_track(path, log=self.log)
            self._schedule_typing(events, lines)
            self._after(t_angel * 1000, self._revive)
            self._after(MAX_DURATION_MS, lambda: self._finish("предохранитель"))
        except Exception:
            import traceback

            self.log("скример упал при показе: " + traceback.format_exc())
            self._finish("ошибка показа")

    def _finish(self, reason=""):
        if self._finished:
            return
        self._finished = True
        self.log(
            f"скример: конец{(' (' + reason + ')') if reason else ''}, возвращаю всё как было"
        )
        self._cancel_afters()
        if reason != "возрождение":
            # ангельский хвост не обрываем, а при сбое — глушим
            try:
                stop_sound(self.log)
            except Exception:
                pass
        try:
            if self.win is not None:
                self.win.grab_release()
        except Exception:
            pass
        try:
            if self.win is not None:
                self.win.destroy()
        except Exception:
            pass
        self.win = None
        self._stage_img = None
        self._frames.clear()
        for step in (self.cursor.unlock, self.muter.restore):
            try:
                step()
            except Exception as e:
                self.log(f"restore step failed: {e}")
        try:
            self.set_input_blocked(False)
        except Exception as e:
            self.log(f"input unblock failed: {e}")
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception as e:
                self.log(f"on_done error: {e}")
