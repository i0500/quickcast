@echo off
REM Build single-file Windows executable for QuickCast.

setlocal enabledelayedexpansion

REM Move to project root (parent of this script's directory)
pushd "%~dp0.."

if not exist .venv\Scripts\python.exe (
    echo [build] creating venv...
    python -m venv .venv
    if errorlevel 1 goto :error
)

call .venv\Scripts\activate.bat
if errorlevel 1 goto :error

echo [build] installing dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r quickcast\requirements.txt
if errorlevel 1 goto :error
python -m pip install pyinstaller
if errorlevel 1 goto :error

echo [build] running PyInstaller...
REM Distpath is fixed to F:\린w\dist for consistency.
pyinstaller --noconfirm --onefile --windowed --uac-admin ^
    --name quickcast ^
    --distpath dist ^
    --icon "quickcast\data\icon.ico" ^
    --version-file "quickcast\data\version.txt" ^
    --splash "quickcast\data\splash.png" ^
    --add-data "quickcast\data\targets;quickcast\data\targets" ^
    --add-data "quickcast\data\icon.ico;quickcast\data" ^
    --collect-submodules PySide6 ^
    --hidden-import cv2 ^
    -p . ^
    quickcast\__main__.py
if errorlevel 1 goto :error

echo.
echo [build] OK -^> dist\quickcast.exe
popd
endlocal
exit /b 0

:error
echo [build] FAILED
popd
endlocal
exit /b 1
