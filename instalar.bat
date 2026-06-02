@echo off
echo ========================================
echo   OpenAI Privacy Filter - Installer
echo ========================================
echo.
echo Running installer...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"

pause
