@echo off
rem Igual que run.bat pero con consola visible para ver los logs si algo falla.
cd /d "%~dp0"
.venv\Scripts\python.exe app.py
pause
