@echo off
rem Launch the PC Temperature Monitor with no console window.
rem Uses pythonw.exe (no console) and "start" so this batch closes immediately.
rem The app self-elevates (UAC) so HWiNFO can read the sensors.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py" %*
