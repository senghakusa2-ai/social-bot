@echo off
title Stop Telegram Bot
echo Stopping Social Downloader Telegram Bot...
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Done!
timeout /t 2 >nul
