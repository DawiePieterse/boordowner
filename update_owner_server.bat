@echo off
setlocal EnableDelayedExpansion
:: Boord Owner - double-click to pull the latest code, install any new
:: dependencies, bring the Owner database up to date, and restart the
:: service so the update takes effect.
::
:: NOTE: this does a plain "git pull" of the current branch. Unlike the main
:: Boord update path there is no GPG-signed-tag check here yet - if that
:: matters for your install, update by hand from a tag you have verified.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo This needs administrator rights to restart the server - requesting them now...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
set "TASK=Boord Owner Server"
set "VENV_PY=%~dp0backend\.venv\Scripts\python.exe"

echo.
echo ==^> Fetching the latest code...
git pull --ff-only
if %errorLevel% neq 0 (
    echo.
    echo git pull failed. The server has NOT been changed or restarted.
    pause
    exit /b 1
)

echo.
echo ==^> Installing any new dependencies...
"%~dp0backend\.venv\Scripts\pip.exe" install --quiet --disable-pip-version-check -r "%~dp0backend\requirements.txt"

echo.
echo ==^> Bringing the Owner database up to date...
:: A cheap pre-change snapshot, matching Boord's habit.
if not exist "%~dp0data\backups" mkdir "%~dp0data\backups"
if exist "%~dp0data\owner.db" copy /y "%~dp0data\owner.db" "%~dp0data\backups\owner-before-update.db" >nul
cd /d "%~dp0backend"
"%VENV_PY%" -c "from db import init_owner_db; init_owner_db()"
cd /d "%~dp0"

echo.
echo ==^> Restarting the server...
cmd /c "schtasks /end /tn ""%TASK%"" >nul 2>&1"
timeout /t 2 /nobreak >nul
schtasks /run /tn "%TASK%" >nul

echo.
echo ==^> Checking it came back up...
set OK=
for /l %%i in (1,1,20) do (
    if not defined OK (
        timeout /t 1 /nobreak >nul
        powershell -NoProfile -Command "try { if ((Invoke-WebRequest -Uri 'http://localhost:8010/' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { exit 0 } } catch {}; exit 1" && set OK=1
    )
)
if defined OK ( echo     Server is back up on port 8010. ) else ( echo     Server did not answer in 20s - run start_owner_server.bat in a window to see why. )
echo.
pause
