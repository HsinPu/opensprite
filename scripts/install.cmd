@echo off
REM OpenSprite installer for Windows CMD users.

echo.
echo  OpenSprite Installer
echo  Launching PowerShell installer...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.ps1)"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.ps1)"
    echo.
    pause
    exit /b 1
)
