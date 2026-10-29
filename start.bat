@echo off
rem Sonora launcher: no console window, exits when the page is closed.
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
  start "" python -m app.server
) else (
  start "" pythonw -m app.server
)
