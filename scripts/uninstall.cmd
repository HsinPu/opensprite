@echo off
REM OpenSprite uninstaller for Windows CMD users.

echo.
echo  OpenSprite Uninstaller
echo  Launching PowerShell uninstaller...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/uninstall.ps1)"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Uninstall failed. Try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/uninstall.ps1)"
    echo.
    pause
    exit /b 1
)
