"""PC Temperature Monitor — a lightweight tkinter GUI for live CPU/GPU temps.

Shows the current CPU (AMD) and GPU (NVIDIA) temperatures as large, colour-coded
readouts plus a rolling history graph drawn natively on a tkinter Canvas. Polls
the sensors once per second in a background thread to keep the UI responsive.

Temperatures come from HWiNFO: the app launches HWiNFO hidden in the background
(it self-elevates so HWiNFO inherits administrator rights without a second
prompt), reads CPU + GPU temps from HWiNFO's shared memory, and terminates
HWiNFO again when the window is closed.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import font as tkfont

from sensors import HWINFO_EXE, HWiNFOProcess, SharedMemoryReader

# ----------------------------------------------------------------- config
POLL_INTERVAL = 1.0        # seconds between sensor samples
HISTORY_SECONDS = 600      # how much history to keep / show (10 minutes)
UI_REFRESH_MS = 250        # how often the UI drains the sample queue

TEMP_MIN = 20              # graph Y-axis bounds (degrees Celsius)
TEMP_MAX = 100
GRID_STEP = 10             # horizontal gridline spacing (degrees Celsius)

# (warm_at, hot_at) thresholds in degrees C: >= warm -> amber, >= hot -> red.
CPU_THRESH = (70, 85)
GPU_THRESH = (65, 80)

# Palette (dark theme).
COLOR_BG = "#1e1e1e"
COLOR_PANEL = "#252526"
COLOR_GRID = "#3a3a3a"
COLOR_AXIS_TEXT = "#9a9a9a"
COLOR_CPU = "#4fc3f7"      # light blue
COLOR_GPU = "#81c784"      # light green
COLOR_GOOD = "#66bb6a"
COLOR_WARM = "#ffb74d"
COLOR_HOT = "#ef5350"
COLOR_TEXT = "#e0e0e0"
COLOR_MUTED = "#8a8a8a"

WINDOW_TITLE = "PC Temperature Monitor"
INITIAL_GEOMETRY = "780x640"


# ----------------------------------------------------------- admin helpers
def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pythonw() -> str:
    """The windowless interpreter (pythonw.exe) next to the current one."""
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return cand if os.path.isfile(cand) else sys.executable


def _hide_console() -> None:
    """Hide this process's console window if it has one (no-op under pythonw)."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def _relaunch_as_admin() -> bool:
    """Relaunch this script elevated, windowless. Returns True if it started."""
    script = os.path.abspath(sys.argv[0])
    args = [script] + sys.argv[1:]
    params = " ".join('"%s"' % a for a in args)
    workdir = os.path.dirname(script)
    try:
        # Launch via pythonw.exe so the elevated instance has no console window.
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", _pythonw(), params, workdir, 1
        )
    except Exception:
        return False
    return rc > 32  # ShellExecute returns > 32 on success


def _temp_color(temp, thresh) -> str:
    if temp is None:
        return COLOR_MUTED
    warm, hot = thresh
    if temp >= hot:
        return COLOR_HOT
    if temp >= warm:
        return COLOR_WARM
    return COLOR_GOOD


# ------------------------------------------------------------- poller thread
class Poller(threading.Thread):
    """Runs HWiNFO (if needed) and samples its shared memory on its own thread."""

    SM_WAIT_SECONDS = 25       # how long to wait for HWiNFO to populate shared memory
    STALE_LIMIT = 12           # consecutive empty reads before restarting HWiNFO

    def __init__(self, out_queue: "queue.Queue", stop_event: threading.Event):
        super().__init__(daemon=True)
        self._q = out_queue
        self._stop = stop_event
        self.reader = SharedMemoryReader()
        self.hwinfo = HWiNFOProcess()
        self._we_started = False

    def _wait_for_sm(self, seconds: int) -> bool:
        # Step in 0.25s so we hide HWiNFO's window quickly after it appears.
        for _ in range(seconds * 4):
            if self._we_started:
                self.hwinfo.hide_windows()
            if self.reader.available():
                return True
            if self._stop.wait(0.25):
                return False
        return self.reader.available()

    def run(self) -> None:
        # Reuse a running HWiNFO if present; otherwise launch our own hidden one.
        if not self.reader.available():
            if not self.hwinfo.exe_exists:
                self._q.put(("error", "HWiNFO64.exe not found — run setup.ps1."))
                return
            if not self.hwinfo.start():
                self._q.put(("error", "Could not start HWiNFO (administrator required)."))
                return
            self._we_started = True
            self._wait_for_sm(self.SM_WAIT_SECONDS)

        if not self.reader.available():
            self._q.put(("error", "HWiNFO shared memory unavailable (enable 'Shared Memory Support')."))
            self._cleanup()
            return

        misses = 0
        while not self._stop.is_set():
            start = time.monotonic()
            if self._we_started:
                self.hwinfo.hide_windows()  # keep HWiNFO hidden if it re-shows
            reading = self.reader.read()
            if reading["cpu"] is None and reading["gpu"] is None:
                misses += 1
                if misses >= self.STALE_LIMIT and self._we_started:
                    # Shared memory went stale (e.g. free-version 12h limit) — restart.
                    self.hwinfo.stop()
                    if self.hwinfo.start():
                        self._wait_for_sm(15)
                    misses = 0
            else:
                misses = 0
            self._q.put(("data", reading["cpu"], reading["gpu"]))
            elapsed = time.monotonic() - start
            self._stop.wait(max(0.0, POLL_INTERVAL - elapsed))
        self._cleanup()

    def _cleanup(self) -> None:
        self.reader.close()
        if self._we_started:
            self.hwinfo.stop()


# --------------------------------------------------------------------- app
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        self.stop_event = threading.Event()

        self.maxlen = max(2, int(round(HISTORY_SECONDS / POLL_INTERVAL)))
        self.cpu_hist: "deque" = deque(maxlen=self.maxlen)
        self.gpu_hist: "deque" = deque(maxlen=self.maxlen)
        self.errored = False

        self._build_ui()

        self.poller = Poller(self.q, self.stop_event)
        self.poller.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(UI_REFRESH_MS, self._tick)

    # -- UI construction --
    def _build_ui(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.configure(bg=COLOR_BG)
        self.root.geometry(INITIAL_GEOMETRY)
        self.root.minsize(520, 460)

        self._font_big = tkfont.Font(family="Segoe UI", size=40, weight="bold")
        self._font_small = tkfont.Font(family="Segoe UI", size=12)

        top = tk.Frame(self.root, bg=COLOR_BG)
        top.pack(fill="x", padx=16, pady=(14, 4))

        self.cpu_value = self._readout(top, "CPU", COLOR_CPU)
        self.gpu_value = self._readout(top, "GPU", COLOR_GPU)

        self.status = tk.Label(
            self.root, text="Starting HWiNFO…", font=self._font_small,
            fg=COLOR_MUTED, bg=COLOR_BG, anchor="w",
        )
        self.status.pack(fill="x", padx=18, pady=(0, 4))

        graphs = tk.Frame(self.root, bg=COLOR_BG)
        graphs.pack(fill="both", expand=True, padx=16, pady=(4, 14))

        self.cpu_canvas = tk.Canvas(graphs, bg=COLOR_PANEL, highlightthickness=0)
        self.cpu_canvas.pack(fill="both", expand=True, pady=(0, 6))
        self.cpu_canvas.bind(
            "<Configure>",
            lambda _e: self._draw_graph(self.cpu_canvas, self.cpu_hist, COLOR_CPU, "CPU"),
        )

        self.gpu_canvas = tk.Canvas(graphs, bg=COLOR_PANEL, highlightthickness=0)
        self.gpu_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.gpu_canvas.bind(
            "<Configure>",
            lambda _e: self._draw_graph(self.gpu_canvas, self.gpu_hist, COLOR_GPU, "GPU"),
        )

    def _readout(self, parent, label, accent):
        col = tk.Frame(parent, bg=COLOR_BG)
        col.pack(side="left", expand=True, fill="x")
        tk.Label(col, text=label, font=self._font_small, fg=accent, bg=COLOR_BG).pack()
        value = tk.Label(col, text="—", font=self._font_big, fg=COLOR_MUTED, bg=COLOR_BG)
        value.pack()
        return value

    # -- periodic UI update --
    def _tick(self) -> None:
        latest = None
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "error":
                    self._show_error(item[1])
                    return  # stop scheduling; window stays visible
                _, cpu, gpu = item
                self.cpu_hist.append(cpu)
                self.gpu_hist.append(gpu)
                latest = (cpu, gpu)
        except queue.Empty:
            pass

        if latest is not None:
            self._update_readouts(*latest)
            self._draw_graph(self.cpu_canvas, self.cpu_hist, COLOR_CPU, "CPU")
            self._draw_graph(self.gpu_canvas, self.gpu_hist, COLOR_GPU, "GPU")

        self.root.after(UI_REFRESH_MS, self._tick)

    def _update_readouts(self, cpu, gpu) -> None:
        self.cpu_value.config(
            text=("%.0f°" % cpu) if cpu is not None else "—",
            fg=_temp_color(cpu, CPU_THRESH),
        )
        self.gpu_value.config(
            text=("%.0f°" % gpu) if gpu is not None else "—",
            fg=_temp_color(gpu, GPU_THRESH),
        )
        if cpu is None and gpu is None:
            self.status.config(text="Waiting for HWiNFO readings…", fg=COLOR_WARM)
        else:
            self.status.config(
                text="HWiNFO · sampling every %gs · history %d min"
                % (POLL_INTERVAL, HISTORY_SECONDS // 60),
                fg=COLOR_MUTED,
            )

    def _show_error(self, msg: str) -> None:
        self.errored = True
        self.cpu_value.config(text="—", fg=COLOR_HOT)
        self.gpu_value.config(text="—", fg=COLOR_HOT)
        self.status.config(text="Sensor error: " + msg, fg=COLOR_HOT)

    # -- graph drawing --
    def _draw_graph(self, canvas, hist, color, title) -> None:
        c = canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20 or h < 20:
            return

        left, right, top, bottom = 44, 14, 14, 28
        plot_w = w - left - right
        plot_h = h - top - bottom
        if plot_w <= 10 or plot_h <= 10:
            return

        def y_for(temp):
            t = min(max(temp, TEMP_MIN), TEMP_MAX)
            frac = (t - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)
            return top + plot_h * (1 - frac)

        # Gridlines + Y-axis labels.
        for temp in range(TEMP_MIN, TEMP_MAX + 1, GRID_STEP):
            y = y_for(temp)
            c.create_line(left, y, left + plot_w, y, fill=COLOR_GRID)
            c.create_text(left - 6, y, text=str(temp), anchor="e",
                          fill=COLOR_AXIS_TEXT, font=("Segoe UI", 8))
        c.create_line(left, top, left, top + plot_h, fill=COLOR_GRID)

        # Series (newest sample pinned to the right edge).
        self._draw_series(c, hist, color, left, plot_w, y_for)

        # Time-axis hints.
        c.create_text(left, top + plot_h + 14, text="-%d min" % (HISTORY_SECONDS // 60),
                      anchor="w", fill=COLOR_AXIS_TEXT, font=("Segoe UI", 8))
        c.create_text(left + plot_w, top + plot_h + 14, text="now",
                      anchor="e", fill=COLOR_AXIS_TEXT, font=("Segoe UI", 8))

        # Title (top-left, in the series' accent colour).
        c.create_text(left + 4, top + 2, text=title, anchor="nw",
                      fill=color, font=("Segoe UI", 11, "bold"))

    def _draw_series(self, canvas, hist, color, left, plot_w, y_for) -> None:
        vals = list(hist)
        m = len(vals)
        if m == 0:
            return
        step = plot_w / (self.maxlen - 1) if self.maxlen > 1 else 0.0

        segment = []  # flat [x0, y0, x1, y1, ...] of consecutive non-None points
        for k, v in enumerate(vals):
            if v is None:
                if len(segment) >= 4:
                    canvas.create_line(*segment, fill=color, width=2)
                segment = []
                continue
            x = left + plot_w - (m - 1 - k) * step
            segment.extend((x, y_for(v)))
        if len(segment) >= 4:
            canvas.create_line(*segment, fill=color, width=2)

        if vals[-1] is not None:  # marker on the latest reading
            x = left + plot_w
            y = y_for(vals[-1])
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline="")

    # -- shutdown --
    def on_close(self) -> None:
        self.stop_event.set()
        self.poller.join(timeout=2.0)
        self.root.destroy()


def main() -> None:
    _hide_console()  # in case we were started via python.exe rather than pythonw.exe

    # Self-elevate so HWiNFO can be launched with administrator rights (needed to
    # read the sensors) without a second UAC prompt.
    if os.name == "nt" and not _is_admin():
        if _relaunch_as_admin():
            return  # elevated instance launched; this one exits
        # UAC declined — HWiNFO won't be able to read sensors; the app still
        # opens and shows an error in the status line.

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
