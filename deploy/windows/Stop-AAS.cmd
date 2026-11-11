@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Stop-AAS.ps1" %*
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
endlocal
exit /b %RC%
