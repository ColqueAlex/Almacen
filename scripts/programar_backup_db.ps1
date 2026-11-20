<#
  Registra (o elimina) en el Programador de tareas de Windows el backup diario
  de la base de datos (python manage.py backup_db).

  Uso (desde la raiz del proyecto):
    powershell -ExecutionPolicy Bypass -File scripts\programar_backup_db.ps1
    powershell -ExecutionPolicy Bypass -File scripts\programar_backup_db.ps1 -Hora 02:30
    powershell -ExecutionPolicy Bypass -File scripts\programar_backup_db.ps1 -Eliminar

  - Corre todos los dias a la hora indicada (default 03:00), con el usuario actual.
  - StartWhenAvailable: si la PC estaba apagada a esa hora, corre apenas se enciende.
  - Requiere que la base MySQL (docker compose up -d db) este levantada al ejecutarse.
  - Probar ya mismo:  Start-ScheduledTask -TaskName 'ElRefugio - Backup diario'
  - Resultado:        backups\*.zip, logs\auditoria.log y logs\backup_db.log
#>
param(
    [string]$Hora = '03:00',
    [switch]$Eliminar
)

$ErrorActionPreference = 'Stop'
$NombreTarea = 'ElRefugio - Backup diario'

if ($Eliminar) {
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false
    Write-Host "Tarea '$NombreTarea' eliminada."
    return
}

$raiz = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot 'backup_db.bat'
$python = (Get-Command python -ErrorAction Stop).Source

# cmd /c ""bat" "python.exe""  (comillas dobles externas por las rutas con espacios)
$argumentos = '/c ""{0}" "{1}""' -f $bat, $python
$accion = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $argumentos -WorkingDirectory $raiz
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45)

Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparador -Settings $opciones `
    -Description 'El Refugio: backup completo de MySQL comprimido en backups\ con rotacion de 7 dias (python manage.py backup_db).' `
    -Force | Out-Null

Write-Host "Tarea '$NombreTarea' registrada: todos los dias a las $Hora."
Write-Host "Python:  $python"
Write-Host "Backups: $(Join-Path $raiz 'backups')"
Write-Host "Log:     $(Join-Path $raiz 'logs\backup_db.log')"
