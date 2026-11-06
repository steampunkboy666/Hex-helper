@echo off
rem Build HexHelper.exe (needs: py -3 -m pip install PySide6-Essentials pyinstaller pillow numpy)
cd /d "%~dp0"
py -3 tools\make_icon.py
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --uac-admin --name HexHelper --icon assets\icon.ico --exclude-module tkinter --exclude-module unittest --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf main.py
echo.
echo Done: dist\HexHelper.exe
pause
