@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-AAS.ps1" -OpenBrowser %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
endlocal
exit /b %RC%
