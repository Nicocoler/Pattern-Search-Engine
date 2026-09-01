@echo off
chcp 65001 >nul
title PSE 当日行情增量同步
echo ============================================
echo     PSE 当日行情增量同步脚本
echo ============================================
echo.

REM 切换到项目根目录（批处理文件所在位置）
cd /d "%~dp0"

REM 用 PowerShell 生成纯数字日期 YYYYMMDD（避免中文星期/斜杠污染文件名）
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set LOG_DATE=%%i
if not defined LOG_DATE set LOG_DATE=20260101

REM 强制 Python 以 UTF-8 输出，避免中文/表情在管道里变成乱码（ 或 \Uxxxxxxxx）
set PYTHONIOENCODING=utf-8

REM 判断虚拟环境
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

REM 确保 logs 目录存在
if not exist "logs" mkdir logs

REM 构建日志文件路径
set LOG_FILE=logs\sync_today_%LOG_DATE%.log

REM 同步结束后输出状态的临时状态文件
set STATUS_FILE=logs\sync_status_%LOG_DATE%.txt

echo 日志文件：%LOG_FILE%
echo 开始同步时间：%date% %time%
echo 正在执行增量同步，请稍候（进度实时显示在下方）...
echo ============================================

REM 1) set PYTHONIOENCODING=utf-8     → Python 输出 UTF-8（中文、emoji 不会再变成乱码）
REM 2) cmd /c '... 2>&1'              → 在 cmd 层合并 stderr 到 stdout，不再经过 PowerShell 的 ErrorRecord，
REM 3) [Console]::OutputEncoding=UTF8  → PowerShell 以 UTF-8 解码管道输出，和 Python 的 UTF-8 对齐
REM 4) Tee-Object                      → 输出同时写日志文件 + 实时回显到屏幕
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; cmd /c '%PYTHON% -m backend.app.data_center.sync_daemon 2>&1' | Tee-Object -FilePath '%LOG_FILE%'; exit $LASTEXITCODE"
set EXIT_CODE=%ERRORLEVEL%

echo ============================================
if %EXIT_CODE% EQU 0 (
    echo 同步完成！正在读取 data_sync_status 最新状态...
    echo.
    powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; cmd /c '%PYTHON% -m backend.app.data_center.sync_daemon --status 2>&1' | Tee-Object -FilePath '%STATUS_FILE%'"
    echo.
    echo 状态已保存至：%STATUS_FILE%
    echo 日志已保存至：%LOG_FILE%
) else (
    echo 同步异常，退出码：%EXIT_CODE%，请查看日志：%LOG_FILE%
)
echo ============================================
pause