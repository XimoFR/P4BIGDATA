@echo off
rem Lanzador para el Programador de tareas: ejecutar.cmd <fuente> [<fuente> ...]
rem Un log por primera fuente para que tareas simultaneas no choquen al escribir.
set RAIZ=%~dp0..
"%RAIZ%\.venv\Scripts\python.exe" "%~dp0recolector.py" %* >> "%RAIZ%\logs\cron_%1.log" 2>&1
