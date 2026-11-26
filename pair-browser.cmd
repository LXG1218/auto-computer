@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-auth.ps1" %*
set "EC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %EC%
