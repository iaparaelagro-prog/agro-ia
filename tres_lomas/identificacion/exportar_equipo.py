"""
exportar_equipo.py
Arma los archivos de la página compartida "Identificación en equipo" (un artefacto de claude.ai):
los recorridos de campo.py que todavía no tienen etiqueta, con sus recortes, el frame completo
y el ranking de vacas más parecidas, más las fotos de lado A y lado B de cada vaca.

Qué guarda (en la carpeta equipo/):
    equipo/datos.json     recorridos, rankings y dónde está cada foto
    equipo/img/r*.jpg     hojas con los recorridos (10 por hoja: frame completo + 6 recortes)
    equipo/img/v*.jpg     hojas con las fotos de referencia (A y B de cada vaca)

Uso (desde la carpeta desfile_tres_lomas, con campo.py CERRADO o no, da igual: solo lee):
    python exportar_equipo.py
Después Claude publica la carpeta equipo/ como artefacto. Lo que marque el equipo se trae de
vuelta con importar_equipo.py.

Opciones:
    --recortes   recortes por recorrido     (default: 6)
    --todos      exportar también los recorridos que ya tienen etiqueta (default: solo pendientes)
"""

import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw

import campo as C

I = C.I

SALIDA = Path("equipo")
POR_HOJA = 10                 # recorridos por hoja
CTX_W, CTX_H = 400, 225       # frame completo (16:9)
CELDA = 225                   # recorte cuadrado
REF = 200                     # foto de referencia
REF_COLS, REF_FILAS = 10, 10  # 100 fotos por hoja
FONDO_REC = (34, 34, 34)
FONDO_REF = (124, 116, 104)


def encajar(im, w, h, fondo):
    """La imagen achicada para entrar en w x h, centrada sobre el fondo."""
    im = im.convert("RGB")
    im.thumbnail((w, h))
    lienzo = Image.new("RGB", (w, h), fondo)
    lienzo.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return lienzo


def frame_con_recuadro(campo, clave):
    d = campo.dets[clave]
    im = I.abrir_imagen(campo.dir_frames / d["frame"]).convert("RGB")
    esc = CTX_W / im.width
    im = im.resize((CTX_W, round(im.height * esc)))
    x1, y1, x2, y2 = [v * esc for v in d["box"]]
    ImageDraw.Draw(im).rectangle([x1, y1, x2, y2], outline=(255, 205, 0), width=3)
    return encajar(im, CTX_W, CTX_H, FONDO_REC)


def main():
    p = argparse.ArgumentParser(description="Exporta los recorridos para la página del equipo.")
    p.add_argument("--recortes", type=int, default=6)
    p.add_argument("--todos", action="store_true")
    a = p.parse_args()

    # mismos valores por defecto que campo.py
    args = argparse.Namespace(frames="frames_desfile_camaras", trabajo="campo", por_recorrido=4, min_frames=3,
                              min_lado=70, hueco=1.5, hilos=2, pausa=0.1, puerto=5004,
                              fotos="recorridas_campo", ancho_fotos=1280)
    campo = C.Campo(args)
    (SALIDA / "img").mkdir(parents=True, exist_ok=True)
    for viejo in (SALIDA / "img").glob("*.jpg"):
        viejo.unlink()

    # ---- vacas y fotos de referencia por lado
    lados = campo._fotos_por_lado()
    vacas = sorted(set(m["vaca"] for m in campo.galeria.ref_meta), key=I.orden_natural)
    vi = {v: k for k, v in enumerate(vacas)}
    primera = {}
    for i, m in enumerate(campo.galeria.ref_meta):
        primera.setdefault(m["vaca"], i)
    pedidas = []  # (vaca, lado, índice de referencia)
    for v in vacas:
        ls = lados.get(v, {})
        for l in ("A", "B"):
            if l in ls:
                pedidas.append((v, l, ls[l]))
        if not ls:
            pedidas.append((v, "otra", primera[v]))
    fotos = {}
    por_hoja_ref = REF_COLS * REF_FILAS
    for h in range(0, len(pedidas), por_hoja_ref):
        grupo = pedidas[h:h + por_hoja_ref]
        filas = (len(grupo) + REF_COLS - 1) // REF_COLS
        hoja = Image.new("RGB", (REF_COLS * REF, filas * REF), FONDO_REF)
        nombre = f"v{h // por_hoja_ref:02d}.jpg"
        for k, (v, l, idx) in enumerate(grupo):
            im = campo.refs._imagen_referencia(campo.galeria.ref_meta[idx])
            hoja.paste(encajar(im, REF, REF, FONDO_REF), ((k % REF_COLS) * REF, (k // REF_COLS) * REF))
            fotos.setdefault(v, {})[l] = [nombre, k]
        hoja.save(SALIDA / "img" / nombre, "JPEG", quality=82)
    print(f"Fotos de referencia: {len(pedidas)} de {len(vacas)} vacas")

    # ---- recorridos
    listos = [rid for rid, r in campo.recs.items() if r["estado"] == "listo"]
    listos.sort(key=lambda rid: campo._tiempos(rid)[0])
    exportar = [rid for rid in listos if a.todos or rid not in campo.etiquetas]
    hechos = [{"id": rid, "t0": campo._tiempos(rid)[0], "t1": campo._tiempos(rid)[1], "vaca": et["vaca"]}
              for rid, et in campo.etiquetas.items() if et.get("accion") == "vaca" and rid in campo.recs]
    recs = []
    for h in range(0, len(exportar), POR_HOJA):
        grupo = exportar[h:h + POR_HOJA]
        hoja = Image.new("RGB", (CTX_W + a.recortes * CELDA, len(grupo) * CTX_H), FONDO_REC)
        nombre = f"r{h // POR_HOJA:03d}.jpg"
        for fila, rid in enumerate(grupo):
            r = campo.recs[rid]
            it = campo._item(rid)
            y = fila * CTX_H
            hoja.paste(frame_con_recuadro(campo, it["mejor"]), (0, y))
            elegidos = campo._elegir(r["claves"], a.recortes)
            por_frame = {}
            for c in elegidos:
                por_frame.setdefault(campo.dets[c]["frame"], []).append(c)
            col = {c: k for k, c in enumerate(elegidos)}
            for nf, cs in por_frame.items():
                im = I.abrir_imagen(campo.dir_frames / nf)
                for c in cs:
                    rec = encajar(I.recortar(im, campo.dets[c]["box"]), CELDA, CELDA, FONDO_REC)
                    hoja.paste(rec, (CTX_W + col[c] * CELDA, y))
            rk = []
            for s, v, _ in campo.ranking(rid, k=None, filtrar=False):
                if v in vi:
                    rk += [vi[v], round(s * 1000)]
            recs.append({"id": rid, "t0": it["t0"], "t1": it["t1"], "n": it["n"],
                         "despues": bool(it["despues"]), "amontonado": bool(it["amontonado"]),
                         "hoja": nombre, "fila": fila, "nrec": len(elegidos),
                         "frame": campo.dets[it["mejor"]]["frame"], "rk": rk})
        hoja.save(SALIDA / "img" / nombre, "JPEG", quality=80)
        print(f"  recorridos {h + len(grupo)}/{len(exportar)}")

    datos = {
        "generado": round(time.time()),
        "vacas": vacas, "fotos": fotos, "recs": recs, "hechos": hechos,
        "hojas": {"ctx": [CTX_W, CTX_H], "celda": CELDA, "recortes": a.recortes,
                  "ref": REF, "ref_cols": REF_COLS},
        "motivos_descarte": C.MOTIVOS_DESCARTE,
        "progreso": {"frames": campo.estado["siguiente"], "total": len(campo.frames)},
    }
    with open(SALIDA / "datos.json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    tam = sum(f.stat().st_size for f in SALIDA.rglob("*") if f.is_file()) / 1e6
    print(f"Listo: {len(recs)} recorridos, {len(list((SALIDA / 'img').glob('*.jpg')))} hojas, {tam:.1f} MB en {SALIDA.resolve()}")


if __name__ == "__main__":
    main()
