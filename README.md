# PC Temperature Monitor

A small, lightweight Windows GUI that shows your **CPU and GPU temperatures in
real time**, with a rolling **history graph**. Built with Python + tkinter and
**no third-party Python packages** — sensor data comes from
[HWiNFO](https://www.hwinfo.com/).

- **CPU:** AMD Ryzen 9950X — reads `CPU (Tctl/Tdie)`
- **GPU:** NVIDIA RTX 3090 — reads the GPU core temperature
- Live readouts colour-coded green / amber / red
- 10-minute history graph drawn natively on a tkinter Canvas
- Polls once per second on a background thread — light on CPU and memory

## Why HWiNFO?

On this machine (Ryzen 9950X + X870), LibreHardwareMonitor — even the latest
pre-release — cannot read the CPU temperature (it reports 0 °C). HWiNFO reads it
correctly, so the app uses HWiNFO purely as a sensor engine:

- When you open the monitor, it **launches HWiNFO hidden** (sensors-only, no
  window) and reads CPU + GPU temps from HWiNFO's **shared memory**.
- When you **close the monitor, it terminates HWiNFO** — so HWiNFO runs *only
  while the monitor is open*, never on its own.

The one thing that can't be suppressed while HWiNFO runs is its **tray icon**.
Hide it once in Windows: **Settings → Personalization → Taskbar → Other system
tray icons → HWiNFO → Off**. After that you never see it.

## Requirements

- Windows 11
- **Python 3.x** (3.13 recommended; the app is pure standard library)
- **Administrator (UAC)**: HWiNFO needs admin to read sensors, so the app
  self-elevates on launch (one UAC prompt; HWiNFO inherits the elevation).

## Setup (one time)

From a normal PowerShell prompt in this folder:

```powershell
.\setup.ps1
```

This creates `.venv`, downloads **HWiNFO portable** into `tools\hwinfo\`, and
writes `tools\hwinfo\HWiNFO64.INI` (sensors-only, shared memory on, and the
slow **drive (S.M.A.R.T.) and SMBus/RAM-SPD scans disabled** — `HighestIdeAddress=-1`
and `SMBus=0` — so HWiNFO starts in ~1.5 s instead of ~5 s; CPU/GPU temps are
unaffected since they don't use those buses).

## Run

```powershell
.\run.bat
```

Accept the **UAC prompt**. The window shows live CPU + GPU temperatures and the
history graph fills in over the following minutes. It runs under `pythonw.exe`,
so there is **no console window** (and HWiNFO runs hidden in the background).

## Configuration

All knobs live in a single block at the top of `app.py`:

| Setting | Default | Meaning |
|---|---|---|
| `POLL_INTERVAL` | `1.0` | Seconds between samples |
| `HISTORY_SECONDS` | `600` | History window shown on the graph (10 min) |
| `TEMP_MIN` / `TEMP_MAX` | `20` / `100` | Graph Y-axis range (°C) |
| `CPU_THRESH` | `(70, 85)` | Amber at 70 °C, red at 85 °C |
| `GPU_THRESH` | `(65, 80)` | Amber at 65 °C, red at 80 °C |

## Troubleshooting

- **Readouts stuck on "—" / "Waiting for HWiNFO…":** make sure you accepted UAC.
  If HWiNFO's free *Shared Memory Support* was turned off, re-enable it (the app
  auto-restarts HWiNFO if shared memory goes stale, e.g. the free-version 12-hour
  limit).
- **HWiNFO tray icon visible:** hide it via the Taskbar settings above.
- **HWiNFO download failed in setup:** grab the *Portable* zip from
  <https://www.hwinfo.com/download/> and copy `HWiNFO64.exe` into `tools\hwinfo\`.
- **Antivirus flags HWiNFO's driver:** HWiNFO loads a kernel driver to read
  sensors; some AV products flag it as a false positive.

## Files

| File | Purpose |
|---|---|
| `app.py` | tkinter GUI, polling thread, admin self-elevation |
| `sensors.py` | HWiNFO shared-memory reader + HWiNFO process manager |
| `setup.ps1` | Creates `.venv`, downloads HWiNFO portable, writes its INI |
| `run.bat` | Convenience launcher |
| `tools\hwinfo\` | HWiNFO64.exe + HWiNFO64.INI (sensors-only, shared memory) |
