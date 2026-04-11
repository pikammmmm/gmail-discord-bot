@echo off
title Gmail -^> Discord Bot
cd /d "%~dp0"
".venv\Scripts\python.exe" gmail_to_discord.py
echo.
echo [bot stopped - press any key to close this window]
pause > nul
