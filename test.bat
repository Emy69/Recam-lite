@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Falta el entorno .venv
    echo Ejecuta primero:  py -3 -m venv .venv  y luego  .venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pytest %*
