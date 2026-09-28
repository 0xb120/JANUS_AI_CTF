@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-Janus.ps1" -Mode stand -Provider mock -SttProvider disabled -TtsProvider disabled %*
endlocal
