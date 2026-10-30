@echo off
setlocal
cd /d "%~dp0"
py -3 northstar_pos.py
if errorlevel 1 pause
