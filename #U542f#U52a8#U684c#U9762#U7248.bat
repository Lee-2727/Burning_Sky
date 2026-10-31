@echo off
cd /d "%~dp0"
python desktop\fire_cloud_forecast_v1_4_67_local_200km_burn_scale.py
if errorlevel 1 pause
