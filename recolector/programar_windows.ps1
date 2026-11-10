# Equivalente al crontab para el Programador de tareas de Windows (solo para probar en local).
# Uso (PowerShell):  .\programar_windows.ps1            -> crea las tareas
#                    .\programar_windows.ps1 -Quitar    -> las elimina
param([switch]$Quitar)

$raiz = Split-Path -Parent $PSScriptRoot
$lanzador = Join-Path $PSScriptRoot "ejecutar.cmd"

# nombre, fuentes, argumentos de schtasks para la frecuencia
$tareas = @(
    @("P4-estado",  "estado",     @("/SC", "MINUTE", "/MO", "5")),
    @("P4-tramos",  "tramos",     @("/SC", "MINUTE", "/MO", "15")),
    @("P4-espiras", "espiras",    @("/SC", "HOURLY", "/MO", "1")),
    @("P4-aire",    "aire meteo", @("/SC", "HOURLY", "/MO", "1")),
    @("P4-aemet",   "aemet",      @("/SC", "DAILY", "/ST", "06:30"))
)

foreach ($t in $tareas) {
    $nombre, $fuentes, $frecuencia = $t
    if ($Quitar) {
        schtasks /Delete /TN $nombre /F | Out-Null
        continue
    }
    # /TR admite como máximo 261 caracteres: por eso se usa un lanzador corto
    $cmd = "`"$lanzador`" $fuentes"
    schtasks /Create /TN $nombre /TR $cmd @frecuencia /F | Out-Null
    Write-Host "Creada $nombre ($fuentes)"
}
