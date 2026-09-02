@echo off
:: Boord Owner - double-click to install the Owner service on this PC.
:: It runs BESIDE the main Boord server (its own port, its own auto-start
:: task) and reads Boord's database read-only. Install Boord first.
::
:: "-ExecutionPolicy Bypass" is scoped to this one invocation - it changes
:: no system-wide setting; Windows blocks local PowerShell scripts by default.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This installer needs administrator rights - requesting them now...
    echo If Windows shows a User Account Control prompt, click "Yes".
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
