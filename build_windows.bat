@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
 echo Install Python 3 and the Python launcher first.
 pause
 exit /b 1
)
py -m pip install psutil pyinstaller pillow customtkinter
if errorlevel 1 goto failed
py -m PyInstaller --noconfirm --clean --onefile --windowed --name BuddyConnect --icon "buddy_connect.ico" --add-data "buddy_connect.ico;." --add-data "buddy_connect_icon.png;." --collect-all customtkinter --hidden-import psutil --hidden-import buddy_updater buddy_connect.py
if errorlevel 1 goto failed
echo Built: %~dp0dist\BuddyConnect.exe
pause
exit /b 0
:failed
echo Build failed.
pause
exit /b 1
