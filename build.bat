@echo off
setlocal
cd /d "%~dp0"
rem Resolve Python before isolating DLL discovery from other installed applications.
if defined HEXHELPER_PYTHON goto build
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)"') do set "HEXHELPER_PYTHON=%%P"
:build
if not exist "%HEXHELPER_PYTHON%" exit /b 1
for %%P in ("%HEXHELPER_PYTHON%") do set "PYDIR=%%~dpP"
set "PATH=%PYDIR%;%PYDIR%Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "PYTHONPATH="
set "PYTHONHOME="
"%HEXHELPER_PYTHON%" tools\make_icon.py || exit /b 1
if not defined HEXHELPER_DIST set "HEXHELPER_DIST=dist"
"%HEXHELPER_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --noconsole --uac-admin --distpath "%HEXHELPER_DIST%" --add-data "assets/tessdata;assets/tessdata" --additional-hooks-dir tools/hooks --copy-metadata tesserocr --name HexHelper --icon assets\icon.ico --exclude-module tkinter --exclude-module unittest --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf main.py
exit /b %errorlevel%
