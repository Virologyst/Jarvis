# Start this PC's Jarvis terminal whenever you sign in, with no window, and keep it running.
#   powershell -ExecutionPolicy Bypass -File scripts\install-terminal.ps1             # install + start
#   powershell -ExecutionPolicy Bypass -File scripts\install-terminal.ps1 -Uninstall  # remove
# The task runs as you (it needs your mic, speakers and screen), only while you're signed in.
# Logs: logs\terminal.log. Restart: Stop-ScheduledTask / Start-ScheduledTask "Jarvis terminal".
param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "Jarvis terminal"
$Repo = Split-Path -Parent $PSScriptRoot
$PythonW = Join-Path $Repo ".venv\Scripts\pythonw.exe"
$Service = Join-Path $Repo "scripts\terminal_service.pyw"

function Stop-Terminal {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'jarvis_terminal|terminal_service' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
if ($Uninstall) {
    Stop-Terminal
    Write-Host "Removed the $TaskName task and stopped the terminal."
    return
}
if (-not (Test-Path $PythonW)) { throw "No ${PythonW}: run scripts\setup-dev.ps1 first." }

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Service`"" -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Jarvis terminal (mic, speaker, screen at localhost:8080)" | Out-Null

Stop-Terminal                       # one terminal only: the task's
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed: the Jarvis terminal starts when $user signs in, and is running now."
