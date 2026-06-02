@echo off
title Privacy Filter - Local
color 0A

echo ========================================
echo   Privacy Filter - Local
echo ========================================
echo.

cd /d "%~dp0"

echo Iniciando servidor web...
echo Abre http://localhost:7860 en tu navegador
echo Presiona Ctrl+C para detener
echo.

python app_local.py

pause
