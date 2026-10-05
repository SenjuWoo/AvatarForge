@echo off
setlocal
cd /d "%~dp0"
if not exist ".runtime\python\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Install.ps1" -Component launcher
  if errorlevel 1 goto failed
)
"%~dp0.runtime\python\python.exe" "%~dp0run.py" connect --providers auto
if errorlevel 1 goto failed
echo AvatarForge configuration is ready. Restart or reload your AI client.
pause
exit /b 0
:failed
echo Some clients could not be registered. Read the specific error above.
pause
exit /b 1
