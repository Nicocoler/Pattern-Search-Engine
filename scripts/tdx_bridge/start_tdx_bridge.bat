@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   通达信同步助手
echo   配置文件: tdx_bridge_config.json
echo ========================================
echo   [1] 开始同步
echo   [2] 修改设置（服务器地址 / 口令等）
echo   [3] 用记事本打开配置文件
echo ========================================
set /p choice=请选择 (1/2/3，默认1): 

set "EXE=%~dp0tdx_bridge_agent.exe"
set "PY=%~dp0tdx_bridge_agent.py"

if exist "%EXE%" (
  set "RUN=%EXE%"
) else if exist "%PY%" (
  set "RUN=python "%PY%""
) else (
  echo 未找到 tdx_bridge_agent.exe / .py
  pause
  exit /b 1
)

if "%choice%"=="2" (
  if exist "%EXE%" (
    "%EXE%" --setup
  ) else (
    python "%PY%" --setup
  )
  goto end
)
if "%choice%"=="3" (
  if not exist "%~dp0tdx_bridge_config.json" (
    if exist "%EXE%" (
      "%EXE%" --setup
    ) else (
      python "%PY%" --setup
    )
  ) else (
    notepad "%~dp0tdx_bridge_config.json"
  )
  goto end
)

if exist "%EXE%" (
  "%EXE%"
) else (
  python "%PY%"
)

:end
pause
