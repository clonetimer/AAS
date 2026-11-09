@echo off
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-TestEnv.ps1"
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%.
pause
exit /b %RC%
