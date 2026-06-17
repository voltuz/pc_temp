# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small **Windows-only** tkinter GUI that shows live CPU + GPU temperatures with stacked history graphs. It is **pure Python standard library** — no pip packages (tkinter, ctypes, struct). The sensor data comes from **HWiNFO**, which the app drives as a hidden background engine.

## Commands

```powershell
.\setup.ps1                                   # one-time: create .venv, download HWiNFO portable to tools\hwinfo\, write its INI
.\run.bat                                      # launch (windowless via pythonw; triggers one UAC prompt)
.venv\Scripts\python.exe -m py_compile app.py sensors.py   # syntax check (there is no test suite / linter)
.venv\Scripts\python.exe sensors.py            # standalone sensor read loop — must run from an elevated shell, prints {cpu, gpu}
```

- `run.bat` launches under `pythonw.exe` (no console). For debugging, run `.venv\Scripts\python.exe app.py` from an **elevated** terminal so tracebacks are visible (under `pythonw` stdout/stderr are discarded).
- There are no automated tests. Verify changes by launching the app (or `sensors.py`) elevated and observing readings; UI/graph changes are typically confirmed via a screenshot.
- `.venv` may contain leftover `pythonnet` from an earlier design — it is unused; the app imports only stdlib.

## Why HWiNFO (the central design constraint)

LibreHardwareMonitor — even the latest pre-release — **cannot read this machine's Ryzen 9950X CPU temperature** on the X870 board (it reports 0 °C; Zen 5 / X870 SMU support is missing). HWiNFO reads it correctly. Reading a Ryzen CPU temperature on Windows fundamentally needs **ring-0/admin** access, and HWiNFO's executable is manifested `requireAdministrator` — so **admin is mandatory** and the app self-elevates. The NVIDIA GPU temp (NVAPI) would work without admin, but the CPU temp will not.

## Architecture (data flow across `app.py` + `sensors.py`)

1. `app.py:main()` self-elevates: `_relaunch_as_admin()` re-launches via `_pythonw()` + `ShellExecuteW("runas", ...)` so the elevated instance has no console; `_hide_console()` is a fallback if started under `python.exe`.
2. `App` (in `app.py`) starts a `Poller` thread and drives the Tk UI:
   - `Poller.run()` calls `sensors.HWiNFOProcess.start()` to launch `tools\hwinfo\HWiNFO64.exe` **hidden + elevated** (via `ShellExecuteEx` — `subprocess`/`CreateProcess` fails with error 740 on the requireAdministrator manifest). It then reads `sensors.SharedMemoryReader.read()` once per `POLL_INTERVAL` and pushes `("data", cpu, gpu)` onto a `queue.Queue`.
   - `App._tick()` (scheduled with `root.after`) drains the queue, appends to `cpu_hist` / `gpu_hist` deques, updates the two numeric readouts, and redraws the two graphs.
3. On window close, `App.on_close()` stops the poller, whose `_cleanup()` terminates HWiNFO — **only if the app started it** (`_we_started`); a pre-existing user HWiNFO instance is reused and left alone.

### HWiNFO lifecycle quirks (in `sensors.py:HWiNFOProcess` + `app.py:Poller`)
- **Window hiding:** HWiNFO ignores the `SW_HIDE` launch hint, so `hide_windows()` force-hides its top-level windows by PID via `ShowWindow(SW_HIDE)`, called repeatedly during startup (`_wait_for_sm`) and once per poll.
- **Watchdog:** if shared memory yields no readings for `STALE_LIMIT` polls (e.g. HWiNFO Free disables shared memory after ~12 h), the poller restarts HWiNFO.
- HWiNFO's tray icon cannot be suppressed programmatically — it's hidden via a one-time Windows Taskbar setting (documented in README).

### Shared-memory parsing (`sensors.py:SharedMemoryReader`)
Reads `Global\HWiNFO_SENS_SM2` via `OpenFileMapping`/`MapViewOfFile`. Format is a **packed** struct: signature `b"HWiS"`, then section offsets/sizes/counts at byte offsets 20/24/28/32/36/40; each reading element's value is a little-endian double at offset **284**. Selection is deliberately locale-robust because HWiNFO localizes display labels:
- **CPU**: temperature reading whose label contains the token `Tctl`.
- **GPU**: first temperature on a sensor whose **name** contains `NVIDIA`/`GEFORCE` (vendor names aren't localized), preferring the discrete card over the AMD iGPU.
Non-finite / `<=0` / `>150` values are treated as "no reading" (`None`).

## Configuration

All UI/behaviour knobs are constants at the **top of `app.py`**: `POLL_INTERVAL`, `HISTORY_SECONDS`, `UI_REFRESH_MS`, graph range `TEMP_MIN`/`TEMP_MAX`/`GRID_STEP`, `CPU_THRESH`/`GPU_THRESH` (amber/red thresholds), colours, `WINDOW_TITLE`, `INITIAL_GEOMETRY`.

`tools\hwinfo\HWiNFO64.INI` controls HWiNFO: `SensorsOnly=1` + `SensorsSM=1` are required; `HighestIdeAddress=-1` (skip drive/S.M.A.R.T. scan) and `SMBus=0` (skip RAM-SPD scan) cut cold-start from ~5 s to ~1.7 s without affecting CPU/GPU temps. The INI template is duplicated in `setup.ps1` — change both.

## Gotchas

- **Windows-only**: the whole sensor/elevation/window-hiding layer is Win32 `ctypes`. There is no cross-platform path.
- **`setup.ps1` must stay pure ASCII**: Windows PowerShell 5.1 reads a UTF-8-without-BOM `.ps1` as ANSI, and any non-ASCII char (e.g. an em-dash) decodes into a smart quote that breaks the parser. Use `-`, not `—`.
- HWiNFO is downloaded from a SourceForge **master mirror** (the plain `/files/.../download` URL returns an HTML interstitial, not the zip); `setup.ps1` falls back to manual-download instructions if that fails.
