@echo off
rem Debug launcher: keeps the console and disables exit-on-close.
cd /d "%~dp0"
set SONORA_EXIT_ON_CLOSE=0
python -m app.server
pause
