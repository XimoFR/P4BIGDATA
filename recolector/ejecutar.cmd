@echo off
rem Lanzador para el Programador de tareas: ejecutar.cmd <fuente> [<fuente> ...]
set RAIZ=%~dp0..
"%RAIZ%\.venv\Scripts\python.exe" "%~dp0recolector.py" %* >> "%RAIZ%\logs\cron.log" 2>&1
