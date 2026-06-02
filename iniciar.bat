@echo off
title Privacy Filter - Local
color 0A

echo ========================================
echo   Privacy Filter - Local
echo ========================================
echo.

cd /d "%~dp0"

echo Starting web server...
echo Open http://localhost:7860 in your browser
echo Press Ctrl+C to stop
echo.

python app_local.py

pause
