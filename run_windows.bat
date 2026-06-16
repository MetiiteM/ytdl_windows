@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python ytdl_windows.py
pause
