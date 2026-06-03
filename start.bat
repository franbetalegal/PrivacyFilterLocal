@echo off
title Privacy Filter - Local
color 0A

echo ========================================
echo   Privacy Filter - Local
echo ========================================
echo.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo Run install.bat first to set up the application.
    echo.
    pause
    exit /b 1
)

echo Starting web server...
echo Open http://localhost:7860 in your browser
echo (if 7860 is busy, it will try the next port)
echo Press Ctrl+C to stop
echo.

.venv\Scripts\python.exe app_local.py

pause
