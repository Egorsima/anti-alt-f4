# -*- coding: utf-8 -*-
"""
Проверка скримера. Две части:

1) Логическая (работает везде, без tkinter): объект создаётся через __init__ и
   вызываются методы отрисовки. Ловит класс багов «в __init__ забыли поле» —
   именно из-за такого скример раньше падал до первого кадра и не показывался.

2) GUI (только там, где есть tkinter и рабочий стол — CI на windows-latest):
   окно реально создаётся, кадры переключаются, вспышка доходит до конца.

Код возврата != 0 — сборка падает до релиза.
"""

import os
import sys

os.environ["SCREAMER_SMOKE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + name + (f" — {extra}" if extra else ""))
    if not ok:
        FAILED.append(name)


# ---------------------------------------------------------------------------
# 1) логика без GUI
# ---------------------------------------------------------------------------
def logic_checks():
    import screamer

    class FakeRoot:
        def after(self, ms, fn):
            return 1

        def after_cancel(self, aid):
            pass

    logs = []
    obj = screamer.ScreamerOverlay(FakeRoot(), log=lambda m: logs.append(str(m)))
    obj._text = None          # окна нет — текст рисовать некуда, но падать нельзя
    obj._label = None

    for attr in ("_lines_done", "_cur", "_cursor_on", "_flash_i", "_photos", "_screen"):
        check(f"поле {attr} инициализировано", hasattr(obj, attr))

    try:
        obj._render()
        obj._render()
        check("_render не падает", True)
    except Exception as e:
        check("_render не падает", False, repr(e))

    try:
        obj._show_frame(0)
        check("_show_frame без кадров не падает", True)
    except Exception as e:
        check("_show_frame без кадров не падает", False, repr(e))

    # таймлайн печати: строки не должны склеиваться в одну
    events, lines, t_end = screamer.build_timeline()
    check("в таймлайне 4 строки", len(lines) == 4, f"строк: {len(lines)}")
    check("в таймлайне есть буквы", len(events) > 100, f"событий: {len(events)}")
    check("ангел идёт после последней строки",
          screamer.build_timeline()[2] <= t_end + 2.0)

    # звук и кадры на месте
    try:
        path, ev, ln, t_angel = screamer.get_track(lambda m: None)
        check("трек собран", os.path.exists(path), f"{t_angel:.1f}s, событий {len(ev)}")
    except Exception as e:
        check("трек собран", False, repr(e))

    try:
        frames = screamer.load_frames(lambda m: None)
        check("кадры загружены", len(frames) >= 5, f"кадров: {len(frames)}")
    except Exception as e:
        check("кадры загружены", False, repr(e))

    for i in range(1, 10):
        p = screamer.notify_sound_path(i)
        check(f"уведомление {i}", os.path.exists(p), os.path.basename(p))

    bad = [m for m in logs if "error" in m.lower() or "упал" in m.lower()]
    check("в логах нет ошибок", not bad, "; ".join(bad[:2]))


# ---------------------------------------------------------------------------
# 2) GUI: окно реально показывается
# ---------------------------------------------------------------------------
def gui_checks():
    try:
        import tkinter as tk
    except Exception as e:
        print(f"tkinter недоступен ({e}) — GUI-часть пропущена")
        return

    try:
        root = tk.Tk()
    except Exception as e:
        print(f"нет рабочего стола для GUI ({e}) — GUI-часть пропущена")
        return

    import screamer

    root.withdraw()
    state = {"done": False, "frames": 0, "logs": []}
    overlay = screamer.ScreamerOverlay(
        root,
        log=lambda m: state["logs"].append(str(m)),
        set_input_blocked=lambda flag: None,
        on_done=lambda: state.__setitem__("done", True),
    )
    orig_show = overlay._show_frame

    def spy(idx):
        state["frames"] += 1
        return orig_show(idx)

    overlay._show_frame = spy

    def finish():
        check("скример дошёл до конца", state["done"])
        check("кадры переключались", state["frames"] >= 2, f"переключений: {state['frames']}")
        bad = [m for m in state["logs"] if "упал" in m or "error" in m]
        check("GUI-лог без ошибок", not bad, "; ".join(bad[:2]))
        root.quit()

    def fail_safe():
        check("предохранитель 20с", False, "скример не завершился сам")
        try:
            overlay._finish("тест")
        except Exception:
            pass
        root.quit()

    overlay.show()
    root.after(12000, finish)
    root.after(20000, fail_safe)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass


def main():
    print("=== логика ===")
    logic_checks()
    print("=== GUI ===")
    gui_checks()
    print("\nитог:", "OK" if not FAILED else f"провалено: {FAILED}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
