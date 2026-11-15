@echo off
REM ==========================================================================
REM  Job diario - Regla 3 (vencimientos) del Modulo de Notificaciones.
REM  Ejecuta: python manage.py alertas_vencimiento
REM  Lo invoca la tarea programada "ElRefugio - Alertas de vencimiento"
REM  (ver scripts\programar_alertas_vencimiento.ps1). Tambien se puede
REM  ejecutar a mano con doble clic.
REM
REM  Parametro opcional %1: ruta completa a python.exe (si no, usa el del PATH).
REM  La salida se agrega a logs\alertas_vencimiento.log
REM ==========================================================================
setlocal
cd /d "%~dp0.."

set "PY=%~1"
if "%PY%"=="" set "PY=python"
set "PYTHONIOENCODING=utf-8"

if not exist logs mkdir logs
echo ==== %date% %time% ==== >> logs\alertas_vencimiento.log
"%PY%" manage.py alertas_vencimiento >> logs\alertas_vencimiento.log 2>&1
set "RESULTADO=%ERRORLEVEL%"
echo (codigo de salida: %RESULTADO%) >> logs\alertas_vencimiento.log
exit /b %RESULTADO%
