@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    echo First, follow the setup instructions in README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py %*
pause
