@echo off
setlocal
cd /d "%~dp0"
if not exist ".runtime\python\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Install.ps1" -Component launcher
  if errorlevel 1 goto failed
)
"%~dp0.runtime\python\python.exe" "%~dp0run.py" ui
if errorlevel 1 goto failed
exit /b 0
:failed
echo AvatarForge stopped. Read the error above or .runtime\setup.log.
pause
exit /b 1
