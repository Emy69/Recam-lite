@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Falta el entorno .venv
    echo Ejecuta primero:  py -3 -m venv .venv  y luego  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" app.py
