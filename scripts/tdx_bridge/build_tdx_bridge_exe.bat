@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Installing build deps (pyinstaller / pystray / pillow) ...
python -m pip install -q pyinstaller pystray pillow
if errorlevel 1 (
  echo pip install failed.
  exit /b 1
)

echo Building tdx_bridge_agent.exe (tray / no console) ...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --noconsole ^
  --name tdx_bridge_agent ^
  --hidden-import pystray._win32 ^
  --hidden-import PIL._tkinter_finder ^
  --collect-all pystray ^
  --distpath dist ^
  --workpath build ^
  --specpath build ^
  tdx_bridge_agent.py

if errorlevel 1 (
  echo Build failed. Need Python +: pip install pyinstaller pystray pillow
  exit /b 1
)

copy /Y tdx_bridge_config.example.json dist\tdx_bridge_config.example.json >nul
copy /Y start_tdx_bridge.bat dist\start_tdx_bridge.bat >nul
copy /Y dist\tdx_bridge_agent.exe .\tdx_bridge_agent.exe >nul

echo.
echo OK: %~dp0tdx_bridge_agent.exe
echo Double-click = tray background. Right-click tray icon: config / log / exit
echo Debug console: tdx_bridge_agent.exe --console
echo Setup:         tdx_bridge_agent.exe --setup
echo Copy: tdx_bridge_agent.exe + start_tdx_bridge.bat (+ config) to other PCs.
