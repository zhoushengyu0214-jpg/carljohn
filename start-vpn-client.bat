@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0vpn_client.py"
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0vpn_client.py"
    exit /b %errorlevel%
)

echo Python 3 was not found. Install Python 3.10 or later with Tcl/Tk, then reopen this launcher.
echo Download Python from https://www.python.org/downloads/windows/
pause
exit /b 1
