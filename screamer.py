# -*- coding: utf-8 -*-
"""
Скример (10-я «реплика») + «возрождение» после него.

Звук — не синтез на ходу, а заранее подготовленные сэмплы из открытой
библиотеки uisfx (MIT, https://github.com/romainsimon/uisfx): они лежат в
assets/sfx/*.wav, а «тяжёлая» обработка делается в tools/build_sfx.py.

Картинка — своя, из assets/frames.bin (набор кадров, собранных
tools/build_frames.py): лицо «висит» в темноте, дышит, дёргается, глитчит.

Что происходит на 10-е нажатие Alt+F4:
  * на весь экран (все мониторы) кадры без рамок и кнопок, поверх всего;
  * по центру по одной букве печатается текст — удар на каждую клавишу,
    глухой гул фоном, стеклянный акцент на точках;
  * звук всех остальных приложений (Discord в том числе) глушится;
  * клавиатура, мышь и переключение окон блокируются;
  * после последней строки — белая вспышка и «ангельский» звук: возрождение.

Ограничение: Ctrl+Alt+Del перехватить невозможно (это защита самой Windows).
"""

import ctypes
import os
import random
import struct
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
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None
try:
    from PIL import ImageTk
except Exception:  # ImageTk требует tkinter
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

RATE = 22050
CHAR_DELAY_MIN = 0.055
CHAR_DELAY_MAX = 0.115
LINE_PAUSE = 0.95
HOLD_AFTER_LAST = 1.1
MAX_DURATION_MS = 120_000  # предохранитель

# громкости слоёв
VOL_BED = 0.55
VOL_KEY = 0.95
VOL_ACCENT = 0.75
VOL_ANGEL = 1.0


# ---------------------------------------------------------------------------
# Пути и ресурсы
# ---------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def sfx_path(name):
    return resource_path(os.path.join("assets", "sfx", name))


def frames_path():
    return resource_path(os.path.join("assets", "frames.bin"))


def notify_sound_path(n):
    """Звук для окна с репликой: 1–9, по нарастающей."""
    n = max(1, min(9, int(n or 1)))
    return sfx_path(f"notify{n}.wav")


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
# Звук: чтение сэмплов, микс трека, воспроизведение
# ---------------------------------------------------------------------------
_wav_cache = {}


def read_wav(path):
    """mono float32-подобный array('f') в диапазоне [-1, 1]."""
    if path in _wav_cache:
        return _wav_cache[path]
    out = array("f")
    try:
        with wave.open(path, "rb") as w:
            ch = w.getnchannels()
            sw = w.getsampwidth()
            sr = w.getframerate()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            raise ValueError(f"ожидался 16-bit wav: {path}")
        pcm = array("h")
        pcm.frombytes(raw)
        if ch > 1:
            # усредняем каналы
            pcm = array("h", [sum(pcm[i:i + ch]) // ch for i in range(0, len(pcm), ch)])
        if sr != RATE:
            # простой ресемпл (ассеты и так в 22050, страховка)
            n = int(len(pcm) * RATE / sr)
            out = array("f", [pcm[int(i * sr / RATE)] / 32768.0 for i in range(n)])
        else:
            out = array("f", [v / 32768.0 for v in pcm])
    except Exception as e:
        print(f"[sfx] не прочитал {path}: {e}", file=sys.stderr)
        return array("f")
    _wav_cache[path] = out
    return out


def play_file(path, loop=False, log=print):
    if os.name != "nt":
        return
    try:
        import winsound

        flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        if loop:
            flags |= winsound.SND_LOOP
        winsound.PlaySound(path, flags)
    except Exception as e:
        log(f"play error: {e}")


def stop_sound(log=print):
    if os.name != "nt":
        return
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception as e:
        log(f"stop sound error: {e}")


# ---------------------------------------------------------------------------
# Таймлайн печати
# ---------------------------------------------------------------------------
def build_timeline(seed=424242):
    """Когда какая буква печатается. Один источник правды и для звука, и для окна."""
    rnd = random.Random(seed)
    t = 1.0
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


_track_lock = threading.Lock()
_track_ready = None  # (wav_path, events, lines, t_angel)


def compose_track(log=print):
    """Микширует ОДИН wav: гул + удары по клавишам + акценты + ангел в конце.

    Всё в stdlib: winsound умеет играть только один файл, поэтому слои
    складываем заранее. Готовится в фоне при старте приложения.
    """
    started = __import__("time").time()
    events, lines, t_end = build_timeline()
    t_angel = t_end + HOLD_AFTER_LAST
    total = t_angel + 6.2
    n = int(total * RATE)

    bed = read_wav(sfx_path("bed.wav"))
    keys = [read_wav(sfx_path(f"key{i}.wav")) for i in (1, 2, 3, 4)]
    keys = [k for k in keys if k]
    accent = read_wav(sfx_path("accent.wav"))
    angel = read_wav(sfx_path("angel.wav"))

    buf = array("f", bytes(4 * n))  # нули
    if len(buf) != n:
        buf = array("f", [0.0] * n)

    # 1) гул, зацикленный до момента ангела, с затуханием перед ним
    if bed:
        bn = len(bed)
        end_bed = int(t_angel * RATE)
        for i in range(min(n, end_bed)):
            buf[i] += bed[i % bn] * VOL_BED
        fade_len = int(0.7 * RATE)
        start_fade = max(0, end_bed - fade_len)
        for i in range(start_fade, min(n, end_bed)):
            buf[i] *= (end_bed - i) / fade_len

    # 2) удары по клавишам
    if keys:
        rnd = random.Random(777)
        for (t, ch) in events:
            if ch.isspace():
                continue
            sample = rnd.choice(keys)
            off = int(t * RATE)
            sn = len(sample)
            gain = VOL_KEY * rnd.uniform(0.75, 1.0)
            for j in range(sn):
                k = off + j
                if k >= n:
                    break
                buf[k] += sample[j] * gain

    # 3) акцент на точках и многоточиях
    if accent:
        for (t, ch) in events:
            if ch not in ".…":
                continue
            off = int(t * RATE)
            for j in range(len(accent)):
                k = off + j
                if k >= n:
                    break
                buf[k] += accent[j] * VOL_ACCENT

    # 4) ангел
    if angel:
        off = int(t_angel * RATE)
        for j in range(min(len(angel), n - off)):
            buf[off + j] += angel[j] * VOL_ANGEL

    # 5) мягкий лимитер и запись
    pcm = array("h", bytes(2 * n))
    for i in range(n):
        v = buf[i]
        if v > 0.9:
            v = 0.9 + (v - 0.9) * 0.35
        elif v < -0.9:
            v = -0.9 + (v + 0.9) * 0.35
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        pcm[i] = int(v * 32767)

    path = os.path.join(tempfile.gettempdir(), "anti_alt_f4_screamer.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())

    log(f"трек собран за {__import__('time').time() - started:.1f}s: "
        f"{total:.1f}s, событий {len(events)}, ангел на {t_angel:.1f}s")
    return path, events, lines, t_angel


def get_track(log=print):
    global _track_ready
    with _track_lock:
        if _track_ready is None:
            _track_ready = compose_track(log)
        return _track_ready


# ---------------------------------------------------------------------------
# Кадры
# ---------------------------------------------------------------------------
_frames_lock = threading.Lock()
_frames_ready = None  # список PIL.Image


def load_frames(log=print):
    """Читает assets/frames.bin (набор JPEG-кадров) -> список картинок."""
    global _frames_ready
    with _frames_lock:
        if _frames_ready is not None:
            return _frames_ready
        path = frames_path()
        if Image is None or not os.path.exists(path):
            log(f"кадры скримера не найдены: {path}")
            _frames_ready = []
            return _frames_ready
        with open(path, "rb") as f:
            blob = f.read()
        if blob[:4] != b"AAF4":
            log("frames.bin повреждён")
            _frames_ready = []
            return _frames_ready
        count, w, h = struct.unpack("<HHH", blob[4:10])
        off = 10
        out = []
        import io

        for _ in range(count):
            ln = struct.unpack("<I", blob[off:off + 4])[0]
            off += 4
            out.append(Image.open(io.BytesIO(blob[off:off + ln])).convert("RGB"))
            off += ln
        log(f"кадры скримера загружены: {len(out)} шт, {w}x{h}")
        _frames_ready = out
        return out


def frame_timeline():
    """Сценарий анимации: (мс от старта, индекс кадра).

    Лицо просто висит, дышит, иногда дёргается и «глитчит» — без криков.
    """
    return [
        (0, 0), (900, 1), (1700, 2), (2500, 3), (3300, 4), (3800, 0),
        (5200, 5), (5600, 6), (6000, 0), (7400, 7), (7620, 8), (7900, 0),
        (9500, 1), (10400, 2), (11400, 3), (12400, 4), (12900, 0),
        (14200, 5), (14800, 6), (15400, 0), (16800, 8), (17100, 0),
        (18500, 9),
    ]


def warmup(log=print):
    """Заранее готовим звук и кадры, чтобы скример стартовал мгновенно."""
    def _worker():
        try:
            get_track(log)
        except Exception as e:
            log(f"warmup sound failed: {e}")
        try:
            load_frames(log)
        except Exception as e:
            log(f"warmup frames failed: {e}")

    t = threading.Thread(target=_worker, daemon=True, name="screamer-warmup")
    t.start()
    return t


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
        if self._coinit_done or os.name != "nt":
            return
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
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
                    vol.SetMute(False, None)
                    continue
                key = self._key(s) or f"pid:{pid}"
                with self._lock:
                    if key not in self._saved:
                        self._saved[key] = bool(vol.GetMute())
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
# Скример
# ---------------------------------------------------------------------------
class ScreamerOverlay:
    def __init__(self, root, log=print, set_input_blocked=None, on_done=None):
        self.root = root
        self.log = log
        self.set_input_blocked = set_input_blocked or (lambda flag: None)
        self.on_done = on_done

        self.win = None
        self._label = None
        self._text = None
        self._photos = {}
        self._cur_photo = None
        self._screen = (1280, 720)
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
    def _prepare_photos(self):
        """JPEG -> PhotoImage под размер экрана (делаем на главном потоке)."""
        if ImageTk is None:
            return
        frames = load_frames(self.log)
        if not frames:
            return
        w, h = self._screen
        for i, img in enumerate(frames):
            try:
                fitted = ImageOps.fit(img, (w, h), method=Image.LANCZOS, centering=(0.5, 0.5))
                self._photos[i] = ImageTk.PhotoImage(fitted)
            except Exception as e:
                self.log(f"кадр {i} не подготовлен: {e}")
        self.log(f"кадры готовы к показу: {len(self._photos)}")

    def _show_frame(self, idx):
        if self._label is None:
            return
        photo = self._photos.get(idx)
        if photo is None:
            return
        self._cur_photo = photo
        try:
            self._label.config(image=photo, bg="black")
        except Exception as e:
            self.log(f"frame show error: {e}")

    def _play_timeline(self):
        if self._finished:
            return
        for (ms, idx) in frame_timeline():
            self._after(ms, lambda i=idx: self._show_frame(i))

    # -- окно ---------------------------------------------------------------
    def _build_window(self):
        x, y, w, h = virtual_screen()
        self._screen = (w, h)
        win = tk.Toplevel(self.root)
        self.win = win
        win.overrideredirect(True)
        win.configure(bg="black", cursor="none")
        win.attributes("-topmost", True)

        self._label = tk.Label(win, bd=0, bg="black")
        self._label.place(x=0, y=0, width=w, height=h)

        self._text = tk.Label(
            win,
            text="",
            bg="black",
            fg="#f2f2f2",
            font=("Consolas", 26, "bold"),
            justify="center",
            wraplength=int(w * 0.70),
        )
        self._text.place(relx=0.5, rely=0.53, anchor="center")

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
            self._text.config(text="\n".join(parts))
        except Exception:
            pass

    def _blink(self):
        if self._finished:
            return
        self._cursor_on = not self._cursor_on
        shades = ["#f2f2f2", "#cfcfcf", "#ffffff", "#a9a9a9", "#f2f2f2", "#fafafa"]
        self._flash_i = getattr(self, "_flash_i", 0) + 1
        try:
            self._text.config(fg=shades[self._flash_i % len(shades)])
        except Exception:
            pass
        self._render()
        self._after(340, self._blink)

    def _schedule_typing(self, events, lines):
        """Печать строго по тому же таймлайну, по которому собран звук."""
        state = {"i": 0, "text": ""}
        self._lines_done = []
        self._cur = ""
        self._cursor_on = True

        def step():
            if self._finished or state["i"] >= len(events):
                return
            t, ch = events[state["i"]]
            state["i"] += 1
            state["text"] += ch
            self._cur = state["text"]
            self._cursor_on = True
            self._render()
            if state["i"] < len(events):
                nxt = events[state["i"]][0]
                self._after((nxt - t) * 1000, step)

        if events:
            self._after(events[0][0] * 1000, step)

        for (start, end, line) in lines:
            def start_line():
                state["text"] = ""

            def done(line=line):
                if self._finished:
                    return
                self._lines_done.append(line)
                self._cur = ""
                self._render()

            self._after(start * 1000, start_line)
            self._after(end * 1000 + 40, done)

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
            self._label.config(image="", bg=color)
            self._cur_photo = None
            self._text.config(text="", bg=color)
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
            path, events, lines, t_angel = get_track(self.log)
        except Exception as e:
            self.log(f"трек не собрался: {e}")
            path, events, lines, t_angel = None, [], [], 8.0

        try:
            self._build_window()
            self._prepare_photos()
            self._show_frame(0)
            self._render()
            self._after(300, self._blink)
            self._play_timeline()
            if path:
                play_file(path, log=self.log)
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
        self._cur_photo = None
        self._photos.clear()
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
