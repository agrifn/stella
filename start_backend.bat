@echo off
REM Ensure the STELLA backend (the WSL2 Docker container) is running, and keep WSL
REM alive for as long as STELLA is open so it does not idle-shut and kill the backend.
REM
REM The backend lives inside WSL2, which Windows does NOT boot at login and which
REM idle-stops the VM after a short time with no session. So before launching the
REM client we: boot the distro + bring the container up (systemd auto-starts Docker),
REM start a lightweight keepalive bound to this console (i.e. this STELLA session),
REM and wait until the API actually answers from the Windows side.
REM
REM Called by start_stella.bat / start_manager.bat; can also be run on its own.

set "DISTRO=Ubuntu-24.04"
set "COMPOSE=/opt/stella-stack/docker-compose.yml"

echo Starting STELLA backend (WSL2 Docker)...
wsl -d %DISTRO% -u root docker compose -f %COMPOSE% up -d

REM Keepalive: a background WSL process that lives as long as this console window.
REM Launching the client keeps the console open, so WSL stays up for the whole
REM session; when STELLA closes, this exits and WSL may idle-stop again. Harmless if
REM one is already running.
start /b "" wsl -d %DISTRO% -u root --exec sleep infinity

echo Waiting for backend to answer...
set /a n=0
:wait
curl.exe -s -m 2 http://127.0.0.1:8420/health >nul 2>&1 && goto up
set /a n+=1
if %n% geq 45 (
  echo WARNING: backend did not respond after ~45s. Check WSL / Docker on this PC.
  goto end
)
ping -n 2 127.0.0.1 >nul
goto wait
:up
echo Backend is up.
:end
