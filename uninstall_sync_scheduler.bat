@echo off
chcp 65001 >nul
echo ============================================
echo  PSE 后台同步守护进程 - 卸载计划任务
echo ============================================
echo.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

%PYTHON% -m backend.app.data_center.sync_daemon --uninstall-scheduler
echo.
echo 计划任务已卸载。
pause
