@echo off
rem Atajo para la versión sin GUI (headless). Ejemplos:
rem   recordbate-cli run
rem   recordbate-cli add https://chaturbate.com/usuario
rem   recordbate-cli list
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Falta el entorno .venv. Ejecuta:  py -3 -m venv .venv  y  .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)
".venv\Scripts\python.exe" -m recordbate.cli %*
