# Baja del servidor todo lo marcado (carpeta campo), la cache (identificacion) y las fotos por vaca
# (recorridas_campo). Antes guarda una copia de tus carpetas locales con la fecha.
# Para tener una copia completa, frena campo.py unos minutos: avisale al equipo antes.
# Uso:  powershell -ExecutionPolicy Bypass -File servidor\bajar.ps1 -IP 123.45.67.89
param([Parameter(Mandatory = $true)][string]$IP)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "1/3 Frenando campo.py en el servidor y armando el paquete..."
& ssh "root@$IP" "systemctl stop campo; cd /home/vacas/desfile_tres_lomas && tar -cf /root/resultado.tar campo identificacion recorridas_campo; systemctl start campo"
if ($LASTEXITCODE -ne 0) { Write-Host "Algo fallo en el servidor."; exit 1 }

Write-Host "2/3 Bajando..."
$tar = Join-Path $env:TEMP "resultado.tar"
& scp "root@${IP}:/root/resultado.tar" $tar
if ($LASTEXITCODE -ne 0) { Write-Host "Se corto la bajada. Volve a correr este script."; exit 1 }

Write-Host "3/3 Guardando copia de lo local y reemplazando..."
$sello = Get-Date -Format "yyyyMMdd_HHmm"
foreach ($c in "campo", "identificacion", "recorridas_campo") {
    if (Test-Path $c) { Rename-Item $c "${c}_antes_servidor_$sello" }
}
& python servidor/empaquetar.py abrir $tar
if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo abrir el paquete; quedo en $tar"; exit 1 }
Remove-Item $tar
Write-Host "Listo. Tus carpetas anteriores quedaron como *_antes_servidor_$sello."
