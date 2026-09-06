@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo Building tdx_bridge_agent.exe ...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --name tdx_bridge_agent ^
  --distpath dist ^
  --workpath build ^
  --specpath build ^
  tdx_bridge_agent.py

if errorlevel 1 (
  echo Build failed. Need Python +: pip install pyinstaller
  exit /b 1
)

copy /Y tdx_bridge_config.example.json dist\tdx_bridge_config.example.json >nul
copy /Y start_tdx_bridge.bat dist\start_tdx_bridge.bat >nul
copy /Y dist\tdx_bridge_agent.exe .\tdx_bridge_agent.exe >nul

echo.
echo OK: %~dp0tdx_bridge_agent.exe
echo Copy: tdx_bridge_agent.exe + start_tdx_bridge.bat (+ config) to other PCs.
