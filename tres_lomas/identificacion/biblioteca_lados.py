"""
biblioteca_lados.py
Arma una biblioteca más amplia de cada vaca a partir de los videos de costado
(carpetas "LADO A" y "LADO B"), antes de buscar en el video del campo.

Pasos:
  1. preparar  -> saca frames de cada video (3 por segundo), detecta las vacas (yolo11s),
                  les quita el fondo con SAM y guarda UN PNG TRANSPARENTE POR VACA
                  (si en un frame hay dos vacas, salen dos recortes). Calcula la huella
                  visual (DINOv2) de cada recorte. Liviano y retomable.
  2. revisar   -> une los recortes de la misma vaca en frames seguidos ("pasadas") y abre
                  una página donde, para cada pasada, el programa propone las 5 vacas más
                  probables. Vos confirmás (una vez por pasada, no frame por frame).
                  Cada pasada confirmada copia sus mejores recortes (PNG sin fondo) a la carpeta
                  de esa vaca:  Asignaciones (Verificado)\\<vaca>\\bib_....png
                  y además mejora las propuestas de las pasadas siguientes.
  3. identificador.py usa automáticamente esos bib_*.png como referencias extra.

Uso (desde la carpeta desfile_tres_lomas):
    python biblioteca_lados.py preparar      (~1 hora para LADO A; podés cortar con Ctrl+C y seguir)
    python biblioteca_lados.py revisar       (abre http://127.0.0.1:5003)
    python biblioteca_lados.py estado        (resumen de cómo va)

Se puede revisar mientras "preparar" sigue corriendo: aparecen los videos que ya terminaron
(recargá la página para ver los nuevos).

Opciones:
    --lados        carpetas con videos                       (default: "LADO A" "LADO B")
    --trabajo      carpeta donde se guarda todo              (default: lados)
    --biblioteca   dónde se copian los recortes confirmados  (default: "Asignaciones (Verificado)",
                   una carpeta por vaca, archivos bib_*.png)
    --fps          frames por segundo a extraer              (default: 3)
    --conf         confianza mínima de YOLO                  (default: 0.35)
    --min-alto     ignora vacas más bajas que esta fracción del alto del frame (default: 0.25;
                   así no se toman las vacas chiquitas del fondo, en los corrales)
    --por-pasada   recortes que se copian a la Biblioteca por cada pasada confirmada (default: 4)
    --min-frames   las pasadas con menos frames que esto se descartan solas (default: 3)
    --grupos       orden de las vacas por tanda y qué video es cada tanda (default: grupos.json).
                   Con esto solo se proponen vacas de la tanda del video, priorizando la que
                   toca según el orden (las pasadas ya confirmadas sirven de ancla).
    --yolo / --sam / --mascara   modelos (default: yolo11s.pt / sam2.1_t.pt / sam)
    --hilos        hilos de CPU                              (default: 2)
    --pausa        segundos de descanso entre imágenes       (default: 0.1)
    --puerto       puerto de la página                       (default: 5003)
"""

import identificador as I  # primero: limita los hilos de CPU antes de cargar torch

import argparse
import math
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

EXT_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".mts", ".3gp"}
PESO_ORDEN = 0.08  # cuánto pesa estar en el lugar esperado de la tanda (la similitud va de 0 a 1)


def tiempo_frame(rel):
    """Segundo del video a partir del nombre del frame (…_t0012.34s.jpg)."""
    m = re.search(r"_t(\d+(?:\.\d+)?)s", rel)
    return float(m.group(1)) if m else 0.0


# ----------------------------------------------------------------------------- utilidades

def videos_de(lados):
    for lado in lados:
        d = Path(lado)
        if not d.is_dir():
            print(f"[AVISO] No existe la carpeta {d.resolve()}; se saltea.")
            continue
        for v in sorted(d.iterdir(), key=I.orden_natural):
            if v.is_file() and v.suffix.lower() in EXT_VIDEO:
                yield lado, v


def clave_video(lado, video):
    return f"{lado}/{video.stem}"


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


class Modelos:
    """Carga los modelos recién cuando hacen falta (para no gastar memoria de más)."""

    def __init__(self, args):
        self.args = args
        self._det = self._seg = self._emb = None

    def detector(self):
        if self._det is None:
            self._det = I.Detector(self.args.yolo, self.args.conf)
        return self._det

    def segmentador(self):
        if self.args.mascara != "sam":
            return None
        if self._seg is None:
            self._seg = I.Segmentador(self.args.sam)
        return self._seg

    def embebedor(self):
        if self._emb is None:
            self._emb = I.Embebedor()
        return self._emb


def limitar_hilos(n):
    import torch
    torch.set_num_threads(n)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    try:
        import cv2
        cv2.setNumThreads(1)
    except ImportError:
        pass


class Datos:
    """Lo que queda guardado en la carpeta de trabajo."""

    def __init__(self, dir_trabajo):
        self.dir = Path(dir_trabajo)
        self.dir_frames = self.dir / "frames"
        self.dir_recortes = self.dir / "recortes"
        self.dir_frames.mkdir(parents=True, exist_ok=True)
        self.dir_recortes.mkdir(parents=True, exist_ok=True)
        self.progreso = I.leer_json(self.dir / "progreso.json", {"extraidos": {}, "hechos": []})
        self.dets = I.leer_json(self.dir / "detecciones.json", [])
        ruta_emb = self.dir / "emb.npy"
        self.E = np.load(ruta_emb) if ruta_emb.exists() else np.zeros((0, 384), np.float32)
        self._reparar()

    def _reparar(self):
        """Si se cortó en el medio de un guardado, descarta lo que quedó a medias
        (se guarda en orden emb -> detecciones -> progreso, así que alcanza con recortar la cola)."""
        hechos = set(self.progreso["hechos"])
        n = min(len(self.dets), len(self.E))
        while n > 0 and self.dets[n - 1]["frame"] not in hechos:
            n -= 1
        if n != len(self.dets) or n != len(self.E):
            self.dets, self.E = self.dets[:n], self.E[:n]
        self.set_hechos = hechos

    def guardar(self):
        tmp = self.dir / "emb.npy.tmp"
        with open(tmp, "wb") as f:
            np.save(f, self.E.astype(np.float32))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.dir / "emb.npy")
        I.escribir_json(self.dir / "detecciones.json", self.dets)
        I.escribir_json(self.dir / "progreso.json", self.progreso)

    def frames_de_video(self, clave):
        d = self.dir_frames / clave
        if not d.is_dir():
            return []
        return [f"{clave}/{f.name}" for f in sorted(d.glob("*.jpg"), key=I.orden_natural)]

    def video_listo(self, clave):
        if clave not in self.progreso["extraidos"]:
            return False
        return all(f in self.set_hechos for f in self.frames_de_video(clave))


# ----------------------------------------------------------------------------- 1. preparar

def extraer_frames(video, carpeta, fps):
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"  [ERROR] No se pudo abrir {video}")
        return 0
    fps_video = cap.get(cv2.CAP_PROP_FPS) or 30.0
    paso = fps_video / min(fps, fps_video)
    carpeta.mkdir(parents=True, exist_ok=True)
    i, proximo, n = 0, 0.0, 0
    while cap.grab():
        if i + 1e-6 >= proximo:
            ok, fr = cap.retrieve()
            if ok:
                nombre = f"f{i:06d}_t{i / fps_video:07.2f}s.jpg"
                ok2, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok2:
                    buf.tofile(str(carpeta / nombre))  # soporta tildes en la ruta
                    n += 1
            proximo += paso
        i += 1
        if i % 300 == 0:
            time.sleep(0.02)
    cap.release()
    return n


def preparar(args):
    limitar_hilos(args.hilos)
    datos = Datos(args.trabajo)
    modelos = Modelos(args)
    videos = list(videos_de(args.lados))
    if not videos:
        print("[ERROR] No encontré videos.")
        sys.exit(1)

    # --- frames
    for lado, v in videos:
        clave = clave_video(lado, v)
        if clave in datos.progreso["extraidos"]:
            continue
        print(f"Sacando frames de: {clave}")
        n = extraer_frames(v, datos.dir_frames / clave, args.fps)
        datos.progreso["extraidos"][clave] = n
        I.escribir_json(datos.dir / "progreso.json", datos.progreso)
        print(f"  {n} frames")

    # --- recortes sin fondo + huellas
    pendientes = [f for lado, v in videos for f in datos.frames_de_video(clave_video(lado, v))
                  if f not in datos.set_hechos]
    total = sum(len(datos.frames_de_video(clave_video(l, v))) for l, v in videos)
    print(f"\nFrames: {total} en total, {total - len(pendientes)} ya hechos, {len(pendientes)} por hacer.")
    if not pendientes:
        print("Todo listo. Ahora:  python biblioteca_lados.py revisar")
        return

    det = modelos.detector()
    seg = modelos.segmentador()
    emb = modelos.embebedor()
    t0, hechos_sesion, nuevas = time.time(), 0, 0
    try:
        for rel in pendientes:
            ruta = datos.dir_frames / rel
            try:
                im = I.abrir_imagen(ruta)
            except Exception as e:
                print(f"  [AVISO] No se pudo abrir {rel}: {e}")
                continue
            ds = [d for d in det.detectar(im) if (d["box"][3] - d["box"][1]) >= args.min_alto * im.height]
            ds.sort(key=lambda d: d["box"][0])  # de izquierda a derecha
            if seg is not None and ds:
                for d, p in zip(ds, seg.siluetas(im, [d["box"] for d in ds])):
                    d["poli"] = p
            elif args.mascara == "no":
                for d in ds:
                    d["poli"] = None

            grises, nuevos = [], []
            for k, d in enumerate(ds):
                rgba = I.recorte_rgba(im, d["box"], d["poli"])
                gris = I.rgba_a_gris(rgba) or rgba.convert("RGB")
                png_rel = rel[:-4] + f"_v{k}.png"
                destino = datos.dir_recortes / png_rel
                destino.parent.mkdir(parents=True, exist_ok=True)
                rgba.save(destino)
                x1, _, x2, _ = d["box"]
                nuevos.append({
                    "id": len(datos.dets) + len(nuevos), "video": rel.rsplit("/", 1)[0], "frame": rel, "k": k,
                    "box": [round(v, 1) for v in d["box"]], "conf": round(d["conf"], 3),
                    "borde": bool(x1 <= 3 or x2 >= im.width - 3),
                    "ancho": im.width, "alto": im.height, "png": png_rel,
                })
                grises.append(gris)
            if grises:
                datos.E = np.concatenate([datos.E, emb(grises)])
                datos.dets.extend(nuevos)
                nuevas += len(nuevos)
            datos.progreso["hechos"].append(rel)
            datos.set_hechos.add(rel)
            hechos_sesion += 1
            del im, grises

            if hechos_sesion % 10 == 0 or hechos_sesion == len(pendientes):
                datos.guardar()
                vel = (time.time() - t0) / hechos_sesion
                falta = (len(pendientes) - hechos_sesion) * vel / 60
                print(f"  {hechos_sesion}/{len(pendientes)} frames · {nuevas} vacas recortadas · "
                      f"{vel:.1f} s/frame · faltan ~{falta:.0f} min")
            if args.pausa > 0:
                time.sleep(args.pausa)
    except KeyboardInterrupt:
        print("\nCortado. Se guarda el avance...")
    finally:
        datos.guardar()
    hechos = sum(f in datos.set_hechos for lado, v in videos for f in datos.frames_de_video(clave_video(lado, v)))
    print(f"Listo por ahora: {hechos}/{total} frames de estos videos, {len(datos.dets)} recortes en total.")
    print("Para identificar:  python biblioteca_lados.py revisar")


# ----------------------------------------------------------------------------- 2. pasadas

def armar_pasadas(datos, clave, max_hueco=2, iou_min=0.1, sim_min=0.62, min_frames=3):
    """Une detecciones de frames seguidos que son la misma vaca (se superponen y se parecen)."""
    por_frame = defaultdict(list)
    for d in datos.dets:
        if d["video"] == clave:
            por_frame[d["frame"]].append(d["id"])
    pasadas, activas = [], []
    for pos, fr in enumerate(datos.frames_de_video(clave)):
        ids = por_frame.get(fr, [])
        activas = [a for a in activas if pos - a["ult"] <= max_hueco + 1]
        pares = []
        for j, did in enumerate(ids):
            for ai, a in enumerate(activas):
                sup = iou(datos.dets[did]["box"], a["box"])
                sim = float(datos.E[did] @ a["emb"])
                if sup >= iou_min and sim >= sim_min:
                    pares.append((sup + sim, j, ai))
        pares.sort(reverse=True)
        usados_d, usados_a = set(), set()
        for _, j, ai in pares:
            if j in usados_d or ai in usados_a:
                continue
            usados_d.add(j)
            usados_a.add(ai)
            a, did = activas[ai], ids[j]
            pasadas[a["p"]]["dets"].append(did)
            a["box"], a["ult"] = datos.dets[did]["box"], pos
            e = a["emb"] + datos.E[did]
            a["emb"] = e / (np.linalg.norm(e) + 1e-9)
        for j, did in enumerate(ids):
            if j in usados_d:
                continue
            pasadas.append({"id": f"{clave}#{len(pasadas) + 1:03d}", "video": clave, "dets": [did]})
            activas.append({"p": len(pasadas) - 1, "box": datos.dets[did]["box"], "ult": pos,
                            "emb": datos.E[did].copy()})
    # solo sirven las pasadas de al menos `min_frames` frames donde la vaca se ve entera alguna vez
    return [p for p in pasadas if len(p["dets"]) >= min_frames
            and any(not datos.dets[i]["borde"] for i in p["dets"])]


# ----------------------------------------------------------------------------- 3. revisar

class Referencias:
    """Usa la misma lógica de referencias que identificador.py (sin fondo aprobadas + originales)."""
    _listar_referencias = I.Identificador._listar_referencias
    _imagen_referencia = I.Identificador._imagen_referencia
    _cargar_referencias = I.Identificador._cargar_referencias
    completar_siluetas = I.Identificador.completar_siluetas
    descansar = I.Identificador.descansar

    def __init__(self, args, modelos):
        self.args = args
        self._modelos = modelos
        self.dir_sf = Path(args.sin_fondo)
        self.dir_orig = Path(args.originales)
        self.dir_bib = None        # acá NO se usan los recortes de la biblioteca
        self.sin_biblioteca = True  # (se están armando con esto mismo: no compararse consigo misma)
        self.dir_trabajo = Path(args.trabajo)
        self.emb = modelos.embebedor()
        self.galeria = I.Galeria(self.emb.dim)
        self._cargar_referencias()

    @property
    def detector(self):
        return self._modelos.detector()

    @property
    def segmentador(self):
        return self._modelos.segmentador()


class Revisor:
    def __init__(self, args):
        limitar_hilos(args.hilos)
        self.args = args
        self.lock = threading.RLock()
        self.datos = Datos(args.trabajo)
        self.dir_bib = Path(args.biblioteca)
        self.dir_orig = Path(args.originales)
        self.refs = Referencias(args, Modelos(args))
        self.ref_meta = self.refs.galeria.ref_meta
        self.ref_E = self.refs.galeria.ref_emb
        self.ruta_pasadas = self.datos.dir / "pasadas.json"
        self.pasadas = I.leer_json(self.ruta_pasadas, {})          # clave video -> [pasadas]
        self.etiquetas = I.leer_json(self.datos.dir / "etiquetas.json", {})
        self.excluidas = set(I.leer_json(self.datos.dir / "excluidas.json", []))
        self.grupos = I.leer_json(args.grupos, {})
        if self.grupos:
            print(f"Orden de las tandas cargado de {args.grupos}: "
                  f"{sum(len(v) for v in self.grupos.get('tandas', {}).values())} vacas en "
                  f"{len(self.grupos.get('tandas', {}))} tandas.")
        else:
            print(f"[AVISO] No encontré {args.grupos}: se compara contra todas las vacas, sin usar el orden.")
        self._t = {}
        self._cache_img = {}
        self._galeria = None
        self.actualizar_videos()

    # ---------- videos y pasadas

    def actualizar_videos(self):
        with self.lock:
            if getattr(self, "videos", None) and all(v["listo"] for v in self.videos):
                return  # ya está todo cargado
            try:
                self.datos = Datos(self.args.trabajo)
            except Exception as e:  # "preparar" justo estaba guardando: se usa lo que había
                print(f"  [AVISO] No se pudo releer el avance ({e}); se reintenta en la próxima recarga.")
                if not getattr(self, "videos", None):
                    raise
            self.videos = []
            for lado, v in videos_de(self.args.lados):
                clave = clave_video(lado, v)
                listo = self.datos.video_listo(clave)
                if listo and clave not in self.pasadas:
                    self.pasadas[clave] = armar_pasadas(self.datos, clave, min_frames=self.args.min_frames)
                    I.escribir_json(self.ruta_pasadas, self.pasadas)
                self.videos.append({"clave": clave, "lado": lado, "nombre": v.stem, "listo": listo})
            self.por_id = {p["id"]: p for ps in self.pasadas.values() for p in ps}
            self._galeria = None

    def dets_utiles(self, p):
        """Recortes que se usan para comparar: no excluidos, enteros (no cortados en el borde) y
        grandes (>= la mitad del más grande de la pasada = la vaca de costado, no alejándose)."""
        ids = [i for i in p["dets"] if i not in self.excluidas]
        ids = [i for i in ids if not self.datos.dets[i]["borde"]] or ids
        if not ids:
            return ids
        maximo = max(area(self.datos.dets[i]["box"]) for i in ids)
        return [i for i in ids if area(self.datos.dets[i]["box"]) >= 0.5 * maximo]

    # ---------- galería y ranking

    def galeria(self):
        """Matriz con referencias + recortes de pasadas ya confirmadas."""
        if self._galeria is None:
            filas, vacas, origen, pasada = [self.ref_E], [m["vaca"] for m in self.ref_meta], \
                [("r", i) for i in range(len(self.ref_meta))], [None] * len(self.ref_meta)
            for pid, et in self.etiquetas.items():
                p = self.por_id.get(pid)
                if p is None or et.get("accion") != "vaca" or self.es_duplicado(p["video"]):
                    continue
                ids = self.dets_utiles(p)
                if not ids:
                    continue
                filas.append(self.datos.E[ids])
                vacas += [et["vaca"]] * len(ids)
                origen += [("c", i) for i in ids]
                pasada += [pid] * len(ids)
            M = np.concatenate(filas).astype(np.float32)
            lista = sorted(set(vacas), key=I.orden_natural)
            cols = defaultdict(list)
            for c, v in enumerate(vacas):
                cols[v].append(c)
            self._galeria = {"M": M, "vacas": lista, "cols": {v: np.array(c) for v, c in cols.items()},
                             "origen": origen, "pasada": np.array(pasada, dtype=object)}
        return self._galeria

    # ---------- tandas, secuencias y orden

    def orden_de(self, clave):
        """Cómo se usa el orden para un video, o None si no hay datos:
          tanda (lado A):     solo se proponen vacas de la tanda; lugar = 1..n dentro de la tanda.
          secuencia (lado B): se proponen todas las vacas; lugar = nº de recorte B; los videos de
                              la secuencia se encadenan como si fueran uno solo."""
        info = self.grupos.get("videos", {}).get(clave)
        if not info:
            return None
        if "secuencia" in info:
            sec = self.grupos.get("secuencias", {}).get(info["secuencia"])
            if not sec:
                return None
            lugar = {v: float(n) for v, n in sec["posiciones"].items()}
            return {"tipo": "secuencia", "nombre": info["secuencia"], "lugar": lugar,
                    "lista": sorted(lugar, key=lugar.get), "videos": sec["videos"],
                    "restringir": False, "usar_orden": True}
        lista = self.grupos.get("tandas", {}).get(str(info.get("tanda")))
        if not lista:
            return None
        return {"tipo": "tanda", "nombre": str(info["tanda"]), "lugar": {v: n + 1 for n, v in enumerate(lista)},
                "lista": lista, "videos": [clave], "restringir": True,
                "usar_orden": bool(info.get("usar_orden"))}

    def t_pasada(self, p):
        """Segundo en que la vaca se ve más grande (de costado) en esa pasada."""
        if p["id"] not in self._t:
            ids = self.dets_utiles(p) or p["dets"]
            i = max(ids, key=lambda i: area(self.datos.dets[i]["box"]))
            self._t[p["id"]] = tiempo_frame(self.datos.dets[i]["frame"])
        return self._t[p["id"]]

    def duracion(self, clave):
        frames = self.datos.frames_de_video(clave)
        return tiempo_frame(frames[-1]) + 1.0 / self.args.fps if frames else 0.0

    def posicion_esperada(self, p, o):
        """Lugar esperado (en la numeración de `o["lugar"]`) de la vaca de esta pasada, y su margen.
        Usa el tiempo de la pasada y las pasadas ya confirmadas como anclas. En una secuencia el
        tiempo se acumula de un video al siguiente y sirven anclas de cualquier video de la secuencia."""
        offset, total = {}, 0.0
        for v in o["videos"]:
            offset[v] = total
            total += self.duracion(v)
        if p["video"] not in offset:
            return None
        ps = [x for v in o["videos"] for x in self.pasadas.get(v, [])]
        tg = lambda x: offset[x["video"]] + self.t_pasada(x)  # tiempo global
        lugares = o["lugar"].values()
        lmin, lmax = (1, max(lugares)) if o["tipo"] == "secuencia" else (1, len(o["lista"]))
        if o["tipo"] == "tanda":
            ts = [tg(x) for x in ps]
            t0, t1 = min(ts), max(ts)
        else:
            t0, t1 = 0.0, total
        ritmo = (t1 - t0) / max(lmax - lmin, 1) or 1.0  # segundos entre vaca y vaca
        t = tg(p)
        anclas = []
        for x in ps:
            et = self.etiquetas.get(x["id"])
            if x["id"] != p["id"] and et and et.get("accion") == "vaca" and et.get("vaca") in o["lugar"]:
                anclas.append((tg(x), o["lugar"][et["vaca"]]))
        anclas.sort()
        antes = [a for a in anclas if a[0] <= t]
        despues = [a for a in anclas if a[0] > t]
        if antes and despues:
            (ta, la), (tb, lb) = antes[-1], despues[0]
            e = la + (lb - la) * (t - ta) / (tb - ta) if tb > ta else la
            dist = min(abs(e - la), abs(lb - e))
        elif antes:
            ta, la = antes[-1]
            e = la + (t - ta) / ritmo
            dist = abs(e - la)
        elif despues:
            tb, lb = despues[0]
            e = lb - (tb - t) / ritmo
            dist = abs(lb - e)
        else:
            e, dist = lmin + (t - t0) / ritmo, (4 if o["tipo"] == "tanda" else 15)
        e = min(max(e, float(lmin)), float(lmax))
        if o["tipo"] == "tanda":
            return e, min(1.5 + 0.4 * dist, 4.0)
        return e, min(2.0 + 0.35 * dist, 8.0)  # lado B: más margen (alguna antes o alguna después)

    def ranking(self, p, k=5, todas=False):
        """[(puntaje, vaca, origen, similitud, lugar)] de mayor a menor. Con tanda: solo vacas de
        la tanda (salvo todas=True). Con orden: se suma un extra a las cercanas al lugar esperado."""
        g = self.galeria()
        ids = self.dets_utiles(p)
        if not ids or not g["vacas"]:
            return []
        o = self.orden_de(p["video"])
        vacas = [v for v in o["lista"] if v in g["cols"]] if o and o["restringir"] and not todas else g["vacas"]
        esperado = self.posicion_esperada(p, o) if o and o["usar_orden"] else None
        S = self.datos.E[ids] @ g["M"].T
        S[:, g["pasada"] == p["id"]] = -np.inf  # no compararse consigo misma
        res = []
        for v in vacas:
            sub = S[:, g["cols"][v]]
            mejor_fila = sub.max(axis=1)
            mejor_fila = mejor_fila[np.isfinite(mejor_fila)]
            if not len(mejor_fila):
                continue
            col = g["cols"][v][int(np.argmax(np.where(np.isfinite(sub), sub, -9).mean(axis=0)))]
            sim = float(mejor_fila.mean())
            lugar = o["lugar"].get(v) if o else None
            extra = 0.0
            if esperado is not None and lugar is not None:
                e, sigma = esperado
                extra = PESO_ORDEN * math.exp(-0.5 * ((lugar - e) / sigma) ** 2)
            res.append((sim + extra, v, g["origen"][col], sim, lugar))
        res.sort(key=lambda r: -r[0])
        return res if k is None else res[:k]

    # ---------- biblioteca

    def es_duplicado(self, clave):
        """True si el video es copia exacta de otro (grupos.json: "duplicado_de")."""
        return bool(self.grupos.get("videos", {}).get(clave, {}).get("duplicado_de"))

    def _exportar(self, pid):
        et = self.etiquetas.get(pid, {})
        for ruta in et.pop("exportadas", []):
            Path(ruta).unlink(missing_ok=True)
        p = self.por_id[pid]
        if et.get("accion") != "vaca" or self.es_duplicado(p["video"]):
            return  # los videos copia no agregan recortes (ya están los del original)
        ids = [i for i in p["dets"] if i not in self.excluidas and not self.datos.dets[i]["borde"]]
        ids = sorted(ids, key=lambda i: -area(self.datos.dets[i]["box"]))[:self.args.por_pasada]
        carpeta = self.dir_bib / I.nombre_seguro(et["vaca"])
        carpeta.mkdir(parents=True, exist_ok=True)
        hechas = []
        for i in ids:
            d = self.datos.dets[i]
            lado, video = d["video"].split("/", 1)
            nombre = (f"{I.PREFIJO_BIB}{I.nombre_seguro(lado).replace(' ', '')}_"
                      f"{I.nombre_seguro(video)[:40].strip()}_{Path(d['png']).name}")
            destino = carpeta / nombre
            shutil.copyfile(self.datos.dir_recortes / d["png"], destino)
            hechas.append(str(destino))
        et["exportadas"] = hechas

    def _guardar(self):
        I.escribir_json(self.datos.dir / "etiquetas.json", self.etiquetas)
        I.escribir_json(self.datos.dir / "excluidas.json", sorted(self.excluidas))

    # ---------- acciones

    def etiquetar(self, pid, accion, vaca=None):
        with self.lock:
            if pid not in self.por_id:
                raise ValueError("Esa pasada no existe.")
            if accion not in I.ACCIONES:
                raise ValueError(f"Acción inválida: {accion}")
            vaca = (vaca or "").strip()
            if accion == "vaca" and not vaca:
                raise ValueError("Falta el número de vaca.")
            previo = self.etiquetas.get(pid)
            if previo:
                for ruta in previo.get("exportadas", []):
                    Path(ruta).unlink(missing_ok=True)
            if accion == "borrar":
                self.etiquetas.pop(pid, None)
            else:
                reg = {"accion": accion, "ts": round(time.time())}
                if accion == "vaca":
                    self._galeria = None
                    pos = next((n + 1 for n, r in enumerate(self.ranking(self.por_id[pid], k=None))
                                if r[1] == vaca), 0)
                    reg.update(vaca=vaca, posicion=pos)
                self.etiquetas[pid] = reg
                self._exportar(pid)
            self._galeria = None
            self._guardar()
            return self.video_json(self.por_id[pid]["video"])

    def excluir(self, did, si):
        with self.lock:
            did = int(did)
            if si:
                self.excluidas.add(did)
            else:
                self.excluidas.discard(did)
            d = self.datos.dets[did]
            pid = next((p["id"] for p in self.pasadas.get(d["video"], []) if did in p["dets"]), None)
            if pid and pid in self.etiquetas:
                self._exportar(pid)
            self._t.pop(pid, None)
            self._galeria = None
            self._guardar()
            return self.video_json(d["video"])

    # ---------- json para la página

    def _estadisticas(self):
        s = {"n": 0, "top1": 0, "top5": 0, "nuevas": 0, "descartadas": 0}
        for pid, et in self.etiquetas.items():
            if pid not in self.por_id:
                continue
            if et["accion"] == "vaca":
                p = et.get("posicion", 0)
                if p is None:  # etiqueta recuperada: no se sabe en qué lugar estaba
                    continue
                s["n"] += 1
                s["top1"] += int(p == 1)
                s["top5"] += int(1 <= p <= 5)
                s["nuevas"] += int(p == 0)
            elif et["accion"] == "descartar":
                s["descartadas"] += 1
        return s

    def resumen(self):
        with self.lock:
            videos = []
            for v in self.videos:
                ps = self.pasadas.get(v["clave"], [])
                videos.append({**v, "pasadas": len(ps),
                               "revisadas": sum(1 for p in ps if p["id"] in self.etiquetas)})
            vacas_bib = sorted({et["vaca"] for et in self.etiquetas.values() if et.get("accion") == "vaca"},
                               key=I.orden_natural)
            return {"videos": videos, "vacas": self.galeria()["vacas"], "estadisticas": self._estadisticas(),
                    "en_biblioteca": len(vacas_bib)}

    def video_json(self, clave):
        with self.lock:
            o = self.orden_de(clave)
            tanda = None
            if o:
                asignadas = {self.etiquetas[p["id"]]["vaca"] for v in o["videos"] for p in self.pasadas.get(v, [])
                             if self.etiquetas.get(p["id"], {}).get("accion") == "vaca"}
                tanda = {"numero": o["nombre"], "tipo": o["tipo"], "usar_orden": o["usar_orden"],
                         "orden": [{"lugar": o["lugar"][v], "vaca": v, "asignada": v in asignadas}
                                   for v in o["lista"]]}
            salida = []
            for p in self.pasadas.get(clave, []):
                et = self.etiquetas.get(p["id"])
                dets = [{"id": i, "frame": self.datos.dets[i]["frame"].rsplit("/", 1)[1],
                         "box": self.datos.dets[i]["box"], "ancho": self.datos.dets[i]["ancho"],
                         "alto": self.datos.dets[i]["alto"], "borde": self.datos.dets[i]["borde"],
                         "excluida": i in self.excluidas} for i in p["dets"]]
                cands = [{"vaca": v, "score": round(s, 4), "sim": round(sim, 4), "lugar": lugar, "t": t, "id": i}
                         for s, v, (t, i), sim, lugar in self.ranking(p)]
                esperado = None
                if o and o["usar_orden"] and p["dets"]:
                    pe = self.posicion_esperada(p, o)
                    esperado = round(pe[0], 1) if pe else None
                salida.append({"id": p["id"], "dets": dets, "candidatos": cands, "esperado": esperado,
                               "segundo": round(self.t_pasada(p), 1),
                               "etiqueta": {k: et.get(k) for k in ("accion", "vaca", "posicion")} if et else None})
            return {"clave": clave, "tanda": tanda, "pasadas": salida, "resumen": self.resumen()}

    def foto_lado(self):
        """{vaca: {"A": índice de referencia, "B": índice}} para mostrar la foto del lado que corresponde."""
        if getattr(self, "_foto_lado", None) is None:
            fotos = {}
            for i, m in enumerate(self.ref_meta):
                nombre = Path(m["rel"]).stem
                if nombre.endswith("_B"):
                    lado = "B"
                elif nombre.endswith("_A"):
                    lado = "A"
                else:  # vlcsnap: las capturas del lado A miden 832x464, las del B 1024x576
                    try:
                        with Image.open(self.dir_orig / m["rel"]) as im:
                            lado = "B" if im.size[0] >= 1000 else "A"
                    except OSError:
                        lado = "A"
                fotos.setdefault(m["vaca"], {}).setdefault(lado, i)
            self._foto_lado = fotos
        return self._foto_lado

    def todas_json(self, pid):
        """Todas las vacas en orden de paso (número de recorte B), con su similitud con esta pasada."""
        with self.lock:
            p = self.por_id.get(pid)
            if p is None:
                raise ValueError("Esa pasada no existe.")
            sim = {r[1]: r[3] for r in self.ranking(p, k=None, todas=True)}
            sec = next(iter(self.grupos.get("secuencias", {}).values()), {})
            lugar = {v: float(n) for v, n in sec.get("posiciones", {}).items()}
            lado = "B" if p["video"].upper().startswith("LADO B") else "A"
            fotos = self.foto_lado()
            o = self.orden_de(p["video"])
            esperado = None
            if o and o["usar_orden"]:
                pe = self.posicion_esperada(p, o)
                if pe:
                    # en una tanda el lugar es 1..n: se pasa a la numeración global (recorte B)
                    esperado = pe[0] if o["tipo"] == "secuencia" else \
                        lugar.get(o["lista"][min(int(round(pe[0])) - 1, len(o["lista"]) - 1)])
            asignadas = {et["vaca"] for et in self.etiquetas.values() if et.get("accion") == "vaca"}
            vacas = sorted(fotos, key=lambda v: (lugar.get(v, 10 ** 6), I.orden_natural(v)))
            return {"esperado": esperado, "lado": lado, "vacas": [
                {"vaca": v, "lugar": lugar.get(v), "sim": round(sim[v], 4) if v in sim else None,
                 "ref": fotos[v].get(lado, next(iter(fotos[v].values()))), "con_biblioteca": v in asignadas}
                for v in vacas]}

    # ---------- imágenes

    def img_det(self, did, lado=220):
        clave = ("d", did, lado)
        if clave not in self._cache_img:
            d = self.datos.dets[did]
            with Image.open(self.datos.dir_recortes / d["png"]) as im:
                im = im.convert("RGBA")
            self._cache_img[clave] = I.a_jpg(I.rgba_a_gris(im) or im.convert("RGB"), lado)
        return self._cache_img[clave]

    def img_ref(self, tipo, ident):
        if tipo == "c":
            return self.img_det(int(ident), 260)
        clave = ("r", ident)
        if clave not in self._cache_img:
            self._cache_img[clave] = I.a_jpg(self.refs._imagen_referencia(self.ref_meta[int(ident)]), 260)
        return self._cache_img[clave]


def revisar(args):
    from flask import Flask, Response, abort, jsonify, request, send_file

    html = Path(__file__).with_name("interfaz_lados.html")
    if not html.exists():
        print("[ERROR] Falta interfaz_lados.html al lado de este script.")
        sys.exit(1)
    rev = Revisor(args)
    listos = sum(v["listo"] for v in rev.videos)
    print(f"Videos listos para revisar: {listos} de {len(rev.videos)}")
    if not listos:
        print("Todavía no terminó ningún video. Corré primero:  python biblioteca_lados.py preparar")

    web = Flask(__name__)

    def jpg(datos):
        return Response(datos, mimetype="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @web.get("/")
    def inicio():
        return send_file(html)

    @web.get("/api/resumen")
    def api_resumen():
        rev.actualizar_videos()  # por si "preparar" terminó más videos mientras tanto
        return jsonify(rev.resumen())

    @web.get("/api/video")
    def api_video():
        return jsonify(rev.video_json(request.args.get("v", "")))

    @web.get("/api/todas")
    def api_todas():
        try:
            return jsonify(rev.todas_json(request.args.get("pasada", "")))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @web.post("/api/etiquetar")
    def api_etiquetar():
        d = request.get_json(force=True)
        try:
            return jsonify(rev.etiquetar(d.get("pasada"), d.get("accion"), d.get("vaca")))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @web.post("/api/excluir")
    def api_excluir():
        d = request.get_json(force=True)
        try:
            return jsonify(rev.excluir(d.get("det"), bool(d.get("excluir"))))
        except (ValueError, IndexError, TypeError) as e:
            return jsonify({"error": str(e)}), 400

    @web.get("/img/det/<int:did>")
    def img_det(did):
        if not 0 <= did < len(rev.datos.dets):
            abort(404)
        return jpg(rev.img_det(did))

    @web.get("/img/frame/<int:did>")
    def img_frame(did):
        if not 0 <= did < len(rev.datos.dets):
            abort(404)
        return send_file((rev.datos.dir_frames / rev.datos.dets[did]["frame"]).resolve(), max_age=3600)

    @web.get("/img/ref")
    def img_ref():
        try:
            return jpg(rev.img_ref(request.args.get("t"), request.args.get("id", "")))
        except (IndexError, ValueError, KeyError):
            abort(404)

    url = f"http://127.0.0.1:{args.puerto}"
    print(f"\nListo. Abrí {url} en el navegador (Ctrl+C para cortar).")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    web.run(host="127.0.0.1", port=args.puerto, threaded=True)


# ----------------------------------------------------------------------------- estado

def estado(args):
    datos = Datos(args.trabajo)
    etiquetas = I.leer_json(datos.dir / "etiquetas.json", {})
    pasadas = I.leer_json(datos.dir / "pasadas.json", {})
    print(f"{'Video':70} {'frames':>8} {'hechos':>7} {'pasadas':>8} {'revisadas':>10}")
    for lado, v in videos_de(args.lados):
        clave = clave_video(lado, v)
        fr = datos.frames_de_video(clave)
        ps = pasadas.get(clave, [])
        print(f"{clave[:70]:70} {len(fr):>8} {sum(f in datos.set_hechos for f in fr):>7} "
              f"{len(ps):>8} {sum(p['id'] in etiquetas for p in ps):>10}")
    vacas = {e["vaca"] for e in etiquetas.values() if e.get("accion") == "vaca"}
    print(f"\nRecortes de vacas: {len(datos.dets)} · Vacas en la Biblioteca: {len(vacas)}")


def main():
    p = argparse.ArgumentParser(description="Biblioteca de vacas desde los videos de costado.")
    p.add_argument("accion", choices=["preparar", "revisar", "estado"])
    p.add_argument("--lados", nargs="+", default=["LADO A", "LADO B"])
    p.add_argument("--trabajo", default="lados")
    p.add_argument("--biblioteca", default="Asignaciones (Verificado)")
    p.add_argument("--sin-fondo", default="Asignaciones_sin_fondo")
    p.add_argument("--originales", default="Asignaciones (Verificado)")
    p.add_argument("--grupos", default="grupos.json")
    p.add_argument("--fps", type=float, default=3)
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--min-alto", type=float, default=0.25)
    p.add_argument("--por-pasada", type=int, default=4)
    p.add_argument("--min-frames", type=int, default=3)
    p.add_argument("--yolo", default="yolo11s.pt")
    p.add_argument("--sam", default="sam2.1_t.pt")
    p.add_argument("--mascara", choices=["sam", "yolo", "no"], default="sam")
    p.add_argument("--hilos", type=int, default=2)
    p.add_argument("--pausa", type=float, default=0.1)
    p.add_argument("--puerto", type=int, default=5003)
    args = p.parse_args()
    {"preparar": preparar, "revisar": revisar, "estado": estado}[args.accion](args)


if __name__ == "__main__":
    main()
