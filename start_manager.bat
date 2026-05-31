@echo off
REM Launch the STELLA Command & Voice Manager GUI. Does NOT need admin (it only
REM talks to the backend API and plays audio). Use the "Launch STELLA" button
REM inside it to start the overlay (which self-elevates for in-game keystrokes).
cd /d "%~dp0"
client\.venv\Scripts\python.exe -m client.command_manager
