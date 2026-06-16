@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller --onefile --noconsole --name YTDL-Windows ytdl_windows.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo Done. EXE is here: dist\YTDL-Windows.exe
echo Make sure ffmpeg.exe is available in PATH or next to the EXE.
pause
