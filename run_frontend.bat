@echo off
:: =============================================================================
:: Pattern Search Engine (PSE) - 前端开发服务器一键拉起大印 (run_frontend.bat)
:: =============================================================================
chcp 65001 >nul
title ⚡ PSE 前端开发服务器启动大印 ⚡
echo =======================================================================
echo     ⚡ 正在准备拉起形态选股前端 React 开发服务器...
echo =======================================================================

echo 🔍 第一步：正在探测 5173 端口占用情况...
set "TARGET_PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5173 ^| findstr LISTENING') do (
    set "TARGET_PID=%%a"
)

if defined TARGET_PID (
    echo 🚨 发现已有前端进程占用 5173 端口，PID: %TARGET_PID%
    echo 🛠️ 正在释放该端口，确保无缝重启动...
    taskkill /F /PID %TARGET_PID% >nul 2>&1
    echo ✅ 旧版前端开发服务器已退位！
) else (
    echo 🍃 5173 端口一片空旷，准备直接点火起飞。
)

echo.
echo 🚀 第二步：正在启动 Vite React 开发服务器并自动弹出浏览器...
echo =======================================================================
echo.

:: 自动启动默认浏览器直达研盘大盘大厅
start http://localhost:5173

:: 进入前端目录并启动开发服务器
cd frontend
npm run dev

if %errorlevel% neq 0 (
    echo.
    echo ❌ 糟糕！前端启动失败，请检查是否在项目根目录运行本脚本，或 Node 依赖是否正常。
    pause
)
