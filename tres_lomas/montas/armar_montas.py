"""
armar_montas.py
Junta todos los frames de las pruebas de montas en una sola carpeta MONTAS, separados por día.
No importa la cámara ni la subcarpeta de origen: quedan todos juntos en MONTAS/<día>/.

Qué hace:
  - Recorre todas las carpetas detecciones_monta_1120_* (y cualquier otra) buscando .jpg.
  - El DÍA sale de la subcarpeta con la fecha (por ejemplo .../camara_3/20260925/...), que es el día
    real de la foto (una carpeta "del 21" puede tener fotos de la madrugada del 22).
  - Saltea los recortes de eventos_monta (no son frames completos, son resúmenes del evento).
  - Si el mismo frame (misma cámara, día y hora) está repetido en varias carpetas, lo copia UNA vez,
    prefiriendo la versión limpia de dataset_crudo (sin recuadros dibujados).
  - COPIA, no mueve ni borra nada. Si lo corrés de nuevo, saltea lo que ya está copiado.
  - El nombre nuevo es camX_HHMMSS_mmm.jpg (la cámara va en el nombre solo para que no se pisen
    dos fotos de distintas cámaras tomadas en el mismo milisegundo).
  - Deja MONTAS/indice.csv con de dónde salió cada frame.

Uso (desde la carpeta pruebas_montas_TresLomas):
    python armar_montas.py              copia todo
    python armar_montas.py --simular    solo muestra cuántos frames quedarían por día, sin copiar
"""

import argparse
import csv
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DESTINO = RAIZ / "MONTAS"
FECHA = re.compile(r"^\d{8}$")
CAMARA = re.compile(r"^camara_(\d+)$")
# menor número = versión preferida cuando el mismo frame aparece en varias carpetas
PRIORIDAD = {"crudo": 0, "deteccion": 1, "deteccion_baja": 2}


def clasificar(ruta: Path):
    """Devuelve (fecha, camara, origen) o None si el archivo no es un frame."""
    partes = ruta.relative_to(RAIZ).parts
    if partes[0] == DESTINO.name:
        return None
    if "eventos_monta" in partes or "_evento" in ruta.stem:
        return None
    fechas = [p for p in partes[:-1] if FECHA.match(p)]
    camaras = [CAMARA.match(p).group(1) for p in partes[:-1] if CAMARA.match(p)]
    if not fechas or not camaras:
        return None
    if "dataset_crudo" in partes:
        origen = "crudo"
    elif partes[0].endswith("_biblioteca"):
        origen = "deteccion_baja"
    else:
        origen = "deteccion"
    return fechas[-1], camaras[-1], origen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simular", action="store_true", help="no copia, solo muestra el resumen")
    args = ap.parse_args()

    print(f"Buscando frames en {RAIZ} ...")
    elegidos = {}            # (fecha, camara, hora) -> (prioridad, ruta, origen)
    otras = defaultdict(list)
    salteados = 0
    for ruta in RAIZ.rglob("*"):
        if not ruta.is_file() or ruta.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        info = clasificar(ruta)
        if info is None:
            salteados += 1
            continue
        fecha, camara, origen = info
        clave = (fecha, camara, ruta.stem)
        nuevo = (PRIORIDAD[origen], ruta, origen)
        if clave in elegidos:
            viejo = elegidos[clave]
            if nuevo[0] < viejo[0]:
                elegidos[clave] = nuevo
                otras[clave].append(viejo[1])
            else:
                otras[clave].append(ruta)
        else:
            elegidos[clave] = nuevo

    por_dia = Counter(f for f, _, _ in elegidos)
    print(f"\nFrames únicos: {len(elegidos)}  (repetidos que se unificaron: {sum(len(v) for v in otras.values())}, "
          f"imágenes salteadas por no ser frames: {salteados})\n")
    print("Día          Frames")
    for dia in sorted(por_dia):
        print(f"{dia[:4]}-{dia[4:6]}-{dia[6:]}   {por_dia[dia]:>6}")

    if args.simular:
        print("\n(--simular: no se copió nada)")
        return

    DESTINO.mkdir(exist_ok=True)
    copiados = ya_estaban = 0
    filas = []
    for (fecha, camara, hora), (_, ruta, origen) in sorted(elegidos.items()):
        carpeta = DESTINO / fecha
        carpeta.mkdir(exist_ok=True)
        nombre = f"cam{camara}_{hora}{ruta.suffix.lower()}"
        destino = carpeta / nombre
        if destino.exists() and destino.stat().st_size == ruta.stat().st_size:
            ya_estaban += 1
        else:
            shutil.copy2(ruta, destino)
            copiados += 1
        filas.append([fecha, nombre, camara, hora, origen, str(ruta.relative_to(RAIZ)),
                      " | ".join(str(p.relative_to(RAIZ)) for p in otras.get((fecha, camara, hora), []))])
        if (copiados + ya_estaban) % 500 == 0:
            print(f"  {copiados + ya_estaban} / {len(elegidos)} ...")

    with open(DESTINO / "indice.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["dia", "archivo", "camara", "hora", "origen", "ruta_original", "tambien_estaba_en"])
        w.writerows(filas)

    print(f"\nListo: {copiados} copiados, {ya_estaban} ya estaban. Todo en {DESTINO}")
    print("Detalle de cada frame en MONTAS\\indice.csv")


if __name__ == "__main__":
    main()
