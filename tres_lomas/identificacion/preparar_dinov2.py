"""
preparar_dinov2.py
Arma el dataset para entrenar DINOv2 con las vacas identificadas en el campo, y lo deja en un zip
listo para subir a Google Drive y entrenar en Colab (entrenar_dinov2.ipynb).

Es liviano: solo recorta imágenes (no carga ningún modelo). Tarda unos minutos.

Qué hace:
  1. Lee campo_servidor/resultado_por_frame.csv (lo que marcaron en la página).
  2. FILTRO AUTOMÁTICO:
       - saca los frames marcados con problema (dos vacas, cortada, etc.);
       - saca los CHOQUES: si la misma vaca aparece dos veces en el mismo frame, saca las dos
         (una está mal y no sabemos cuál);
       - saca las vacas muy chicas (lado menor a 70 px), igual que campo.py.
  3. SEPARA PRUEBA POR RECORRIDO (no por frame): para cada vaca con 2 o más recorridos, aparta
     ~25% de sus recorridos (al menos 1) para probar. Nunca usa para prueba los recorridos de más de
     500 frames (son los más dudosos). Así la prueba mide si el modelo reconoce a la vaca en una
     pasada que NUNCA vio, que es lo que importa en el campo.
  4. BALANCEA: como máximo 300 recortes por vaca para entrenar, repartidos entre todos sus recorridos
     y espaciados en el tiempo (no 300 frames casi iguales de la vaca quieta). Para prueba, hasta
     20 recortes por recorrido.
  5. Recorta cada vaca con 15% de margen (en Colab se le quita el fondo con SAM y se lleva al 5%
     de margen que usa campo.py) y la achica a 320 px de lado como máximo.
  6. Copia las fotos de referencia sin fondo (Asignaciones_sin_fondo/<vaca>/*.png) y los recortes
     de biblioteca (Asignaciones (Verificado)/<vaca>/bib_*.png): entran al entrenamiento de las
     124 vacas y a la galería de la prueba.
  7. Deja todo en dataset_dinov2/ y arma dataset_dinov2.zip.

Uso (desde la carpeta desfile_tres_lomas):
    python preparar_dinov2.py
Opciones:
    --campo      carpeta con lo bajado del servidor     (default: campo_servidor)
    --frames     carpeta de frames                      (default: frames_desfile_camaras)
    --tope       recortes máximos por vaca para entrenar (default: 300)
    --semilla    para que la separación de prueba sea siempre la misma (default: 7)
"""

import argparse
import csv
import json
import random
import shutil
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageOps

RAIZ = Path(__file__).resolve().parent
MIN_LADO = 70
MARGEN = 0.15
LADO_MAX = 320
POR_REC_PRUEBA = 20
LARGO_DUDOSO = 500


def espaciados(lista, n):
    """n elementos repartidos parejo a lo largo de la lista (ya ordenada por tiempo)."""
    if len(lista) <= n:
        return list(lista)
    paso = len(lista) / n
    return [lista[int(i * paso + paso / 2)] for i in range(n)]


def caja_con_margen(box, ancho, alto, margen):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return (int(max(0, x1 - w * margen)), int(max(0, y1 - h * margen)),
            int(min(ancho, x2 + w * margen)), int(min(alto, y2 + h * margen)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campo", default="campo_servidor")
    ap.add_argument("--frames", default="frames_desfile_camaras")
    ap.add_argument("--tope", type=int, default=300)
    ap.add_argument("--semilla", type=int, default=7)
    args = ap.parse_args()

    dir_campo, dir_frames = RAIZ / args.campo, RAIZ / args.frames
    csv_ruta = dir_campo / "resultado_por_frame.csv"
    if not csv_ruta.exists():
        raise SystemExit(f"No encuentro {csv_ruta}")
    if not dir_frames.is_dir():
        raise SystemExit(f"No encuentro la carpeta de frames {dir_frames}")

    # ------------------------------------------------------------ 1-2. leer y filtrar
    filas = list(csv.DictReader(open(csv_ruta, encoding="utf-8-sig")))
    for f in filas:
        f["box"] = [float(f[k]) for k in ("x1", "y1", "x2", "y2")]
        f["segundo"] = float(f["segundo"])
    largo_rec = Counter(f["recorrido"] for f in filas)
    choque = Counter((f["frame"], f["vaca"]) for f in filas)

    motivos = Counter()
    buenas = []
    for f in filas:
        x1, y1, x2, y2 = f["box"]
        if f["problema"]:
            motivos["frame marcado con problema"] += 1
        elif choque[(f["frame"], f["vaca"])] > 1:
            motivos["choque (misma vaca dos veces en el frame)"] += 1
        elif min(x2 - x1, y2 - y1) < MIN_LADO:
            motivos[f"muy chica (< {MIN_LADO} px)"] += 1
        else:
            buenas.append(f)

    print(f"Recortes etiquetados: {len(filas)}")
    for m, n in motivos.most_common():
        print(f"  - sacados por {m}: {n}")
    print(f"Quedan: {len(buenas)}\n")

    # ------------------------------------------------------------ 3. separar prueba por recorrido
    por_vaca = defaultdict(lambda: defaultdict(list))
    for f in buenas:
        por_vaca[f["vaca"]][f["recorrido"]].append(f)
    for recs in por_vaca.values():
        for lista in recs.values():
            lista.sort(key=lambda f: f["segundo"])

    rnd = random.Random(args.semilla)
    prueba_recs = set()
    for vaca, recs in sorted(por_vaca.items()):
        utiles = [r for r, l in recs.items() if len(l) >= 3]
        if len(utiles) < 2:
            continue
        candidatos = sorted(r for r in utiles if largo_rec[r] <= LARGO_DUDOSO)
        n = max(1, round(len(utiles) * 0.25))
        n = min(n, len(candidatos), len(utiles) - 1)
        prueba_recs.update(rnd.sample(candidatos, n))

    # ------------------------------------------------------------ 4. balancear
    elegidos = []  # (fila, split)
    for vaca, recs in sorted(por_vaca.items()):
        entrenar = {r: l for r, l in recs.items() if r not in prueba_recs}
        for r in sorted(recs):
            if r in prueba_recs:
                elegidos += [(f, "prueba") for f in espaciados(recs[r], POR_REC_PRUEBA)]
        # reparto parejo del tope entre los recorridos de entrenamiento (ronda)
        cupo = {r: 0 for r in entrenar}
        restante = args.tope
        activos = [r for r in sorted(entrenar) if entrenar[r]]
        while restante > 0 and activos:
            por_cada = max(1, restante // len(activos))
            nuevos = []
            for r in activos:
                dar = min(por_cada, len(entrenar[r]) - cupo[r], restante)
                cupo[r] += dar
                restante -= dar
                if cupo[r] < len(entrenar[r]):
                    nuevos.append(r)
                if restante <= 0:
                    break
            activos = nuevos
        for r, n in cupo.items():
            elegidos += [(f, "entrenar") for f in espaciados(entrenar[r], n)]

    # ------------------------------------------------------------ 5. recortar
    salida = RAIZ / "dataset_dinov2"
    if salida.exists():
        shutil.rmtree(salida)
    (salida / "campo").mkdir(parents=True)

    por_frame = defaultdict(list)
    for f, split in elegidos:
        por_frame[f["frame"]].append((f, split))

    meta = []
    faltan = 0
    print(f"Recortando {len(elegidos)} vacas de {len(por_frame)} frames...")
    for i, (nombre, items) in enumerate(sorted(por_frame.items()), 1):
        ruta = dir_frames / nombre
        if not ruta.exists():
            faltan += len(items)
            continue
        with Image.open(ruta) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            for j, (f, split) in enumerate(items):
                c = caja_con_margen(f["box"], im.width, im.height, MARGEN)
                rec = im.crop(c)
                esc = min(1.0, LADO_MAX / max(rec.size))
                if esc < 1.0:
                    rec = rec.resize((max(1, round(rec.width * esc)), max(1, round(rec.height * esc))),
                                     Image.LANCZOS)
                # recuadro de la vaca DENTRO del recorte (para SAM en Colab)
                x1, y1, x2, y2 = f["box"]
                caja = [round((x1 - c[0]) * esc, 1), round((y1 - c[1]) * esc, 1),
                        round((x2 - c[0]) * esc, 1), round((y2 - c[1]) * esc, 1)]
                d = salida / "campo" / f["vaca"]
                d.mkdir(exist_ok=True)
                archivo = f"{f['recorrido']}__{Path(nombre).stem}__{j}.jpg"
                rec.save(d / archivo, quality=92)
                meta.append({"archivo": f"campo/{f['vaca']}/{archivo}", "vaca": f["vaca"],
                             "recorrido": f["recorrido"], "largo_recorrido": largo_rec[f["recorrido"]],
                             "frame": nombre, "segundo": f["segundo"], "split": split,
                             "caja": json.dumps(caja)})
        if i % 500 == 0:
            print(f"  {i} / {len(por_frame)} frames")
    if faltan:
        print(f"[AVISO] {faltan} recortes no se hicieron porque falta el frame en {dir_frames}")

    # ------------------------------------------------------------ 6. referencias
    refs = 0
    dir_sf = RAIZ / "Asignaciones_sin_fondo"
    dir_ver = RAIZ / "Asignaciones (Verificado)"
    fuentes = []
    if dir_sf.is_dir():
        fuentes += [(p.parent.name, p) for p in dir_sf.glob("*/*.png") if not p.parent.name.startswith("_")]
    if dir_ver.is_dir():
        fuentes += [(p.parent.name, p) for p in dir_ver.glob("*/bib_*.png")]
    for vaca, p in fuentes:
        d = salida / "referencias" / vaca
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, d / p.name)
        meta.append({"archivo": f"referencias/{vaca}/{p.name}", "vaca": vaca, "recorrido": "",
                     "largo_recorrido": 0, "frame": "", "segundo": "", "split": "referencia", "caja": ""})
        refs += 1

    with open(salida / "meta.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(meta[0]))
        w.writeheader()
        w.writerows(meta)

    # ------------------------------------------------------------ resumen
    ent = Counter(m["vaca"] for m in meta if m["split"] == "entrenar")
    pru = Counter(m["vaca"] for m in meta if m["split"] == "prueba")
    vacas_ref = {m["vaca"] for m in meta if m["split"] == "referencia"}
    print("\nResumen")
    print(f"  Entrenar: {sum(ent.values())} recortes de {len(ent)} vacas del campo")
    print(f"  Prueba:   {sum(pru.values())} recortes, {len(prueba_recs)} recorridos de {len(pru)} vacas")
    print(f"  Referencias: {refs} fotos de {len(vacas_ref)} vacas")

    print("\nArmando dataset_dinov2.zip ...")
    zip_ruta = RAIZ / "dataset_dinov2.zip"
    with zipfile.ZipFile(zip_ruta, "w", zipfile.ZIP_STORED) as z:
        for p in salida.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(RAIZ).as_posix())
    print(f"Listo: {zip_ruta}  ({zip_ruta.stat().st_size / 1e6:.0f} MB)")
    print("Subilo a Google Drive, a Mi unidad/vacas/ (ver pasos en entrenar_dinov2.ipynb).")


if __name__ == "__main__":
    main()
