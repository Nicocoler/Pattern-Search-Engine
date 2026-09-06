@echo off
cd /d "%~dp0"

echo ========================================
echo   Tongdaxin Sync Helper
echo   config: tdx_bridge_config.json
echo ========================================
echo   [1] 开始同步
echo   [2] 修改设置
echo   [3] 记事本打开配置
echo ========================================
set /p choice=请选择 1/2/3 默认1: 

set "EXE=%~dp0tdx_bridge_agent.exe"
set "PY=%~dp0tdx_bridge_agent.py"

if not exist "%EXE%" if not exist "%PY%" (
  echo 未找到 tdx_bridge_agent.exe 或 .py
  pause
  exit /b 1
)

if "%choice%"=="2" goto do_setup
if "%choice%"=="3" goto do_edit
goto do_run

:do_setup
if exist "%EXE%" ("%EXE%" --setup) else (python "%PY%" --setup)
goto do_end

:do_edit
if not exist "%~dp0tdx_bridge_config.json" goto do_setup
notepad "%~dp0tdx_bridge_config.json"
goto do_end

:do_run
if exist "%EXE%" ("%EXE%") else (python "%PY%")

:do_end
pause
