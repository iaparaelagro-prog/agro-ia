"""
exportar_mal.py
Junta todas las fotos que marcaste como "Mal" en la revisión de fondos y arma un paquete
para mandarle a Claude.

Crea la carpeta "para_claude" con:
  - mal_hoja_01.jpg, mal_hoja_02.jpg, ...  -> hojas con cada foto mala: original (con la vaca
    elegida en rojo) al lado del resultado sin fondo, con el nombre de la foto arriba.
  - resumen.txt   -> lista de las fotos malas con sus datos (cuántas vacas detectó, caja, etc.)
  - revision.json y resultados.json (copias)
Y además "para_claude.zip" con todo eso adentro (es lo que tenés que adjuntar en el chat).

No toca ni borra nada; solo lee lo que ya generó quitar_fondo.py.

Uso (desde la carpeta desfile_tres_lomas):
    python exportar_mal.py

Opciones:
    --salida   carpeta que generó quitar_fondo.py   (default: Asignaciones_sin_fondo)
    --destino  carpeta del paquete                  (default: para_claude)
    --por-hoja fotos por hoja                       (default: 6)
"""

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CELDA_W, CELDA_H = 440, 320
ETIQUETA_H = 30
MARGEN = 10


def leer_json(ruta, defecto):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def fuente(tam):
    for nombre in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def tablero(w, h, lado=16):
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for y in range(0, h, lado):
        for x in range(0, w, lado):
            if (x // lado + y // lado) % 2:
                d.rectangle([x, y, x + lado - 1, y + lado - 1], fill=(220, 220, 220))
    return im


def encajar(im, w, h):
    im = im.copy()
    im.thumbnail((w, h))
    return im, (w - im.width) // 2, (h - im.height) // 2


def celda_original(ruta):
    fondo = Image.new("RGB", (CELDA_W, CELDA_H), (34, 34, 34))
    if ruta and ruta.exists():
        im, x, y = encajar(Image.open(ruta).convert("RGB"), CELDA_W, CELDA_H)
        fondo.paste(im, (x, y))
    else:
        ImageDraw.Draw(fondo).text((20, CELDA_H // 2), "sin vista previa", fill=(255, 255, 255), font=fuente(18))
    return fondo


def celda_sinfondo(ruta):
    fondo = tablero(CELDA_W, CELDA_H)
    if ruta and ruta.exists():
        im, x, y = encajar(Image.open(ruta).convert("RGBA"), CELDA_W, CELDA_H)
        fondo.paste(im, (x, y), im)
    else:
        ImageDraw.Draw(fondo).text((20, CELDA_H // 2), "NO SE DETECTÓ VACA", fill=(184, 69, 47), font=fuente(20))
    return fondo


def main():
    p = argparse.ArgumentParser(description="Empaqueta las fotos marcadas como Mal para mandarle a Claude.")
    p.add_argument("--salida", default="Asignaciones_sin_fondo")
    p.add_argument("--destino", default="para_claude")
    p.add_argument("--por-hoja", type=int, default=6)
    args = p.parse_args()

    dir_salida = Path(args.salida)
    revision = leer_json(dir_salida / "revision.json", {})
    resultados = leer_json(dir_salida / "resultados.json", {})
    if not revision:
        print(f"[ERROR] No encontré marcas en {dir_salida / 'revision.json'}")
        sys.exit(1)

    malas = [rel for rel, v in revision.items() if v == "mal"]
    bien = sum(1 for v in revision.values() if v == "bien")
    print(f"Marcadas: {bien} bien, {len(malas)} mal (de {len(resultados)} fotos)")
    if not malas:
        print("No hay fotos marcadas como Mal. Nada para exportar.")
        return

    destino = Path(args.destino)
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)

    f_titulo = fuente(16)
    ancho = MARGEN * 3 + CELDA_W * 2
    alto_fila = ETIQUETA_H + CELDA_H + MARGEN
    lineas_resumen = [f"Fotos marcadas como MAL: {len(malas)}  (bien: {bien}, total: {len(resultados)})", ""]
    hojas = []

    for h, inicio in enumerate(range(0, len(malas), args.por_hoja), 1):
        grupo = malas[inicio:inicio + args.por_hoja]
        hoja = Image.new("RGB", (ancho, MARGEN + alto_fila * len(grupo)), (243, 240, 232))
        d = ImageDraw.Draw(hoja)
        for k, rel in enumerate(grupo):
            n = inicio + k + 1
            reg = resultados.get(rel, {})
            y = MARGEN + k * alto_fila
            extra = f"  ·  {reg.get('n_vacas', 0)} vacas detectadas" if reg.get("n_vacas", 0) > 1 else ""
            d.text((MARGEN, y + 5), f"#{n}  {rel}{extra}", fill=(31, 42, 31), font=f_titulo)
            vo = dir_salida / reg["vista_orig"] if reg.get("vista_orig") else None
            vs = dir_salida / reg["vista_sf"] if reg.get("vista_sf") else None
            hoja.paste(celda_original(vo), (MARGEN, y + ETIQUETA_H))
            hoja.paste(celda_sinfondo(vs), (MARGEN * 2 + CELDA_W, y + ETIQUETA_H))
            lineas_resumen.append(
                f"#{n}  {rel}\n"
                f"     vaca: {reg.get('vaca')}  |  estado: {reg.get('estado')}  |  vacas detectadas: {reg.get('n_vacas')}"
                f"  |  caja elegida: {[round(v) for v in reg['box']] if reg.get('box') else '-'}"
                f"  |  hoja: mal_hoja_{h:02d}.jpg")
        nombre = destino / f"mal_hoja_{h:02d}.jpg"
        hoja.save(nombre, "JPEG", quality=85)
        hojas.append(nombre)

    (destino / "resumen.txt").write_text("\n".join(lineas_resumen) + "\n", encoding="utf-8")
    shutil.copy(dir_salida / "revision.json", destino / "revision.json")
    if (dir_salida / "resultados.json").exists():
        shutil.copy(dir_salida / "resultados.json", destino / "resultados.json")

    zip_ruta = destino.with_suffix(".zip")
    with zipfile.ZipFile(zip_ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(destino.iterdir()):
            z.write(f, f.name)

    tam_mb = zip_ruta.stat().st_size / 1024 ** 2
    print(f"\nListo: {len(hojas)} hojas con {len(malas)} fotos malas.")
    print(f"Carpeta: {destino.resolve()}")
    print(f"Zip para adjuntar: {zip_ruta.resolve()}  ({tam_mb:.1f} MB)")


if __name__ == "__main__":
    main()
