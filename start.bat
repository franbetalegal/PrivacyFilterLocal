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

if not exist "frontend\dist\index.html" (
    echo [ERROR] Web frontend not built.
    echo Run install.bat first ^(it builds the React interface^).
    echo.
    pause
    exit /b 1
)

echo Starting web server...
echo Open http://localhost:7860 in your browser
echo Press Ctrl+C to stop
echo.

.venv\Scripts\python.exe -m uvicorn server.main:app --host 0.0.0.0 --port 7860

pause
