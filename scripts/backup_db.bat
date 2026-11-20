@echo off
REM ==========================================================================
REM  Job diario - Backup completo de la base de datos MySQL.
REM  Ejecuta: python manage.py backup_db
REM  Lo invoca la tarea programada "ElRefugio - Backup diario"
REM  (ver scripts\programar_backup_db.ps1). Tambien se puede ejecutar a mano.
REM
REM  Parametro opcional %1: ruta completa a python.exe (si no, usa el del PATH).
REM  Backups:  backups\backup_<base>_<fecha>.zip (se conservan 7 dias)
REM  Registro: logs\auditoria.log (INFO/ERROR) y logs\backup_db.log (salida)
REM ==========================================================================
setlocal
cd /d "%~dp0.."

set "PY=%~1"
if "%PY%"=="" set "PY=python"
set "PYTHONIOENCODING=utf-8"

if not exist logs mkdir logs
echo ==== %date% %time% ==== >> logs\backup_db.log
"%PY%" manage.py backup_db >> logs\backup_db.log 2>&1
set "RESULTADO=%ERRORLEVEL%"
echo (codigo de salida: %RESULTADO%) >> logs\backup_db.log
exit /b %RESULTADO%
