@echo off
rem Starts the Jarvis brain under its supervisor, logging to logs\brain.log.
rem Run by the "Jarvis brain" scheduled task at boot (see brain\install.ps1). There's no window,
rem so everything goes to the log. When the log passes 10 MB it becomes logs\brain.old.log.
cd /d "%~dp0.."
if not exist logs mkdir logs
if exist logs\brain.log for %%A in (logs\brain.log) do if %%~zA GTR 10485760 move /y logs\brain.log logs\brain.old.log >nul
echo ===== %DATE% %TIME% starting Jarvis brain (supervised) >> logs\brain.log
".venv\Scripts\python.exe" -m jarvis_brain.supervisor >> logs\brain.log 2>&1
