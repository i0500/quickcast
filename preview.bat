@echo off
REM QuickCast - Design Preview launcher
REM Runs the real app via venv python with QUICKCAST_FORCE_TUTORIAL=1 so
REM the tutorial overlay always shows — preview = real production
REM behaviour minus the EXE re-build step.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found.
    echo Set up venv at .venv first.
    pause
    exit /b 1
)

set QUICKCAST_FORCE_TUTORIAL=1
.venv\Scripts\python.exe -m quickcast

if errorlevel 1 (
    echo.
    echo [preview exited with error %errorlevel%]
    pause
)
