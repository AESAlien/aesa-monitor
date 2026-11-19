@echo off
rem Copy the board scripts and the laptop ssh public key to the processor SD card (FAT, aesa folder).
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare_board_sd.ps1" %*
pause
