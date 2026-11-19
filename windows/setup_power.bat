@echo off
rem Turn off sleep, lid action, USB selective suspend and NIC power saving for the aesa demo (or restore).
rem Runs setup_power.ps1 as administrator (a UAC prompt appears).
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0setup_power.ps1\"'"
