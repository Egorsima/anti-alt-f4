# -*- coding: utf-8 -*-
"""
Anti Alt+F4 — тестовая версия.

Запустил -> висит в трее.
Нажал Alt+F4 -> комбинация блокируется глобальным low-level хуком,
по центру экрана появляется окно с репликой (1..9, дальше остаётся 9).
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------------------
# Реплики (из README, 1..9; 10-я — скример — пока не используем)
# ---------------------------------------------------------------------------
REPLIKAS = [
    "Ты чо сука?",
    "Блять да не жми тебе говорю",
    "ДА ТЫ ПИДОРАС НЕ ЖМИ СУКА КНОПКУ!!!",
    "последнее предупреждение...",
    "ты глухой?",
    "я тебе щас руку сломаю",
    "нажмёшь ещё раз кнопку = фембой",
    "аххахааххахаха ну ты и лох",
    "ну ладно теперь если нажмёшь то пиздец тебе",
]

APP_NAME = "Anti Alt+F4"
MAX_ATTEMPTS = len(REPLIKAS)
SCREAMER_ATTEMPT = MAX_ATTEMPTS + 1  # 10-е нажатие -> скример

# --- оформление попапа ------------------------------------------------------
BG = "#1a1a1a"
FG = "#f5f5f5"
ACCENT = "#dc2626"
PADX, PADY = 34, 26
MAX_TEXT_W = 560          # максимальная ширина текста в пикселях
MIN_POPUP_W, MAX_POPUP_W = 380, 660
MIN_POPUP_H = 200


# ---------------------------------------------------------------------------
# Логирование: в noconsole-сборке sys.stdout/stderr == None, обычный print падает
# ---------------------------------------------------------------------------
LOG_PATH = None


def _log_path():
    global LOG_PATH
    if LOG_PATH:
        return LOG_PATH
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.getcwd()
    folder = os.path.join(base, "AntiAltF4")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        folder = os.environ.get("TEMP", ".")
    LOG_PATH = os.path.join(folder, "app.log")
    return LOG_PATH


def _log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    for stream in (sys.stderr, sys.stdout):
        try:
            if stream is not None:
                stream.write(line + "\n")
                stream.flush()
        except Exception:
            pass



# ---------------------------------------------------------------------------
# Иконка для трея (генерируется на лету через Pillow)
# ---------------------------------------------------------------------------
def make_tray_image():
    from PIL import Image, ImageDraw, ImageFont

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # тёмный фон
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=12, fill=(26, 26, 26, 255))
    # красная рамка
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=12, outline=(220, 38, 38, 255), width=4)
    # текст F4
    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        try:
            font = ImageFont.load_default(size=26)
        except Exception:
            font = ImageFont.load_default()
    text = "F4"
    try:
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, fill=(255, 255, 255, 255), font=font)
    except Exception:
        d.text((14, 18), text, fill=(255, 255, 255, 255), font=font)
    # красный слэш
    d.line([(10, size - 10), (size - 10, 10)], fill=(220, 38, 38, 255), width=5)
    return img


# ---------------------------------------------------------------------------
# Глобальный хук Alt+F4 (только Windows)
# ---------------------------------------------------------------------------
class AltF4Hook:
    """WH_KEYBOARD_LL хук: глотает Alt+F4 и дёргает callback."""

    WH_KEYBOARD_LL = 13
    VK_F4 = 0x73
    VK_MENU = 0x12  # Alt
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104

    def __init__(self, on_alt_f4):
        self.on_alt_f4 = on_alt_f4
        self.block_all = False  # True = глотать любые клавиши (режим скримера)
        self._hook = None
        self._thread_id = None
        self._thread = None
        self._last_trigger = 0.0
        self._debounce_sec = 0.35
        self._user32 = None
        self._kernel32 = None
        self._proc = None
        self._hook_error = None

    @property
    def active(self):
        return self._hook is not None

    def start(self):
        if os.name != "nt":
            _log("не Windows — хук отключён")
            return False
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        user32 = self._user32
        kernel32 = self._kernel32

        # ВАЖНО: без явных restype/argtypes 64-битные хендлы обрезаются до 32 бит
        # и SetWindowsHookExW падает -> Alt+F4 перестаёт блокироваться.
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.CallNextHookEx.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            ctypes.c_void_p,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        self._KBDLLHOOKSTRUCT = KBDLLHOOKSTRUCT
        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        hook_self = self

        def _proc(nCode, wParam, lParam):
            try:
                if nCode >= 0 and hook_self.block_all:
                    # скример на экране: не пропускаем ни одну клавишу
                    return 1
                if nCode >= 0 and wParam in (hook_self.WM_KEYDOWN, hook_self.WM_SYSKEYDOWN):
                    info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if info.vkCode == hook_self.VK_F4:
                        alt_down = user32.GetAsyncKeyState(hook_self.VK_MENU) & 0x8000
                        if alt_down:
                            now = time.monotonic()
                            if now - hook_self._last_trigger >= hook_self._debounce_sec:
                                hook_self._last_trigger = now
                                try:
                                    hook_self.on_alt_f4()
                                except Exception as e:
                                    _log(f"callback error: {e}")
                            return 1  # проглотить Alt+F4
            except Exception as e:
                _log(f"proc error: {e}")
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = HOOKPROC(_proc)

        def _loop():
            hmod = kernel32.GetModuleHandleW(None)
            hook = user32.SetWindowsHookExW(
                hook_self.WH_KEYBOARD_LL, self._proc, hmod, 0
            )
            if not hook:
                err = kernel32.GetLastError()
                _log(f"SetWindowsHookExW failed, GetLastError={err}")
                hook_self._hook_error = err
                return
            hook_self._hook = hook
            hook_self._thread_id = kernel32.GetCurrentThreadId()
            msg = wintypes.MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:  # WM_QUIT (0) или ошибка (-1)
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            _log("hook loop finished")

        self._thread = threading.Thread(target=_loop, daemon=True, name="altf4-hook")
        self._thread.start()
        # дать потоку поднять хук
        for _ in range(50):
            if self._hook is not None:
                break
            time.sleep(0.02)
        _log(f"hook active={bool(self._hook)} err={self._hook_error}")
        return bool(self._hook)

    def stop(self):
        try:
            if self._hook is not None and self._user32 is not None:
                self._user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
            if self._thread_id is not None and self._user32 is not None:
                # WM_QUIT = 0x0012
                self._user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Приложение
# ---------------------------------------------------------------------------
class AntiAltF4App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()  # живём в трее, окна нет
        self.root.title(APP_NAME)

        self.attempts = 0
        self._lock = threading.Lock()
        self.events = queue.Queue()  # события из хука -> в tkinter-поток

        self.popup = None
        self.popup_text = None
        self.popup_counter = None
        self.popup_body = None
        self.popup_header = None
        self.input_blocked = False
        self.screamer = None
        self.screamer_busy = False

        self.sound_enabled = True
        self.hook = AltF4Hook(on_alt_f4=self._on_alt_f4_from_hook)
        self.tray_icon = None
        self._exiting = False

    # -- события ------------------------------------------------------------
    def _on_alt_f4_from_hook(self):
        # вызывается из потока хука — только кладём в очередь
        self.events.put("alt_f4")

    def _handle_alt_f4(self):
        if self.screamer_busy:
            return
        with self._lock:
            self.attempts += 1
            attempts = self.attempts
        if attempts >= SCREAMER_ATTEMPT:
            # 10-е нажатие: скример, потом счётчик начинается заново
            _log(f"Alt+F4 заблокирован, попытка #{attempts} -> СКРИМЕР")
            self._close_popup()
            # даём окну успеть исчезнуть, чтобы не мешало полноэкранному слою
            self.root.after(60, self._run_screamer)
            return
        n = min(attempts, MAX_ATTEMPTS)
        text = REPLIKAS[n - 1]
        _log(f"Alt+F4 заблокирован, попытка #{attempts}: {text}")
        self._show_popup(text, n)

    # -- скример -------------------------------------------------------------
    def _run_screamer(self):
        if self.screamer_busy:
            return
        self._close_popup()
        self.screamer_busy = True
        try:
            from screamer import ScreamerOverlay
        except Exception as e:
            _log(f"скример недоступен: {e}")
            self.screamer_busy = False
            return

        def _done():
            self.screamer_busy = False
            self.screamer = None
            with self._lock:
                self.attempts = 0  # после скримера всё по новой
            _log("скример отработал, счётчик сброшен")
            # переустанавливаем хук «с чистого листа»: пока скример глотал все
            # клавиши, система могла «потерять» отпускание Alt -> залипший Alt
            try:
                self._reinstall_hook()
            except Exception as e:
                _log(f"переустановка хука не удалась: {e}")

        try:
            self.screamer = ScreamerOverlay(
                self.root,
                log=_log,
                set_input_blocked=self._set_input_blocked,
                on_done=_done,
            )
            self.screamer.show()
        except Exception:
            import traceback

            _log("скример упал: " + traceback.format_exc())
            self.screamer_busy = False
            self._set_input_blocked(False)

    def _reinstall_hook(self):
        """Снять и заново поставить низкоуровневый хук (сбрасывает залипшие клавиши)."""
        try:
            self.hook.stop()
        except Exception:
            pass
        import time as _t

        _t.sleep(0.15)
        ok = self.hook.start()
        _log(f"хук переустановлен: {ok}")
        if not ok:
            # вторая попытка, как при старте
            _t.sleep(0.4)
            ok = self.hook.start()
            _log(f"хук переустановлен со 2-й попытки: {ok}")
        return ok

    def _set_input_blocked(self, flag):
        """Пока скример на экране — хук глотает вообще все клавиши."""
        self.input_blocked = flag
        try:
            self.hook.block_all = flag
        except Exception:
            pass
        _log(f"ввод {'заблокирован' if flag else 'разблокирован'}")

    # -- окно по центру ------------------------------------------------------
    def _center_window(self, win, width, height):
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        except Exception:
            sw, sh = 1280, 720
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _wrap_text(text, font, max_width):
        """Свой перенос строк: считаем реальную ширину в пикселях."""
        lines = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            cur = words[0]
            for word in words[1:]:
                trial = f"{cur} {word}"
                if font.measure(trial) <= max_width:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        return "\n".join(lines)

    def _play_sound(self, n):
        """Звук при появлении окна: системные звуки Windows (1–9, по нарастающей)."""
        if not self.sound_enabled:
            return
        try:
            import winsound
        except Exception:
            return

        def _worker():
            try:
                if n <= 0:
                    alias, times = "SystemAsterisk", 1
                elif n <= 3:
                    alias, times = "SystemExclamation", 1
                elif n <= 6:
                    alias, times = "SystemHand", 1
                else:
                    alias, times = "SystemHand", 2
                for _ in range(times):
                    try:
                        winsound.PlaySound(
                            alias,
                            winsound.SND_ALIAS
                            | winsound.SND_ASYNC
                            | winsound.SND_NODEFAULT,
                        )
                    except Exception:
                        winsound.MessageBeep(winsound.MB_ICONHAND)
                    if times > 1:
                        time.sleep(0.35)
            except Exception as e:
                _log(f"sound error: {e}")

        threading.Thread(target=_worker, daemon=True, name="sound").start()

    def _show_popup(self, text, n):
        # если окно уже открыто — обновить текст/размер и поднять наверх
        if self.popup is not None and self.popup.winfo_exists():
            self._fill_popup(text, n)
            self.popup.deiconify()
            self.popup.lift()
            self.popup.focus_force()
            self._shake(self.popup)
            self._play_sound(n)
            return

        win = tk.Toplevel(self.root)
        self.popup = win
        win.title(APP_NAME)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.configure(bg=BG)

        # контейнер: всё содержимое внутри, окно потом подгоняется по нему
        wrap = tk.Frame(win, bg=BG, padx=int(PADX), pady=int(PADY))
        wrap.pack(fill="both", expand=True)
        self.popup_body = wrap

        self.popup_header = tk.Label(
            wrap, text="", bg=BG, fg=ACCENT, font=("Segoe UI", 12, "bold"),
        )
        self.popup_header.pack(pady=(0, 10))

        self.popup_text = tk.Label(
            wrap, text="", bg=BG, fg=FG, font=self._combo_font(),
            justify="center", anchor="center",
        )
        self.popup_text.pack()

        self.popup_counter = tk.Label(
            wrap, text="", bg=BG, fg="#9ca3af", font=("Segoe UI", 10),
        )
        self.popup_counter.pack(pady=(10, 12))

        btn = tk.Button(
            wrap, text="Понял", command=self._close_popup,
            bg=ACCENT, fg="white", activebackground="#b91c1c",
            activeforeground="white", font=("Segoe UI", 11, "bold"),
            width=16, pady=5, relief="flat", cursor="hand2",
            bd=0, highlightthickness=0,
        )
        btn.pack()
        btn.focus_set()
        win.bind("<Return>", lambda e: self._close_popup())
        win.bind("<Escape>", lambda e: self._close_popup())
        win.bind("<Alt-F4>", lambda e: self._close_popup())

        # закрытие крестиком — просто спрятать окно, приложение живёт в трее
        win.protocol("WM_DELETE_WINDOW", self._close_popup)

        self._fill_popup(text, n)
        win.lift()
        win.focus_force()
        self._shake(win)
        self._play_sound(n)

    def _combo_font(self, size=17):
        return ("Segoe UI", size, "bold")

    def _fill_popup(self, text, n):
        """Обновить текст и подогнать окно под содержимое (ничего не срезается)."""
        win = self.popup
        if win is None:
            return
        # подбираем размер шрифта, чтобы длинные реплики влезали
        box_w = MAX_TEXT_W
        size = 17
        wrapped = ""
        while size >= 11:
            font = tkfont.Font(family="Segoe UI", size=size, weight="bold")
            wrapped = self._wrap_text(text, font, box_w)
            lines = wrapped.count("\n") + 1
            if lines <= 5:
                break
            size -= 1
        self.popup_text.config(text=wrapped, font=("Segoe UI", size, "bold"))
        self.popup_header.config(
            text="Alt+F4 заблокирован" if n > 0 else "Anti Alt+F4 активен"
        )
        self.popup_counter.config(
            text=f"Попытка {n} из {MAX_ATTEMPTS}" if n > 0 else "Жми Alt+F4"
        )

        # окно = запрошенный размер содержимого (ничего не срезается)
        win.update_idletasks()
        w = max(MIN_POPUP_W, min(MAX_POPUP_W, self.popup_body.winfo_reqwidth()))
        h = max(MIN_POPUP_H, self.popup_body.winfo_reqheight())
        # страховка: не вылезать за пределы экрана
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            w = min(w, int(sw * 0.9))
            h = min(h, int(sh * 0.9))
        except Exception:
            pass
        self._center_window(win, w, h)
        _log(f"попап: {w}x{h}, шрифт {size}, строк {wrapped.count(chr(10)) + 1}")

    def _shake(self, win):
        """Лёгкая тряска окна для эффекта."""
        try:
            geo = win.geometry()
            wh, xy = geo.split("+", 1)
            w, h = wh.split("x")
            x, y = int(xy.split("+")[0]), int(xy.split("+")[1])
        except Exception:
            return

        def step(i, dx):
            try:
                if not win.winfo_exists():
                    return
                if i > 6:
                    win.geometry(f"{w}x{h}+{x}+{y}")
                    return
                win.geometry(f"{w}x{h}+{x + dx}+{y}")
                win.after(30, lambda: step(i + 1, -dx if dx else 6))
            except Exception:
                pass

        win.after(60, lambda: step(0, 7))

    def _close_popup(self):
        if self.popup is not None:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None
            self.popup_text = None
            self.popup_counter = None

    def _show_info(self):
        with self._lock:
            n = self.attempts
        if n == 0:
            self._show_popup(
                "Приложение висит в трее. Alt+F4 не сработает — "
                "вместо него появится окно с репликой. "
                "На 10-й раз... ну, сам увидишь.",
                0,
            )
        else:
            self._show_popup(REPLIKAS[min(n, MAX_ATTEMPTS) - 1], min(n, MAX_ATTEMPTS))

    # -- трей ----------------------------------------------------------------
    def _setup_tray(self):
        try:
            import pystray
        except Exception as e:
            _log(f"pystray недоступен: {e}")
            return

        image = make_tray_image()

        def on_show(icon, item):
            # показать инфо-окно (из потока трея — через очередь в tkinter)
            self.events.put("show_info")

        def on_exit(icon, item):
            self.events.put("exit")

        def on_screamer_test(icon, item):
            # сознательно вызывается ТОЛЬКО из трея
            self.events.put("screamer_test")

        def on_toggle_sound(icon, item):
            self.sound_enabled = not self.sound_enabled
            _log(f"звук {'вкл' if self.sound_enabled else 'выкл'}")
            try:
                icon.update_menu()
            except Exception:
                pass

        menu = pystray.Menu(
            pystray.MenuItem("Показать", on_show, default=True),
            pystray.MenuItem("Проверить скример", on_screamer_test),
            pystray.MenuItem(
                "Звук", on_toggle_sound,
                checked=lambda item: self.sound_enabled,
            ),
            pystray.MenuItem("Выход", on_exit),
        )
        self.tray_icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        # двойной клик по иконке — показать окно
        try:
            self.tray_icon.on_activate = lambda icon: self.events.put("show_info")
        except Exception:
            pass

        t = threading.Thread(target=self.tray_icon.run, daemon=True, name="tray")
        t.start()

        # приветствие — с задержкой, иначе иконка ещё не успевает появиться в трее
        def _welcome():
            time.sleep(2.0)
            try:
                self.tray_icon.notify("Anti Alt+F4 запущен. Alt+F4 заблокирован.", APP_NAME)
                _log("уведомление в трее показано")
            except Exception as e:
                _log(f"notify failed: {e}")

        threading.Thread(target=_welcome, daemon=True, name="welcome").start()

    # -- выход ---------------------------------------------------------------
    def _exit(self):
        if self._exiting:
            return
        self._exiting = True
        _log("выход")
        # если выходим прямо во время скримера — вернуть звук и курсор
        try:
            if self.screamer is not None:
                self.screamer.muter.panic_unmute()
                self.screamer.cursor.unlock()
                self.screamer._finish("выход из приложения")
        except Exception as e:
            _log(f"аварийное восстановление скримера: {e}")
        try:
            from screamer import stop_sound

            stop_sound(_log)
        except Exception:
            pass
        try:
            self.hook.stop()
        except Exception:
            pass
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self._close_popup()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # -- запуск ---------------------------------------------------------------
    def run(self):
        _log("старт приложения")
        try:
            import screamer

            screamer.warmup(_log)
        except Exception as e:
            _log(f"прогрев звука не удался: {e}")
        self._setup_tray()
        if not self.hook.start():
            # вторая попытка: иногда первый SetWindowsHookExW не проходит
            _log("хук не встал с первого раза, повтор через 0.5s")
            time.sleep(0.5)
            if not self.hook.start():
                _log(f"хук НЕ установлен (err={self.hook._hook_error})")
        # опрос очереди событий в tkinter-потоке (+ обработка show_info/exit)
        def poll():
            try:
                while True:
                    ev = self.events.get_nowait()
                    if ev == "alt_f4":
                        self._handle_alt_f4()
                    elif ev == "show_info":
                        self._show_info()
                    elif ev == "screamer_test":
                        self._run_screamer()
                    elif ev == "exit":
                        self._exit()
                        return
            except queue.Empty:
                pass
            if not self._exiting:
                self.root.after(80, poll)

        self.root.after(80, poll)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self._exit()


def _fatal(msg):
    """Показать ошибку, даже если нет консоли (noconsole-сборка)."""
    _log(f"FATAL: {msg}")
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, msg, APP_NAME, 0x10
            )
    except Exception:
        pass


def _enable_dpi_awareness():
    """Без этого при масштабировании 125/150% Windows размеры окна врут."""
    if os.name != "nt":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception as e:
        _log(f"dpi awareness failed: {e}")


def main():
    # любые необработанные исключения — в лог, а не молчаливый вылет
    def _hook_exc(exc_type, exc, tb):
        import traceback

        _log("unhandled: " + "".join(traceback.format_exception(exc_type, exc, tb)))

    _enable_dpi_awareness()
    sys.excepthook = _hook_exc
    threading.excepthook = lambda a: _log(
        f"thread {getattr(a.thread, 'name', '?')} error: {a.exc_type.__name__}: {a.exc_value}"
    )

    # тестовый режим: показать окно сразу (для проверки без Windows)
    if os.environ.get("ANTI_ALT_F4_TEST") == "1":
        app = AntiAltF4App()
        app.root.after(500, lambda: app._show_popup(REPLIKAS[0], 1))
        app.root.after(3000, app._exit)
        app.run()
        return

    try:
        app = AntiAltF4App()
        app.run()
    except Exception as e:
        import traceback

        _fatal(f"Приложение упало:\n{e}\n\nПодробности: {_log_path()}")
        _log(traceback.format_exc())


if __name__ == "__main__":
    main()
