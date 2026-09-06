@echo off
cd /d "%~dp0"

echo ========================================
echo   Tongdaxin Sync Helper (托盘后台)
echo   config: tdx_bridge_config.json
echo ========================================
echo   [1] 后台启动（托盘）
echo   [2] 修改设置
echo   [3] 记事本打开配置
echo   [4] 前台调试（有黑窗口）
echo ========================================
set /p choice=请选择 1/2/3/4 默认1: 

set "EXE=%~dp0tdx_bridge_agent.exe"
set "PY=%~dp0tdx_bridge_agent.py"

if not exist "%EXE%" if not exist "%PY%" (
  echo 未找到 tdx_bridge_agent.exe 或 .py
  pause
  exit /b 1
)

if "%choice%"=="2" goto do_setup
if "%choice%"=="3" goto do_edit
if "%choice%"=="4" goto do_console
goto do_run

:do_setup
if exist "%EXE%" ("%EXE%" --setup) else (python "%PY%" --setup)
goto do_end

:do_edit
if not exist "%~dp0tdx_bridge_config.json" goto do_setup
notepad "%~dp0tdx_bridge_config.json"
goto do_end

:do_console
if exist "%EXE%" (
  "%EXE%" --console
) else (
  python "%PY%"
)
goto do_end

:do_run
if exist "%EXE%" (
  start "" "%EXE%"
  echo 已在后台启动，请在任务栏右下角托盘查看蓝色 T 图标。
) else (
  python "%PY%"
)

:do_end
pause
