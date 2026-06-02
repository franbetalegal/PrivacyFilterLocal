@echo off
title Privacy Filter - Uninstaller
color 0C

echo ========================================
echo   Privacy Filter - Uninstaller
echo ========================================
echo.
echo This will remove Privacy Filter from:
echo   C:\privacy-filter
echo.
echo The cached model at %%USERPROFILE%%\.opf\privacy_filter
echo will NOT be removed unless you choose to remove it.
echo.

set /p CONFIRM="Are you sure you want to uninstall? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Uninstall cancelled.
    pause
    exit /b 0
)

echo.
echo Removing application files...

if exist "C:\privacy-filter" (
    rmdir /s /q "C:\privacy-filter"
    echo [OK] Application removed from C:\privacy-filter
) else (
    echo [!] Application directory not found
)

echo.
set /p REMOVE_MODEL="Do you also want to remove the cached model? (Y/N): "
if /i "%REMOVE_MODEL%"=="Y" (
    if exist "%USERPROFILE%\.opf\privacy_filter" (
        rmdir /s /q "%USERPROFILE%\.opf\privacy_filter"
        echo [OK] Model cache removed
    ) else (
        echo [!] Model cache not found
    )
)

echo.
echo ========================================
echo   Uninstall complete
echo ========================================
echo.
pause
