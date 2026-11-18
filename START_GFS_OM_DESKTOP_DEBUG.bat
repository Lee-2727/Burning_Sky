@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Burning Sky V2.2.0 - Desktop GFS OM Debug
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run_desktop_gfs_om_debug.py
) else (
    python run_desktop_gfs_om_debug.py
)
if errorlevel 1 (
    echo.
    echo [ERROR] Please run INSTALL_DESKTOP_DEBUG_DEPENDENCIES.bat first.
    echo         Python 3.11 / 3.12 is recommended.
    pause
)
