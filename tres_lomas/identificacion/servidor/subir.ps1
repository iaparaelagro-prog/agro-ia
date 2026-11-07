# Sube campo.py y todos sus datos al servidor e instala todo (HTTPS + contrasena).
# Uso, desde PowerShell en la carpeta desfile_tres_lomas:
#     powershell -ExecutionPolicy Bypass -File servidor\subir.ps1 -IP 123.45.67.89
# Tarda: armar el paquete ~2 min, subir ~6,5 GB (depende de tu internet: con 10 Mbps de subida, ~1,5 h),
# instalar ~10 min. Si se corta la subida, volve a correrlo.
param([Parameter(Mandatory = $true)][string]$IP)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$abierto = Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match "campo\.py" }
if ($abierto) { Write-Host "Cerra campo.py (Ctrl+C) antes de subir, para que los datos esten completos."; exit 1 }

$tar = Join-Path $env:TEMP "vacas.tar"
Write-Host "1/4 Armando el paquete (unos minutos)..."
& python servidor/empaquetar.py armar $tar campo.py identificador.py interfaz_campo.html sam2.1_t.pt yolo11s.pt campo identificacion "Asignaciones (Verificado)" Asignaciones_sin_fondo frames_desfile_camaras
if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo armar el paquete."; exit 1 }
$gb = [math]::Round((Get-Item $tar).Length / 1GB, 1)

Write-Host "2/4 Subiendo $gb GB al servidor (esto es lo que mas tarda)..."
& scp $tar "root@${IP}:/root/vacas.tar"
if ($LASTEXITCODE -ne 0) { Write-Host "Se corto la subida. Volve a correr este script."; exit 1 }

Write-Host "3/4 Subiendo el instalador..."
& ssh "root@$IP" "mkdir -p /root/servidor"
& scp servidor/instalar.sh "root@${IP}:/root/servidor/instalar.sh"

Write-Host "4/4 Instalando en el servidor (te va a pedir que elijas la contrasena del equipo)..."
& ssh -t "root@$IP" "bash /root/servidor/instalar.sh"
Remove-Item $tar -ErrorAction SilentlyContinue
Write-Host "Desde ahora trabajen SOLO en el servidor: no abras campo.py en tu compu hasta bajar los resultados (bajar.ps1)."
