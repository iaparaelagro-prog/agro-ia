# Sube los cambios de codigo (campo.py, identificador.py, interfaz_campo.html) y, si existe, el modelo
# entrenado dinov2_vacas.pt. Despues reinicia campo.py.
# Lo marcado no se pierde. La pagina queda unos minutos sin andar mientras carga los modelos.
# OJO: si cambia el modelo de la huella (dinov2_vacas.pt nuevo o distinto), al arrancar recalcula las
# huellas de todo lo procesado y la pagina tarda 1 a 2 horas en volver. Ver el avance con:
#     ssh root@IP "journalctl -u campo -f"
# Uso:  powershell -ExecutionPolicy Bypass -File servidor\actualizar.ps1 -IP 123.45.67.89
param([Parameter(Mandatory = $true)][string]$IP)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$archivos = @("campo.py", "identificador.py", "interfaz_campo.html")
if (Test-Path "dinov2_vacas.pt") {
    $archivos += "dinov2_vacas.pt"
    Write-Host "Se sube tambien el modelo entrenado dinov2_vacas.pt"
}
& scp @archivos "root@${IP}:/home/vacas/desfile_tres_lomas/"
if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo subir."; exit 1 }
& ssh "root@$IP" "chown vacas:vacas /home/vacas/desfile_tres_lomas/*.py /home/vacas/desfile_tres_lomas/*.html /home/vacas/desfile_tres_lomas/*.pt && systemctl restart campo"
Write-Host "Listo. campo.py se esta reiniciando."
Write-Host "Para ver el avance (Ctrl+C para salir, no frena nada):  ssh root@$IP `"journalctl -u campo -f`""
