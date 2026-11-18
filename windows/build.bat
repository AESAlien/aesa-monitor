@echo off
rem aesa-monitor Windows exe build (PyInstaller, onefile, no console window)
rem Output: windows\dist\aesa-monitor.exe
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

echo [1/3] Python
%PY% --version || goto :error

echo [2/3] PyInstaller install/update
%PY% -m pip install --upgrade pyinstaller || goto :error

echo [3/3] Build
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name aesa-monitor ^
  --paths "%~dp0.." ^
  --collect-submodules aesa_monitor ^
  --distpath "%~dp0dist" --workpath "%~dp0build" --specpath "%~dp0build" ^
  "%~dp0aesa_monitor_win.py" || goto :error

echo.
echo Done: %~dp0dist\aesa-monitor.exe
echo monitor.env is created next to the exe on first run.
exit /b 0

:error
echo.
echo BUILD FAILED (code %errorlevel%)
exit /b 1
