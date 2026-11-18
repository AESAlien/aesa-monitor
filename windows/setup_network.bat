@echo off
rem Fix the laptop IPv4 address for the aesa demo network (192.168.10.x).
rem Runs setup_network.ps1 as administrator (a UAC prompt appears).
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0setup_network.ps1\"'"
