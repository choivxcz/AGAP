@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python download_and_prepare.py
pause
