@echo off
:: =============================================================================
:: Pattern Search Engine (PSE) - 后端微服务自动重置启动大印 (restart_backend.bat)
:: =============================================================================
chcp 65001 >nul
title ⚡ PSE 后端微服务重启大印 ⚡
echo =======================================================================
echo     ⚡ 正在准备重启形态选股后端 FastAPI 核心微服务...
echo =======================================================================

echo 🔍 第一步：正在探测 8000 端口占用情况...
set "TARGET_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    set "TARGET_PID=%%a"
)

if defined TARGET_PID (
    echo 🚨 发现已有后端进程占用 8000 端口，PID: %TARGET_PID%
    echo 🛠️ 正在强制绝杀旧版后端服务进程...
    taskkill /F /PID %TARGET_PID% >nul 2>&1
    echo ✅ 旧版服务已彻底退位！
) else (
    echo 🍃 8000 端口未被占用，准备直接起飞。
)

echo.
echo 🚀 第二步：正在暖机并重新启动 FastAPI 微服务...
echo 📅 日志将同步记录在控制台及 logs/backend_app.log 中。
echo =======================================================================
echo.

:: 启动后端服务
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

if %errorlevel% neq 0 (
    echo.
    echo ❌ 糟糕！服务启动失败，请检查是否在项目根目录运行本脚本，或 .venv 是否损坏。
    pause
)
