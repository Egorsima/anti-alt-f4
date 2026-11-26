# -*- coding: utf-8 -*-
"""
Скример (10-я «реплика»).

Что делает:
  * на весь экран (все мониторы) показывает картинку без рамок и кнопок, поверх всего;
  * по центру «по-старинному» печатает текст по одной букве со звуком печатной машинки;
  * глушит звук всех остальных приложений, оставляя только свои звуки печати;
  * полностью блокирует клавиатуру и мышь (курсор замораживается и прячется);
  * после последней строки всё возвращает как было: звук, курсор, ввод.

Ограничение: Ctrl+Alt+Del перехватить невозможно (это защита самой Windows).
"""

import ctypes
import math
import os
import random
import sys
import tempfile
import threading
import wave
from array import array

try:
    import tkinter as tk
except Exception:  # без tkinter модуль всё равно импортируется (тесты/логи)
    tk = None

try:
    from PIL import Image, ImageOps, ImageTk
except Exception:  # pragma: no cover
    Image = None

# ---------------------------------------------------------------------------
# Текст скримера (печатается по одной строке)
# ---------------------------------------------------------------------------
SCREAMER_LINES = [
    "тебе говорили...",
    "тебя предупреждали...",
    "но ты не слушался и всеравно тыкал ALT+F4",
    "поверь.. твоим тиммейтам это точно не нравится...",
]

SCREAMER_IMAGE = "assets/screamer.jpg"

# тайминги печати
CHAR_DELAY_MIN = 0.045
CHAR_DELAY_MAX = 0.095
LINE_PAUSE = 0.95
HOLD_AFTER_LAST = 1.5


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
        # SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77,
        # SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
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
# Звук печатной машинки (генерируется один раз в wav)
# ---------------------------------------------------------------------------
_click_path = None
_click_lock = threading.Lock()


def click_sound_path():
    global _click_path
    with _click_lock:
        if _click_path and os.path.exists(_click_path):
            return _click_path
        path = os.path.join(tempfile.gettempdir(), "anti_alt_f4_type.wav")
        rate = 22050
        dur = 0.038
        n = int(rate * dur)
        rnd = random.Random(20240521)
        frames = array("h")
        for i in range(n):
            t = i / rate
            env = math.exp(-t * 95.0)
            noise = rnd.uniform(-1.0, 1.0)
            tone = math.sin(2 * math.pi * 1500 * t) * 0.45
            tone += math.sin(2 * math.pi * 2900 * t) * 0.18
            v = (noise * 0.45 + tone) * env
            v = max(-1.0, min(1.0, v))
            frames.append(int(v * 15000))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames.tobytes())
        _click_path = path
        return path


def play_click(log=print):
    if os.name != "nt":
        return
    try:
        import winsound

        winsound.PlaySound(
            click_sound_path(),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception as e:
        log(f"click sound error: {e}")


# ---------------------------------------------------------------------------
# Глушилка чужих звуков: мьютим сессии всех приложений, кроме своей
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
        try:
            from pycaw.pycaw import AudioUtilities

            self._utils = AudioUtilities
            self.available = True
        except Exception as e:
            self.log(f"pycaw недоступен, чужой звук глушить не смогу: {e}")

    # -- служебное ----------------------------------------------------------
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

    # -- основное -----------------------------------------------------------
    def mute_others(self):
        if not self.available:
            return
        mypid = os.getpid()
        for s in self._sessions():
            vol = getattr(s, "SimpleAudioVolume", None)
            if vol is None:
                continue
            pid = self._pid(s)
            try:
                if pid == mypid:
                    # свои звуки печати должны быть слышны
                    vol.SetMute(False, None)
                    continue
                key = self._key(s) or f"pid:{pid}"
                with self._lock:
                    if key in self._saved:
                        continue
                    prev = bool(vol.GetMute())
                    self._saved[key] = prev
                if not prev:
                    vol.SetMute(True, None)
            except Exception:
                continue

    def _watch(self):
        while not self._stop.wait(0.8):
            try:
                self.mute_others()
            except Exception as e:
                self.log(f"mute watch error: {e}")

    def start(self):
        if not self.available:
            return False
        try:
            self.mute_others()
        except Exception as e:
            self.log(f"mute failed: {e}")
            return False
        self._thread = threading.Thread(target=self._watch, daemon=True, name="mute-watch")
        self._thread.start()
        self.log(f"звук чужих приложений заглушен (сессий: {len(self._saved)})")
        return True

    def restore(self):
        self._stop.set()
        if not self.available:
            return
        mypid = os.getpid()
        restored = 0
        for s in self._sessions():
            vol = getattr(s, "SimpleAudioVolume", None)
            if vol is None:
                continue
            if self._pid(s) == mypid:
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


# ---------------------------------------------------------------------------
# Блокировка курсора
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
        self._hidden = 0

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
            n = 0
            while u.ShowCursor(False) >= 0 and n < 64:
                n += 1
            self._hidden = n
            self.log(f"курсор заблокирован и спрятан (ShowCursor x{n})")
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
            n = 0
            while u.ShowCursor(True) < 0 and n < 64:
                n += 1
            self._hidden = 0
            self.log("курсор освобождён")
        except Exception as e:
            self.log(f"cursor unlock failed: {e}")


# ---------------------------------------------------------------------------
# Сам скример
# ---------------------------------------------------------------------------
class ScreamerOverlay:
    def __init__(self, root, log=print, set_input_blocked=None, on_done=None):
        self.root = root
        self.log = log
        self.set_input_blocked = set_input_blocked or (lambda flag: None)
        self.on_done = on_done

        self.win = None
        self._img_ref = None
        self._text_label = None
        self._image_label = None

        self._lines_done = []
        self._cur = ""
        self._line_i = 0
        self._char_i = 0
        self._cursor_on = True
        self._flicker_i = 0
        self._finished = False
        self._after_ids = []

        self.muter = SessionMuter(log=log)
        self.cursor = CursorLock(log=log)

    # -- окно ---------------------------------------------------------------
    def _after(self, ms, fn):
        try:
            aid = self.root.after(ms, fn)
            self._after_ids.append(aid)
            return aid
        except Exception:
            return None

    def _place_window(self, x, y, w, h):
        win = self.win
        # сначала нормальный geometry (для одного монитора координаты 0,0)
        try:
            win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass
        win.update_idletasks()
        if os.name != "nt":
            return
        try:
            u = ctypes.windll.user32
            u.SetWindowPos.restype = ctypes.c_bool
            hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
            HWND_TOPMOST = -1
            SWP_SHOWWINDOW = 0x0040
            u.SetWindowPos(hwnd, HWND_TOPMOST, int(x), int(y), int(w), int(h), SWP_SHOWWINDOW)
        except Exception as e:
            self.log(f"SetWindowPos failed: {e}")

    def _build_window(self):
        x, y, w, h = virtual_screen()
        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.configure(bg="black", cursor="none")
        win.attributes("-topmost", True)
        try:
            win.attributes("-fullscreen", False)
        except Exception:
            pass

        # картинка на весь экран
        try:
            path = resource_path(SCREAMER_IMAGE)
            if Image is not None and os.path.exists(path):
                img = Image.open(path).convert("RGB")
                img = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.45))
                self._img_ref = ImageTk.PhotoImage(img)
                self._image_label = tk.Label(win, image=self._img_ref, bd=0, bg="black")
                self._image_label.place(x=0, y=0, width=w, height=h)
            else:
                self.log(f"картинка скримера не найдена: {path}")
        except Exception as e:
            self.log(f"image error: {e}")

        # текст по центру
        self._text_label = tk.Label(
            win,
            text="",
            bg="black",
            fg="#e6e6e6",
            font=("Consolas", 26, "bold"),
            justify="center",
            wraplength=int(w * 0.72),
        )
        self._text_label.place(relx=0.5, rely=0.5, anchor="center")

        win.bind("<Alt-F4>", lambda e: "break")
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        win.lift()
        try:
            win.focus_force()
            win.grab_set()
        except Exception:
            pass
        self._place_window(x, y, w, h)

        # поверх всего ещё раз, после того как окно реально создано
        try:
            win.attributes("-topmost", True)
            win.lift()
        except Exception:
            pass

    # -- печать -------------------------------------------------------------
    def _render(self):
        parts = list(self._lines_done)
        cur = self._cur + ("█" if self._cursor_on else "")
        parts.append(cur)
        self._text_label.config(text="\n".join(parts))

    def _blink(self):
        if self._finished:
            return
        self._cursor_on = not self._cursor_on
        self._flicker_i += 1
        # лёгкое «мерцание» старой плёнки
        shades = ["#e6e6e6", "#d8d8d8", "#e6e6e6", "#c9c9c9", "#e6e6e6", "#f0f0f0"]
        try:
            self._text_label.config(fg=shades[self._flicker_i % len(shades)])
        except Exception:
            pass
        self._render()
        self._after(380, self._blink)

    def _type_step(self):
        if self._finished:
            return
        line = SCREAMER_LINES[self._line_i]
        if self._char_i < len(line):
            self._cur = line[: self._char_i + 1]
            self._char_i += 1
            self._cursor_on = True
            self._render()
            ch = line[self._char_i - 1]
            if not ch.isspace():
                play_click(self.log)
            delay = random.uniform(CHAR_DELAY_MIN, CHAR_DELAY_MAX)
            if ch in ".…":
                delay += 0.35
            elif ch == ",":
                delay += 0.18
            self._after(int(delay * 1000), self._type_step)
            return

        # строка допечатана
        self._lines_done.append(line)
        self._cur = ""
        _log = self.log
        _log(f"скример: строка {self._line_i + 1}/{len(SCREAMER_LINES)} допечатана")
        self._line_i += 1
        self._char_i = 0
        self._render()
        if self._line_i >= len(SCREAMER_LINES):
            self._after(int(HOLD_AFTER_LAST * 1000), self._finish)
        else:
            self._after(int(LINE_PAUSE * 1000), self._type_step)

    # -- запуск/остановка ----------------------------------------------------
    def show(self):
        if tk is None:
            self.log("скример: нет tkinter — показать окно невозможно")
            return
        self.log("скример: старт")
        # 1) блокируем ввод и звук ДО показа окна
        try:
            self.set_input_blocked(True)
        except Exception as e:
            self.log(f"input block failed: {e}")
        self.cursor.lock()
        self.muter.start()

        self._build_window()
        self._render()
        self._after(400, self._blink)
        self._after(600, self._type_step)

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        self.log("скример: конец, возвращаю всё как было")
        for aid in self._after_ids:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._after_ids = []
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
        self._img_ref = None
        try:
            self.cursor.unlock()
        except Exception:
            pass
        try:
            self.muter.restore()
        except Exception:
            pass
        try:
            self.set_input_blocked(False)
        except Exception:
            pass
        if self.on_done is not None:
            try:
                self.on_done()
            except Exception as e:
                self.log(f"on_done error: {e}")
