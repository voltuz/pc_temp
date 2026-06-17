# setup.ps1 - one-time setup for PC Temperature Monitor (HWiNFO-backed).
# Creates a local .venv and makes sure HWiNFO portable + its INI are in place.
# The app itself uses only the Python standard library (no pip packages).
#
# Run from a normal (non-admin) PowerShell:  .\setup.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# --- 1. Virtual environment (kept for isolation, per project convention) ---
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    if (Get-Command py -ErrorAction SilentlyContinue) {
        py -3.13 -m venv .venv
        if (-not (Test-Path ".venv")) { python -m venv .venv }
    } else {
        python -m venv .venv
    }
} else {
    Write-Host ".venv already exists - reusing it."
}
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { throw "Could not find $venvPy after venv creation." }
Write-Host "App uses only the Python standard library - no pip packages to install."

# --- 2. HWiNFO portable -----------------------------------------------------
$hwDir = Join-Path $root "tools\hwinfo"
$hwExe = Join-Path $hwDir "HWiNFO64.exe"
New-Item -ItemType Directory -Force -Path $hwDir | Out-Null
if (-not (Test-Path $hwExe)) {
    Write-Host "Downloading HWiNFO portable..."
    $name = "hwi_834.zip"
    $zip = Join-Path $env:TEMP $name
    $url = "https://master.dl.sourceforge.net/project/hwinfo/Windows_Portable/" + $name + "?viasf=1"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -Headers @{ "User-Agent" = "Mozilla/5.0" }
        if ((Get-Item $zip).Length -lt 1000000) { throw "downloaded file too small (mirror returned a page, not the zip)" }
        $exDir = Join-Path $env:TEMP "hwi_portable_extract"
        if (Test-Path $exDir) { Remove-Item -Recurse -Force $exDir }
        Expand-Archive -Path $zip -DestinationPath $exDir
        Copy-Item (Join-Path $exDir "HWiNFO64.exe") $hwExe -Force
        Write-Host "  -> $hwExe"
    } catch {
        Write-Warning ("Automatic HWiNFO download failed: " + $_.Exception.Message)
        Write-Host "Please download HWiNFO 'Portable' from https://www.hwinfo.com/download/"
        Write-Host "and copy HWiNFO64.exe into: $hwDir"
    }
} else {
    Write-Host "HWiNFO64.exe already present - skipping download."
}

# --- 3. HWiNFO INI (sensors-only + shared memory) - only if missing --------
$ini = Join-Path $hwDir "HWiNFO64.INI"
if (-not (Test-Path $ini)) {
    Write-Host "Writing HWiNFO64.INI (sensors-only + shared memory)..."
    $iniText = @"
[Settings]
SensorsOnly=1
SummaryOnly=0
OpenSystemSummary=0
SensorsSM=1
MinimizeSensorsOnStartup=1
MinimizeMainOnStartup=1
ShowWelcomeAndProgress=0
AutoUpdate=0
AutoUpdateBeta=0
DisableAutoUpdate=1
EnableUpdateCheck=0
LogStartupValues=0
HighestIdeAddress=-1
SMBus=0
"@
    Set-Content -Path $ini -Value $iniText -Encoding ASCII
} else {
    Write-Host "HWiNFO64.INI already present - keeping it."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Launch with:  .\run.bat   (accept the UAC prompt so HWiNFO can read the sensors)"
Write-Host "Optional: hide HWiNFO's tray icon via Settings > Personalization > Taskbar > Other system tray icons."
