@echo off
REM Launch the STELLA overlay client as Administrator (required so keystrokes
REM reach Star Citizen / EAC, which run elevated). Double-click this file.
REM Self-elevates via UAC if not already running as admin.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
client\.venv\Scripts\python.exe -m client.app %*
