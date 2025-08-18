@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
if not exist ".venv" (%PY% -m venv .venv)
call ".venv\Scripts\activate"
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist ".env" ( if exist ".env.example" ( copy /y ".env.example" ".env" >nul ) )
set FLASK_HOST=127.0.0.1
set FLASK_PORT=5000
python run.py
pause >nul
