@echo off
chcp 65001 >nul
echo ============================================
echo  PSE 后台同步守护进程 - Windows 计划任务安装
echo ============================================
echo.

REM 切换到项目根目录
cd /d "%~dp0"

REM 激活虚拟环境（如果存在）
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo [1/3] 首次全量同步（仅第一次需要，约 10-30 分钟）...
%PYTHON% -m backend.app.data_center.sync_daemon --full
echo.

echo [2/3] 安装每日自动同步计划任务（每天 16:00 执行）...
%PYTHON% -m backend.app.data_center.sync_daemon --install-scheduler --scheduler-time 16:00
echo.

echo [3/3] 查看同步状态...
%PYTHON% -m backend.app.data_center.sync_daemon --status
echo.
echo ============================================
echo 安装完成！
echo - 每日 16:00 自动增量同步当天缺失数据
echo - 主后端重启不影响同步任务
echo - 查询状态：GET /api/jobs/sync-status
echo ============================================
pause
