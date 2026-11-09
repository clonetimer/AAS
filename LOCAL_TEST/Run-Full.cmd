@echo off
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-LocalTests.ps1" -Mode full
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%. Check outputs\phase53_windows_tests for logs.
pause
exit /b %RC%
