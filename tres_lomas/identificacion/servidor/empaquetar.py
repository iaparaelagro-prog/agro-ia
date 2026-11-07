"""
empaquetar.py
Arma o abre los paquetes .tar que van y vuelven del servidor. Lo usan subir.ps1 y bajar.ps1.
(El tar.exe de Windows rompe los nombres con tilde, como bib_LADOA_Último...; este no.)

    python servidor/empaquetar.py armar  SALIDA.tar  archivo_o_carpeta ...
    python servidor/empaquetar.py abrir  PAQUETE.tar
"""

import sys
import tarfile
from pathlib import Path


def armar(salida, rutas):
    n = 0
    with tarfile.open(salida, "w", format=tarfile.PAX_FORMAT, encoding="utf-8") as t:
        for r in map(Path, rutas):
            if not r.exists():
                print(f"  (no existe, se saltea: {r})")
                continue
            archivos = [r] if r.is_file() else sorted(f for f in r.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
            for f in archivos:
                t.add(f, arcname=f.as_posix(), recursive=False)
                n += 1
                if n % 1000 == 0:
                    print(f"  {n} archivos...")
    print(f"  {n} archivos en {salida}")


def abrir(paquete):
    with tarfile.open(paquete, encoding="utf-8") as t:
        t.extractall(".", filter="data")
        print(f"  {len(t.getmembers())} archivos")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "armar":
        armar(sys.argv[2], sys.argv[3:])
    elif len(sys.argv) == 3 and sys.argv[1] == "abrir":
        abrir(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)
