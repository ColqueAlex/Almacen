<#
  Registra (o elimina) en el Programador de tareas de Windows el job diario
  que genera las alertas de vencimiento (Regla 3 del Modulo de Notificaciones).

  Uso (desde la raiz del proyecto):
    powershell -ExecutionPolicy Bypass -File scripts\programar_alertas_vencimiento.ps1
    powershell -ExecutionPolicy Bypass -File scripts\programar_alertas_vencimiento.ps1 -Hora 07:30
    powershell -ExecutionPolicy Bypass -File scripts\programar_alertas_vencimiento.ps1 -Eliminar

  - Corre todos los dias a la hora indicada (default 08:00), con el usuario actual.
  - StartWhenAvailable: si la PC estaba apagada a esa hora, corre apenas se enciende.
  - Requiere que la base MySQL (docker compose up -d db) este levantada al ejecutarse.
  - Probar ya mismo:  Start-ScheduledTask -TaskName 'ElRefugio - Alertas de vencimiento'
  - Resultado:        logs\alertas_vencimiento.log
#>
param(
    [string]$Hora = '08:00',
    [switch]$Eliminar
)

$ErrorActionPreference = 'Stop'
$NombreTarea = 'ElRefugio - Alertas de vencimiento'

if ($Eliminar) {
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false
    Write-Host "Tarea '$NombreTarea' eliminada."
    return
}

$raiz = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot 'alertas_vencimiento.bat'
$python = (Get-Command python -ErrorAction Stop).Source

# cmd /c ""bat" "python.exe""  (comillas dobles externas por las rutas con espacios)
$argumentos = '/c ""{0}" "{1}""' -f $bat, $python
$accion = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $argumentos -WorkingDirectory $raiz
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparador -Settings $opciones `
    -Description 'El Refugio: genera notificaciones de lotes proximos a vencer (python manage.py alertas_vencimiento).' `
    -Force | Out-Null

Write-Host "Tarea '$NombreTarea' registrada: todos los dias a las $Hora."
Write-Host "Python: $python"
Write-Host "Log:    $(Join-Path $raiz 'logs\alertas_vencimiento.log')"
