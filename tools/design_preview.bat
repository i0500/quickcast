@echo off
rem QuickCast Design Preview launcher.
rem Sets QUICKCAST_FORCE_TUTORIAL so the tutorial always shows regardless
rem of saved state, then runs the app via the project venv.
cd /d "%~dp0\.."
set QUICKCAST_FORCE_TUTORIAL=1
"%~dp0\..\.venv\Scripts\python.exe" -m quickcast
if errorlevel 1 pause
