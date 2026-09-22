@echo off
REM Crea una tarea de Windows que ejecuta la sincronizacion todos los dias a las 08:00
cd /d "%~dp0"
schtasks /Create /F /SC DAILY /ST 08:00 /TN "Bidcom Sync" /TR "cmd /c cd /d \"%~dp0\" && python bidcom_sync.py"
echo Tarea "Bidcom Sync" creada (diaria 08:00). Para borrarla: schtasks /Delete /TN "Bidcom Sync" /F
pause
