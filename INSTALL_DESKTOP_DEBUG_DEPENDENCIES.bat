@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m pip install -r requirements-desktop-debug.txt
) else (
    python -m pip install -r requirements-desktop-debug.txt
)
if errorlevel 1 echo Install failed. Check Python and pip installation.
pause
