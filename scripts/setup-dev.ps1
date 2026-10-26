# Developer setup on a Windows PC: one venv with the brain AND a terminal, so you can
# talk to Jarvis on this machine. Safe to re-run.
#   powershell -ExecutionPolicy Bypass -File scripts\setup-dev.ps1
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher 'py' not found. Install Python 3.12+ from python.org (tick 'py launcher')."
}

if (-not (Test-Path .venv)) {
    Write-Host "== creating .venv"
    py -3 -m venv .venv
}
$Py = Join-Path $Repo ".venv\Scripts\python.exe"

Write-Host "== installing protocol, brain and terminal"
& $Py -m pip install -q --disable-pip-version-check --upgrade pip
$Brain = "brain"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $Brain = "brain[gpu]" }  # CUDA libs for GPU speech-to-text
& $Py -m pip install -q --disable-pip-version-check -e protocol -e $Brain -e terminal

Write-Host "== fetching Piper voice and wake word model"
& $Py brain\scripts\get_voice.py
& $Py -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example"
}

Write-Host ""
Write-Host "Done. In two terminals:"
Write-Host "  .venv\Scripts\python -m jarvis_brain"
Write-Host "  .venv\Scripts\python -m jarvis_terminal"
Write-Host "Then open http://localhost:8080 and say 'hey Jarvis' (or press Enter in the terminal window)."
