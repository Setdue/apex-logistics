@echo off
setlocal
cd /d "%~dp0"
g++ EnterprisePOS.cpp -std=c++17 -O2 -Wall -mwindows -lgdi32 -o EnterprisePOS_UI.exe
if errorlevel 1 pause
