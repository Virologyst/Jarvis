# Install the Jarvis brain as a Windows service on the always-on PC (the 4090 box).
# STUB: the steps are laid out; the service part needs NSSM (https://nssm.cc) and admin rights.
# Run from an elevated PowerShell in the repo root:
#   powershell -ExecutionPolicy Bypass -File brain\install.ps1
#
# Design (see docs/architecture.md):
# - Runs under its own local account ("jarvis"), not yours or the apprentice's, so its
#   API key, mail tokens and memory are unreadable from other accounts.
# - Starts at boot without anyone logged in; restarts itself if it crashes.
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
$ServiceName = "JarvisBrain"

# 1. Python environment (brain only; no terminal or audio libraries needed here)
if (-not (Test-Path $Py)) { py -3 -m venv (Join-Path $Repo ".venv") }
& $Py -m pip install -q --disable-pip-version-check -e (Join-Path $Repo "protocol") -e "$(Join-Path $Repo 'brain')[gpu]"
& $Py (Join-Path $Repo "brain\scripts\get_voice.py")

# 2. Config: .env holds the API key. Lock it down to Administrators + the service account.
if (-not (Test-Path (Join-Path $Repo ".env"))) {
    Copy-Item (Join-Path $Repo ".env.example") (Join-Path $Repo ".env")
    Write-Warning "Created .env from the example: add ANTHROPIC_API_KEY before starting the service."
}

# 3. Service (TODO when the PC is set up):
#   - create the 'jarvis' local user:     New-LocalUser jarvis -NoPassword:$false ...
#   - grant it read on the repo, write on logs/ and data/, and lock .env with icacls
#   - nssm install $ServiceName $Py "-m jarvis_brain"
#   - nssm set $ServiceName AppDirectory $Repo
#   - nssm set $ServiceName ObjectName .\jarvis <password>
#   - nssm set $ServiceName AppStdout / AppStderr  -> $Repo\logs\brain.log (with rotation)
#   - nssm set $ServiceName AppExit Default Restart
#   - open the firewall for the brain port on the private network only:
#       New-NetFirewallRule -DisplayName "Jarvis brain" -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow
#   - power: powercfg /change standby-timeout-ac 0 ; powercfg /hibernate off
Write-Host "Environment ready. Service registration is still a TODO in this script (see comments)."
