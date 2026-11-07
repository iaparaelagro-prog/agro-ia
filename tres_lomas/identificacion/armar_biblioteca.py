"""
armar_biblioteca.py
Arma la carpeta "biblioteca" con TODAS las fotos sin fondo de cada vaca, una carpeta por vaca:

    biblioteca/
        1111/
            1111_1.png   <- foto lado A de Asignaciones (sin fondo)
            1111_2.png   <- foto lado B de Asignaciones (sin fondo)
            1111_3.png   <- recortes de los videos de costado (bib_*.png), primero lado A y después lado B
            ...
        _indice.csv      <- de qué archivo original sale cada foto

Fuentes (no se modifican, solo se copian):
  - Asignaciones_sin_fondo/<vaca>/*.png           (fotos de Asignaciones con el fondo quitado)
  - Asignaciones (Verificado)/<vaca>/bib_*.png     (recortes confirmados en biblioteca_lados.py)

Uso (desde la carpeta desfile_tres_lomas):
    python armar_biblioteca.py
Se puede volver a correr cuando haya más recortes: la carpeta "biblioteca" se rearma desde cero.

Opciones:
    --salida       carpeta a crear                    (default: biblioteca)
    --originales   carpetas de vacas                   (default: "Asignaciones (Verificado)")
    --sin-fondo    fotos de Asignaciones sin fondo     (default: Asignaciones_sin_fondo)
"""

import argparse
import csv
import re
import shutil
from pathlib import Path

from PIL import Image


def orden_natural(texto):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(texto))]


def es_imagen_sana(ruta):
    try:
        with Image.open(ruta) as im:
            im.verify()
        return True
    except Exception:
        return False


def orden_fotos_asignacion(f):
    """Primero la A, después la B, después las demás (B2, vlcsnap...)."""
    s = f.stem.upper()
    clave = 0 if s.endswith("_A") else 1 if s.endswith("_B") else 2
    return (clave, orden_natural(f.name))


def orden_recortes(f):
    """Primero los del lado A, después los del lado B; dentro de cada lado por video y frame."""
    return (0 if f.name.startswith("bib_LADOA") else 1, orden_natural(f.name))


def main():
    p = argparse.ArgumentParser(description="Arma la carpeta biblioteca con todas las fotos sin fondo por vaca.")
    p.add_argument("--salida", default="biblioteca")
    p.add_argument("--originales", default="Asignaciones (Verificado)")
    p.add_argument("--sin-fondo", default="Asignaciones_sin_fondo")
    args = p.parse_args()

    dir_orig, dir_sf, salida = Path(args.originales), Path(args.sin_fondo), Path(args.salida)
    if not dir_orig.is_dir():
        print(f"[ERROR] No existe {dir_orig.resolve()}")
        return
    if salida.exists():
        shutil.rmtree(salida)  # se rearma desde cero (las fuentes no se tocan)
    salida.mkdir()

    filas, total, danadas = [], 0, []
    vacas = sorted([d.name for d in dir_orig.iterdir() if d.is_dir()], key=orden_natural)
    for vaca in vacas:
        fotos = sorted((dir_sf / vaca).glob("*.png"), key=orden_fotos_asignacion) if (dir_sf / vaca).is_dir() else []
        recortes = sorted((dir_orig / vaca).glob("bib_*.png"), key=orden_recortes)
        destino = salida / vaca
        destino.mkdir()
        n = 0
        for origen, tipo in [(f, "asignacion") for f in fotos] + [(f, "recorte_video") for f in recortes]:
            if not es_imagen_sana(origen):
                danadas.append(str(origen))
                continue
            n += 1
            nuevo = destino / f"{vaca}_{n}.png"
            shutil.copyfile(origen, nuevo)
            filas.append({"vaca": vaca, "archivo": f"{vaca}/{nuevo.name}", "tipo": tipo, "origen": str(origen)})
        total += n
        if n == 0:
            print(f"  [AVISO] La vaca {vaca} no tiene ninguna foto sin fondo.")

    with open(salida / "_indice.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["vaca", "archivo", "tipo", "origen"])
        w.writeheader()
        w.writerows(filas)

    por_vaca = [sum(1 for r in filas if r["vaca"] == v) for v in vacas]
    print(f"Listo: {salida.resolve()}")
    print(f"  {len(vacas)} vacas · {total} fotos · entre {min(por_vaca)} y {max(por_vaca)} fotos por vaca")
    print(f"  {sum(r['tipo'] == 'asignacion' for r in filas)} de Asignaciones + "
          f"{sum(r['tipo'] == 'recorte_video' for r in filas)} recortes de los videos")
    if danadas:
        print(f"  [AVISO] Se saltearon {len(danadas)} archivos dañados: {danadas[:5]}")
    print("  El detalle de dónde sale cada foto está en _indice.csv")


if __name__ == "__main__":
    main()
