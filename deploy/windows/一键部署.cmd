@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Deploy-AAS.ps1" %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo [OK] AAS deployment completed. Use Start-AAS.cmd to launch.
) else (
  echo [FAIL] deployment failed. See the log path printed above.
)
echo.
pause
endlocal
exit /b %RC%
