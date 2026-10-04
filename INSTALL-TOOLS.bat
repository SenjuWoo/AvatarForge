@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Install.ps1" -Component all
if errorlevel 1 (
  echo Tool installation stopped. Read the error above.
  pause
  exit /b 1
)
call "%~dp0START-HERE.bat"
