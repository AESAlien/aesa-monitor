@echo off
rem Loopback test without Zynq: fake processor (+ fake simulator/console) on 127.0.0.1,
rem aesa-monitor.exe captures the loopback adapter with dumpcap.
cd /d "%~dp0"
start "" "%~dp0dist\aesa-monitor.exe" --server 127.0.0.1:43200 --settings "%~dp0monitor_loopback.env" --tab beam
timeout /t 6 /nobreak >nul
py -3 "%~dp0..\tools\fake_processor.py" --peers --duration 120 > "%~dp0loopback_fake.log" 2>&1
