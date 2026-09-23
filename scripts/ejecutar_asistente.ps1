<#
.SYNOPSIS
  Ejecuta el asistente y lo reinicia si termina con error (por ejemplo, tras un corte de red).

.DESCRIPTION
  Es un supervisor mínimo para usarlo en tu PC. Ctrl+C detiene todo. Si el asistente termina con
  código 0 (apagado normal) no se reinicia. Gracias al bloqueo de instancia única, un reinicio
  nunca puede duplicar respuestas: si hubiera otra instancia activa, la nueva espera su turno.

.EXAMPLE
  .\scripts\ejecutar_asistente.ps1
#>
param(
    # Ruta absoluta, derivada de dónde vive este script (no del directorio de trabajo de quien lo
    # invoca): una ruta relativa que no resuelve puede terminar silenciosamente en el Python del
    # sistema si algo relanza este script desde otro cwd, y ahí no hay bloqueo de instancia que
    # valga porque igual sería un proceso nuevo con las dependencias correctas — pero un Python
    # sin el venv puede fallar de formas menos claras. Mejor fallar fuerte si no existe.
    [string]$Python = (Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"),
    [string[]]$Argumentos = @("-m", "asistente"),
    [int]$EsperaSegundos = 15,
    [int]$EsperaMaximaSegundos = 300,
    [int]$MaxReinicios = 0  # 0 = sin límite
)

if (-not (Test-Path $Python)) {
    Write-Error "No existe el Python del venv en '$Python'. ¿Falta crear el entorno (.venv)?"
    exit 1
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = Join-Path $PSScriptRoot "..\src"
$reinicios = 0
$espera = $EsperaSegundos

while ($true) {
    $inicio = Get-Date
    & $Python @Argumentos
    $codigo = $LASTEXITCODE

    if ($codigo -eq 0) {
        Write-Host "El asistente terminó con normalidad (código 0). No se reinicia."
        break
    }

    # Si funcionó un buen rato antes de caer, la espera vuelve a empezar desde cero.
    if (((Get-Date) - $inicio).TotalSeconds -gt 300) { $espera = $EsperaSegundos }

    $reinicios++
    if ($MaxReinicios -gt 0 -and $reinicios -ge $MaxReinicios) {
        Write-Host "El asistente terminó con código $codigo y se alcanzó el máximo de reinicios ($MaxReinicios)."
        exit $codigo
    }
    Write-Host "El asistente terminó con código $codigo. Reinicio #$reinicios en $espera s (Ctrl+C para cancelar)."
    Start-Sleep -Seconds $espera
    $espera = [Math]::Min($espera * 2, $EsperaMaximaSegundos)  # espera creciente: no martillar si algo falla siempre
}
