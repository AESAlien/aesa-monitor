@echo off
rem Environment check for aesa-monitor build/capture. Output: check_env.txt
cd /d "%~dp0"
(
echo ==== date
date /t
time /t
echo ==== python
where python
where py
py -3 --version
python --version
echo ==== pyinstaller
py -3 -m PyInstaller --version
python -m PyInstaller --version
echo ==== wireshark
dir "%ProgramFiles%\Wireshark\dumpcap.exe"
"%ProgramFiles%\Wireshark\dumpcap.exe" -v
"%ProgramFiles%\Wireshark\dumpcap.exe" -D
echo ==== npcap
sc query npcap
echo ==== adapters
powershell -NoProfile -Command "Get-NetAdapter | Format-Table -AutoSize Name,InterfaceDescription,Status,MacAddress,LinkSpeed | Out-String -Width 200"
echo ==== bridge
netsh bridge show adapter
echo ==== ipconfig
ipconfig /all
echo ==== arp
arp -a
echo ==== END
) > check_env.txt 2>&1
