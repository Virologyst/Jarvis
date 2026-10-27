# Set up the Jarvis brain on the always-on PC so it starts at boot, before anyone logs in.
# Safe to re-run (after a git pull, or to change settings): it replaces what it made last time.
#
# Run from an elevated PowerShell (Run as Administrator) in the repo root, as the account the
# brain should run as (on the brain PC that's "Jarvis"):
#   powershell -ExecutionPolicy Bypass -File brain\install.ps1
#
# What it does:
#   1. Installs the brain's Python packages into .venv (protocol + brain[gpu]) and fetches the voice.
#   2. Creates .env from the example if there isn't one (add ANTHROPIC_API_KEY to it).
#   3. Registers the "Jarvis brain" scheduled task: at startup, as -User, with S4U logon (no
#      password stored; runs whether or not anyone is logged in, still reaches the internet,
#      and uses that account's Claude Code login for self-upgrades). It runs
#      brain\start-brain.cmd, i.e. the supervisor, so upgrades can restart the brain and a bad
#      one gets rolled back. Output goes to logs\brain.log. No time limit; Windows restarts the
#      task if the supervisor itself dies.
#   4. Opens the brain's port in the firewall for the Private (home/office) network only.
#   5. Stops the PC sleeping or hibernating on mains power.
#   6. Starts the task now and waits for the brain to answer /health.
#
# Options:
#   -User <name>     account to run as (default: whoever runs this script)
#   -Port <n>        brain port (default 8765, must match JARVIS_PORT in .env)
#   -SkipInstall     don't touch .venv (just (re)register the task)
#   -NoStart         register everything but don't start the brain now
#
# Day to day:
#   Start-ScheduledTask -TaskName "Jarvis brain"     # start
#   Stop-ScheduledTask  -TaskName "Jarvis brain"     # stop
#   Get-Content logs\brain.log -Tail 50 -Wait        # watch the log
# Don't also start the brain by hand in a window: two brains can't share the port.
param(
    [string]$User = $env:USERNAME,
    [int]$Port = 8765,
    [switch]$SkipInstall,
    [switch]$NoStart
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
$TaskName = "Jarvis brain"

$principalNow = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principalNow.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from PowerShell opened with 'Run as Administrator'."
}

# 1. Python environment (brain only; no terminal or audio libraries needed here)
if (-not $SkipInstall) {
    Write-Host "== Python environment"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not (Test-Path $Py)) {
        if ($uv) { & uv venv (Join-Path $Repo ".venv") } else { py -3 -m venv (Join-Path $Repo ".venv") }
    }
    $brainPkg = (Join-Path $Repo "brain") + "[gpu]"
    if ($uv) {
        & uv pip install --python $Py -q -e (Join-Path $Repo "protocol") -e $brainPkg
    } else {
        & $Py -m pip install -q --disable-pip-version-check -e (Join-Path $Repo "protocol") -e $brainPkg
    }
    if ($LASTEXITCODE -ne 0) { throw "Installing the brain's packages failed." }
    & $Py (Join-Path $Repo "brain\scripts\get_voice.py")
}

# 2. Config
$envFile = Join-Path $Repo ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Repo ".env.example") $envFile
    Write-Warning "Created .env from the example: add ANTHROPIC_API_KEY to it."
}
New-Item -ItemType Directory -Force (Join-Path $Repo "logs") | Out-Null

# 3. Scheduled task
Write-Host "== Scheduled task '$TaskName' (at startup, as $User, no login needed)"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    # Stopping the task ends start-brain.cmd but can leave its Python running: stop this
    # repo's supervisor and brain too, so the new task can have the port.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'jarvis_brain' -and $_.ExecutablePath -like "$Repo\*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}
$action = New-ScheduledTaskAction -Execute (Join-Path $Repo "brain\start-brain.cmd") -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Jarvis voice assistant brain (supervisor). See brain\install.ps1." | Out-Null

# 4. Firewall: terminals on the local network must reach the brain
Write-Host "== Firewall: TCP $Port on Private networks"
Get-NetFirewallRule -DisplayName "Jarvis brain" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Jarvis brain" -Direction Inbound -Protocol TCP -LocalPort $Port `
    -Profile Private -Action Allow | Out-Null

# 5. Power: an always-on brain mustn't sleep
Write-Host "== Power: no sleep or hibernate on mains power"
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off

# 6. Start it and check it answers
if ($NoStart) {
    Write-Host "Done. The brain will start at the next boot (or: Start-ScheduledTask -TaskName '$TaskName')."
    return
}
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Warning ("Port $Port is already in use (process $($busy[0].OwningProcess)): a brain started by hand? " +
        "Stop it, then: Start-ScheduledTask -TaskName '$TaskName'")
    return
}
Write-Host "== Starting the brain (loading models can take a minute or two)"
Start-ScheduledTask -TaskName $TaskName
$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 3
        if ($health.ok) {
            Write-Host "Done. Jarvis brain is up on port $Port and will start at every boot."
            return
        }
    } catch { }
}
Write-Warning "The brain didn't answer within 4 minutes. Check $Repo\logs\brain.log"
