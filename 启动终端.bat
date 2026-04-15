@echo off
chcp 65001 >nul 2>&1
title 贵金属监控终端 - 启动中...
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║        贵金属量化监控终端  v0.9          ║
echo  ║     XAUUSD / XAGUSD / EURUSD / USDJPY   ║
echo  ╚══════════════════════════════════════════╝
echo.

:: 检查 Python 是否可用
where python >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未找到 Python，请先安装 Python 3.10+
    echo  下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 启动主程序（带 launcher 参数，显示启动画面）
python launcher.py
if errorlevel 1 (
    echo.
    echo  [提示] 程序异常退出，错误日志已保存到 error_log.txt
    echo  如需帮助，请查看上方错误信息。
    pause
)
