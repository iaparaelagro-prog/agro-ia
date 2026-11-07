"""
borrar_sin_vacas.py
Revisa los frames con YOLO de a tandas (100 por defecto) y borra los que no tienen ninguna vaca.

Cómo trabaja:
  - Procesa 100 frames, borra los que no tienen vacas y recién ahí pasa a los siguientes 100.
  - Anota en "<carpeta>_sin_vacas.txt" cada foto borrada.
  - Anota en "<carpeta>_revisados.txt" las fotos que sí tienen vacas, así si cortás con Ctrl+C
    y lo volvés a correr, sigue desde donde quedó sin revisar de nuevo lo ya hecho.
  - Te pide confirmación una sola vez al principio (s/n).

Uso (desde la carpeta desfile_tres_lomas):
    python borrar_sin_vacas.py
    python borrar_sin_vacas.py --tanda 100 --conf 0.35

Opciones:
    --carpeta   carpeta con los frames                (default: frames_desfile_camaras)
    --tanda     frames por tanda                      (default: 100)
    --conf      confianza mínima de YOLO              (default: 0.35, igual que el identificador)
    --min-lado  ignora vacas más chicas que esto (px)  (default: 40, igual que el identificador)
    --yolo      modelo YOLO                           (default: yolo11s.pt)
    --si        no pide confirmación
"""

import argparse
import sys
import time
from pathlib import Path

EXT_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
CLASE_VACA = 19  # "cow" en COCO


def main():
    p = argparse.ArgumentParser(description="Borra los frames donde YOLO no detecta vacas, de a tandas.")
    p.add_argument("--carpeta", default="frames_desfile_camaras")
    p.add_argument("--tanda", type=int, default=100)
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--min-lado", type=int, default=40)
    p.add_argument("--yolo", default="yolo11s.pt")
    p.add_argument("--si", action="store_true", help="No pedir confirmación")
    args = p.parse_args()

    carpeta = Path(args.carpeta)
    if not carpeta.is_dir():
        print(f"[ERROR] No existe la carpeta: {carpeta.resolve()}")
        sys.exit(1)

    archivo_borradas = carpeta.parent / f"{carpeta.name}_sin_vacas.txt"
    archivo_revisados = carpeta.parent / f"{carpeta.name}_revisados.txt"

    revisados = set()
    if archivo_revisados.exists():
        revisados = set(archivo_revisados.read_text(encoding="utf-8").split())

    todas = sorted(f for f in carpeta.iterdir() if f.is_file() and f.suffix.lower() in EXT_IMG)
    fotos = [f for f in todas if f.name not in revisados]
    if not fotos:
        print(f"No quedan fotos por revisar en {carpeta} ({len(todas)} ya revisadas con vacas).")
        return

    tandas = (len(fotos) + args.tanda - 1) // args.tanda
    print(f"Fotos en {carpeta}: {len(todas)}  |  ya revisadas: {len(todas) - len(fotos)}  |  "
          f"por revisar: {len(fotos)} en {tandas} tandas de {args.tanda}")

    if not args.si:
        resp = input("Las fotos sin vacas se van a BORRAR a medida que se revisa cada tanda. "
                     "¿Seguir? (s/n): ").strip().lower()
        if not resp.startswith("s"):
            print("No se hizo nada.")
            return

    from ultralytics import YOLO
    modelo = YOLO(args.yolo)

    total_borradas = 0
    t0 = time.time()
    for n, inicio in enumerate(range(0, len(fotos), args.tanda), 1):
        tanda = fotos[inicio:inicio + args.tanda]

        con_vacas, sin_vacas = [], []
        resultados = modelo.predict(source=[str(f) for f in tanda], stream=True,
                                    classes=[CLASE_VACA], conf=args.conf, verbose=False)
        for r in resultados:
            cajas = r.boxes.xyxy.cpu().numpy()
            hay_vaca = any(min(x2 - x1, y2 - y1) >= args.min_lado for x1, y1, x2, y2 in cajas)
            (con_vacas if hay_vaca else sin_vacas).append(Path(r.path))

        borradas = []
        for f in sin_vacas:
            try:
                f.unlink()
                borradas.append(f.name)
            except OSError as e:
                print(f"  [AVISO] No se pudo borrar {f.name}: {e}")

        if borradas:
            with archivo_borradas.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(borradas) + "\n")
        if con_vacas:
            with archivo_revisados.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(f.name for f in con_vacas) + "\n")

        total_borradas += len(borradas)
        hechas = inicio + len(tanda)
        restante = (time.time() - t0) / hechas * (len(fotos) - hechas)
        print(f"Tanda {n}/{tandas}: {len(con_vacas)} con vacas, {len(borradas)} borradas  "
              f"| total borradas: {total_borradas}  | faltan ~{restante / 60:.1f} min")

    print(f"\nListo. Borradas: {total_borradas}. Quedan {len(todas) - total_borradas} fotos en {carpeta}.")
    print(f"Lista de borradas: {archivo_borradas}")


if __name__ == "__main__":
    main()