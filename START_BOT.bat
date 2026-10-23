@echo off
title Social Downloader Telegram Bot
cd /d "%~dp0"

echo ========================================================
echo        ⚡ Universal Social Downloader Telegram Bot
echo ========================================================
echo.

python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)

python -c "import telegram" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Installing required dependencies...
    pip install -r requirements.txt
)

echo [INFO] Starting Bot...
python bot.py
pause
