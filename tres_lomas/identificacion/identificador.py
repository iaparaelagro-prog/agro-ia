"""
identificador.py
Identificación asistida de vacas por lotes (versión liviana, con referencias SIN FONDO).

Qué hace:
  1. Lee las fotos de referencia de cada vaca:
       - usa la versión sin fondo (Asignaciones_sin_fondo/<vaca>/<foto>.png) si está aprobada
         ("bien" en revision.json);
       - si una foto todavía no está aprobada, usa la original (Asignaciones (Verificado)) y le
         quita el fondo en el momento con YOLO-seg, para no quedarse sin esa foto.
  2. Toma los frames de a lotes (30 por defecto). En cada frame detecta las vacas con YOLO
     (yolo11s), saca la silueta de cada una con SAM (sam2.1_t.pt) y le tapa el fondo con gris,
     igual que las referencias. Así se compara "vaca contra vaca" y no "potrero contra potrero".
  3. Para cada vaca detectada calcula una huella visual (DINOv2) y propone las 5 más probables.
  4. Abre una página web donde elegís cuál es. Cada vaca que confirmás se suma a la galería al
     instante, así el identificador mejora para el resto del lote y los siguientes.
  5. Al pasar al lote siguiente, la huella se "afina" de nuevo (LDA) con todo lo confirmado:
     cada lote de 20 entrena al identificador para los 20 siguientes.
  6. Una vaca no puede estar dos veces en el mismo frame: si ya la asignaste a una detección,
     no se propone para las otras de ese frame.

Modo liviano (para que la compu no se apague):
  - una imagen por vez, 2 hilos de CPU por defecto, pequeña pausa entre imágenes,
  - yolo11s + SAM tiny + DINOv2 small (cada lote de 30 frames tarda ~2 minutos).
  - Nota: yolo11n-seg casi no ve las vacas en esta toma desde arriba; por eso se usa yolo11s.

Uso (desde la carpeta desfile_tres_lomas):
    python identificador.py

Opciones:
    --frames       carpeta con los frames                     (default: frames_desfile_camaras)
    --sin-fondo    carpeta con las referencias sin fondo      (default: Asignaciones_sin_fondo)
    --originales   carpeta con las fotos originales por vaca  (default: "Asignaciones (Verificado)")
    --biblioteca   carpeta APARTE con recortes extra por vaca (default: ninguna). Normalmente no hace
                   falta: biblioteca_lados.py guarda los recortes como bib_*.png dentro de cada
                   carpeta de Asignaciones, y se usan solos.
    --trabajo      carpeta donde se guarda el avance          (default: identificacion)
    --lote         frames por lote                            (default: 20)
    --conf         confianza mínima de YOLO                   (default: 0.25)
    --min-lado     descarta solas las vacas más chicas que esto, en píxeles (default: 70;
                   son las del fondo, que no se pueden identificar)
    --clases       qué acepta el detector: animales | vaca   (default: animales; desde arriba
                   YOLO confunde vacas con pájaros, ovejas, caballos...)
    --imgsz        resolución con la que mira YOLO           (default: 1280; detecta más vacas)
    --sin-afinar   no ajustar la huella (por defecto se re-entrena en cada lote con lo confirmado)
    --yolo         modelo YOLO para detectar                  (default: yolo11s.pt)
    --mascara      cómo quitar el fondo en los frames: sam | yolo | no   (default: sam)
                   (yolo = usa la máscara del modelo si es -seg; no = recorte con fondo)
    --sam          modelo SAM                                 (default: sam2.1_t.pt)
    --hilos        hilos de CPU a usar                        (default: 2)
    --pausa        segundos de descanso entre imágenes        (default: 0.1)
    --puerto       puerto de la página                        (default: 5000)

Todo el avance queda en la carpeta de trabajo; podés cortar con Ctrl+C y seguir otro día.
"""

import argparse
import os
import sys

# Limitar hilos ANTES de importar numpy / torch / opencv
def _hilos_desde_argv(defecto=2):
    for i, a in enumerate(sys.argv):
        if a == "--hilos" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--hilos="):
            return a.split("=", 1)[1]
    return str(defecto)

_HILOS = _hilos_desde_argv()
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_var] = _HILOS

import csv
import hashlib
import io
import json
import re
import shutil
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

EXT_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
CLASE_VACA = 19  # "cow" en COCO
# Desde arriba YOLO suele confundir vacas con "pájaro", "oveja", "caballo"...: en el campo se aceptan
# todas las clases de animales de COCO (14 = bird ... 23 = giraffe)
CLASES_ANIMALES = list(range(14, 24))
MODELO_EMB = "vit_small_patch14_dinov2.lvd142m"
TAM_EMB = 224
GRIS = (124, 116, 104)  # color de fondo neutro (el mismo para referencias y frames)
MEDIA = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DESV = np.array([0.229, 0.224, 0.225], dtype=np.float32)
ACCIONES = {"vaca", "descartar", "nose", "borrar"}
PREFIJO_BIB = "bib_"  # recortes sin fondo agregados a cada carpeta de Asignaciones por biblioteca_lados.py

# Bloques: la misma vaca en frames seguidos (casi en el mismo lugar y con la misma huella) se
# etiqueta una sola vez; el resto del bloque toma la etiqueta solo.
BLOQUE_IOU = 0.3      # superposición mínima del recuadro con el del frame anterior
BLOQUE_SIM = 0.75     # parecido mínimo de la huella con la del frame anterior
BLOQUE_HUECO = 1.5    # segundos máximos entre dos frames para seguir el bloque
NOVEDAD = 0.92        # un recorte automático se suma a la galería solo si aporta una vista nueva
VERSION_CACHE = 2


# ----------------------------------------------------------------------------- utilidades

def orden_natural(texto):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(texto))]


def leer_json(ruta, defecto):
    """Lee un JSON. Si está dañado (p. ej. por un apagón), usa la copia .bak y avisa."""
    ruta = Path(ruta)
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defecto
    except (json.JSONDecodeError, UnicodeDecodeError):
        bak = ruta.with_suffix(ruta.suffix + ".bak")
        try:
            datos = json.loads(bak.read_text(encoding="utf-8"))
            print(f"[AVISO] {ruta} estaba dañado: se usa la copia de seguridad {bak.name}")
            return datos
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            print(f"[AVISO] {ruta} está dañado y no hay copia sana: se arranca vacío.")
            return defecto


def escribir_json(ruta, datos):
    """Guarda de forma segura: escribe a .tmp, lo fuerza al disco, guarda la versión anterior
    como .bak (si estaba sana) y recién ahí reemplaza. Así un apagón no deja el archivo en cero."""
    ruta = Path(ruta)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    if ruta.exists():
        try:
            json.loads(ruta.read_text(encoding="utf-8"))  # solo se respalda si está sano
            shutil.copyfile(ruta, ruta.with_suffix(ruta.suffix + ".bak"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    tmp.replace(ruta)


def abrir_imagen(ruta):
    with Image.open(ruta) as im:
        im = ImageOps.exif_transpose(im)
        return im.convert("RGB")


def caja_con_margen(box, ancho, alto, margen=0.05):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    return (int(max(0, x1 - w * margen)), int(max(0, y1 - h * margen)),
            int(min(ancho, x2 + w * margen)), int(min(alto, y2 + h * margen)))


def tiempo_de(nombre):
    """Segundo del video a partir del nombre del frame (…_t00143s297.jpg -> 143.297)."""
    m = re.search(r"_t(\d+)s(\d+)", nombre)
    return int(m.group(1)) + int(m.group(2)) / 1000 if m else 0.0


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def recortar(im, box, margen=0.05):
    return im.crop(caja_con_margen(box, im.width, im.height, margen))


def recortar_con_mascara(im, box, poligono, margen=0.05):
    """Recorta la vaca y pinta de gris todo lo que no está dentro de su silueta."""
    c = caja_con_margen(box, im.width, im.height, margen)
    rec = im.crop(c)
    if poligono is None or len(poligono) < 3:
        return rec
    pts = [(float(x) - c[0], float(y) - c[1]) for x, y in poligono]
    mascara = Image.new("L", rec.size, 0)
    ImageDraw.Draw(mascara).polygon(pts, fill=255)
    mascara = mascara.filter(ImageFilter.MaxFilter(5))  # agranda un poquito el borde
    fondo = Image.new("RGB", rec.size, GRIS)
    return Image.composite(rec, fondo, mascara)


def recorte_rgba(im, box, poligono, margen=0.05):
    """Recorte de la vaca como PNG transparente (todo lo que no es vaca queda invisible)."""
    c = caja_con_margen(box, im.width, im.height, margen)
    rec = im.crop(c).convert("RGBA")
    if poligono is not None and len(poligono) >= 3:
        pts = [(float(x) - c[0], float(y) - c[1]) for x, y in poligono]
        mascara = Image.new("L", rec.size, 0)
        ImageDraw.Draw(mascara).polygon(pts, fill=255)
        rec.putalpha(mascara.filter(ImageFilter.MaxFilter(5)))
    return rec


def rgba_a_gris(im):
    """RGBA -> RGB recortado al contorno visible y con fondo gris. None si está vacío."""
    caja = im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    if caja is None:
        return None
    im = im.crop(caja)
    fondo = Image.new("RGB", im.size, GRIS)
    fondo.paste(im, mask=im.getchannel("A"))
    return fondo


def png_sin_fondo_a_rgb(ruta):
    """Abre un PNG transparente, lo recorta al contorno de la vaca y le pone fondo gris.
    Devuelve None si la imagen no tiene transparencia útil."""
    with Image.open(ruta) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
            return None
        im = im.convert("RGBA")
    return rgba_a_gris(im)


def a_jpg(im, lado):
    im = im.copy()
    im.thumbnail((lado, lado))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def nombre_seguro(texto):
    return re.sub(r'[<>:"/\\|?*]', "_", str(texto)).strip() or "sin_nombre"


# ----------------------------------------------------------------------------- modelos

class Detector:
    def __init__(self, modelo, conf, clases=None, imgsz=640):
        from ultralytics import YOLO
        print(f"Cargando detector YOLO ({modelo})...")
        self.yolo = YOLO(modelo)
        self.conf = conf
        self.clases = clases or [CLASE_VACA]
        self.imgsz = imgsz

    def detectar(self, im, conf=None):
        """Una sola imagen PIL RGB. Devuelve [{'box':[x1,y1,x2,y2], 'conf':c, 'poli':[[x,y],...] o None}]"""
        r = self.yolo.predict(im, classes=self.clases, conf=conf or self.conf, imgsz=self.imgsz,
                              agnostic_nms=len(self.clases) > 1, device="cpu", verbose=False)[0]
        cajas = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        polis = r.masks.xy if r.masks is not None else [None] * len(cajas)
        salida = []
        for b, c, p in zip(cajas, confs, polis):
            poli = p.tolist() if p is not None and len(p) >= 3 else None
            salida.append({"box": [float(v) for v in b], "conf": float(c), "poli": poli})
        return salida


class Segmentador:
    """Silueta de cada vaca con SAM, usando como guía la caja que dio YOLO."""

    def __init__(self, modelo):
        from ultralytics import SAM
        print(f"Cargando SAM para quitar el fondo ({modelo})...")
        self.sam = SAM(modelo)

    def siluetas(self, im, cajas):
        """Devuelve un polígono (o None) por cada caja, en el mismo orden."""
        if not cajas:
            return []
        try:
            r = self.sam.predict(im, bboxes=[list(c) for c in cajas], device="cpu", verbose=False)[0]
        except Exception as e:
            print(f"  [AVISO] SAM falló ({e}); se usa el recorte sin quitar el fondo.")
            return [None] * len(cajas)
        if r.masks is None:
            return [None] * len(cajas)
        polis = [p.tolist() if len(p) >= 3 else None for p in r.masks.xy]
        return (polis + [None] * len(cajas))[:len(cajas)]


class Embebedor:
    """Huella visual con DINOv2. Si se le pasa `pesos` (por ejemplo dinov2_vacas.pt, entrenado en Colab
    con entrenar_dinov2.ipynb) usa esa versión ajustada a las vacas; si no, el DINOv2 original.
    `firma` identifica qué modelo calculó las huellas, para no mezclar huellas de modelos distintos."""

    def __init__(self, pesos=None):
        import torch
        import timm
        self.torch = torch
        ruta = Path(pesos) if pesos else None
        if ruta is not None and ruta.is_file():
            print(f"Cargando modelo de identificación ENTRENADO ({ruta.name}, base {MODELO_EMB})...")
            self.modelo = timm.create_model(MODELO_EMB, pretrained=False, num_classes=0, img_size=TAM_EMB)
            estado = torch.load(ruta, map_location="cpu")
            self.modelo.load_state_dict({k: v.float() for k, v in estado.items()})
            with open(ruta, "rb") as f:
                huella_archivo = hashlib.md5(f.read()).hexdigest()[:12]
            self.firma = f"{MODELO_EMB}+{ruta.name}:{huella_archivo}"
        else:
            if ruta is not None:
                print(f"  [AVISO] No encuentro {ruta}: se usa el DINOv2 original.")
            print(f"Cargando modelo de identificación ({MODELO_EMB})...")
            self.modelo = timm.create_model(MODELO_EMB, pretrained=True, num_classes=0, img_size=TAM_EMB)
            self.firma = MODELO_EMB
        self.modelo.eval()
        self.dim = self.modelo.num_features

    def _preparar(self, im):
        im = ImageOps.pad(im.convert("RGB"), (TAM_EMB, TAM_EMB), color=GRIS)
        a = (np.asarray(im, dtype=np.float32) / 255.0 - MEDIA) / DESV
        return a.transpose(2, 0, 1)

    def __call__(self, imagenes, tam_batch=4):
        torch = self.torch
        partes = []
        for i in range(0, len(imagenes), tam_batch):
            x = np.stack([self._preparar(im) for im in imagenes[i:i + tam_batch]])
            x = torch.from_numpy(x)
            with torch.inference_mode():
                f = self.modelo(x) + self.modelo(torch.flip(x, dims=[3]))  # original + espejado
                f = torch.nn.functional.normalize(f, dim=1)
            partes.append(f.numpy().astype(np.float32))
        if not partes:
            return np.zeros((0, self.dim), np.float32)
        return np.concatenate(partes)


# ----------------------------------------------------------------------------- afinado

class Afinador:
    """Ajusta la huella con las vacas ya identificadas (análisis discriminante, LDA): junta las
    fotos de una misma vaca y separa las de vacas distintas. Se recalcula en segundos, así que se
    vuelve a entrenar en cada lote con lo que se va confirmando.
    (Prueba: reconocer el lado B usando solo el lado A pasó de 75% a 91% de acierto en 1° lugar)."""

    def __init__(self, mu, P, n_muestras, n_vacas):
        self.mu, self.P = mu.astype(np.float32), P.astype(np.float32)
        self.n_muestras, self.n_vacas = n_muestras, n_vacas

    @classmethod
    def entrenar(cls, X, y, dims=120, reg=0.5):
        y = np.asarray(y)
        clases = [c for c in np.unique(y) if (y == c).sum() >= 2]
        if len(clases) < 3:
            return None
        sel = np.isin(y, clases)
        X, y = X[sel].astype(np.float64), y[sel]
        d = X.shape[1]
        mu = X.mean(0)
        Sw, Sb = np.zeros((d, d)), np.zeros((d, d))
        for c in clases:
            Xc = X[y == c]
            mc = Xc.mean(0)
            Sw += (Xc - mc).T @ (Xc - mc)
            Sb += len(Xc) * np.outer(mc - mu, mc - mu)
        Sw += reg * np.trace(Sw) / d * np.eye(d)
        L = np.linalg.cholesky(Sw)              # Sw = L Lᵀ
        Li = np.linalg.inv(L)
        w, V = np.linalg.eigh(Li @ Sb @ Li.T)   # problema simétrico equivalente
        k = min(dims, len(clases) - 1)
        P = Li.T @ V[:, np.argsort(-w)[:k]]
        return cls(mu, P, len(X), len(clases))

    def __call__(self, X):
        Z = (X - self.mu) @ self.P
        return (Z / (np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9)).astype(np.float32)


# ----------------------------------------------------------------------------- galería

class Galeria:
    """Embeddings conocidos: fotos de referencia + recortes confirmados a mano."""

    def __init__(self, dim):
        self.dim = dim
        self.ref_emb = np.zeros((0, dim), np.float32)
        self.ref_meta = []   # [{"vaca", "ruta", "tipo", "box", "poli"}]
        self.conf = {}       # clave -> {"emb", "vaca", "ruta"}
        self.afinador = None
        self._cache = None

    def poner_afinador(self, afinador):
        self.afinador = afinador
        self._cache = None

    def poner_referencias(self, emb, meta):
        self.ref_emb, self.ref_meta = emb, meta
        self._cache = None

    def agregar(self, clave, emb, vaca, ruta):
        self.conf[clave] = {"emb": emb, "vaca": vaca, "ruta": str(ruta)}
        self._cache = None

    def quitar(self, clave):
        item = self.conf.pop(clave, None)
        self._cache = None
        return item

    def vacas(self):
        return self._armar()[2]

    def _armar(self):
        if self._cache is None:
            embs = [self.ref_emb]
            meta = [("r", i, m["vaca"]) for i, m in enumerate(self.ref_meta)]
            for k, v in self.conf.items():
                embs.append(v["emb"][None])
                meta.append(("c", k, v["vaca"]))
            M = np.concatenate(embs) if meta else np.zeros((0, self.dim), np.float32)
            if self.afinador is not None and len(M):
                M = self.afinador(M)
            vacas = sorted({m[2] for m in meta}, key=orden_natural)
            idx = {v: [] for v in vacas}
            for col, m in enumerate(meta):
                idx[m[2]].append(col)
            idx = {v: np.array(c) for v, c in idx.items()}
            colclave = {m[1]: col for col, m in enumerate(meta) if m[0] == "c"}
            self._cache = (M, meta, vacas, idx, colclave)
        return self._cache

    def puntajes_grupo(self, Q, excluir_claves=()):
        """Varias fotos de la MISMA vaca (un recorrido) contra la galería: [(puntaje, vaca, (tipo, id))]
        de mayor a menor. Puntaje = promedio, sobre las fotos, del mejor parecido con cada vaca."""
        M, meta, vacas, idx, colclave = self._armar()
        if not vacas or len(Q) == 0:
            return []
        if self.afinador is not None:
            Q = self.afinador(Q)
        S = Q @ M.T
        for c in excluir_claves:
            col = colclave.get(c)
            if col is not None:
                S[:, col] = -np.inf
        res = []
        for v in vacas:
            sub = S[:, idx[v]]
            fila = sub.max(axis=1)
            ok = np.isfinite(fila)
            if not ok.any():
                continue
            col = idx[v][int(np.argmax(np.where(np.isfinite(sub), sub, -9).mean(axis=0)))]
            res.append((float(fila[ok].mean()), v, meta[col][:2]))
        res.sort(key=lambda r: -r[0])
        return res

    def puntajes(self, Q, excluir=None, k=5):
        """Para cada fila de Q devuelve [(score, vaca, (tipo, id))] ordenado de mayor a menor.
        El puntaje de cada vaca es la máxima similitud contra cualquiera de sus fotos/recortes."""
        M, meta, vacas, idx, colclave = self._armar()
        if not vacas or len(Q) == 0:
            return [[] for _ in range(len(Q))]
        if self.afinador is not None:
            Q = self.afinador(Q)
        S = Q @ M.T
        if excluir:
            for q, clave in enumerate(excluir):
                col = colclave.get(clave)
                if col is not None:
                    S[q, col] = -np.inf
        n = len(Q)
        P = np.empty((n, len(vacas)), np.float32)
        B = np.empty((n, len(vacas)), np.int64)
        filas = np.arange(n)
        for j, v in enumerate(vacas):
            sub = S[:, idx[v]]
            a = sub.argmax(axis=1)
            P[:, j] = sub[filas, a]
            B[:, j] = idx[v][a]
        salida = []
        for q in range(n):
            orden = np.argsort(-P[q])
            if k:
                orden = orden[:k]
            salida.append([(float(P[q, j]), vacas[j], meta[B[q, j]][:2])
                           for j in orden if np.isfinite(P[q, j])])
        return salida


# ----------------------------------------------------------------------------- lógica principal

class Identificador:
    def __init__(self, args):
        import torch
        torch.set_num_threads(args.hilos)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        try:
            import cv2
            cv2.setNumThreads(1)
        except ImportError:
            pass

        self.args = args
        self.lock = threading.RLock()
        self.dir_frames = Path(args.frames)
        self.dir_sf = Path(args.sin_fondo)
        self.dir_orig = Path(args.originales)
        self.dir_bib = Path(args.biblioteca) if args.biblioteca else None
        self.dir_trabajo = Path(args.trabajo)
        (self.dir_trabajo / "recortes").mkdir(parents=True, exist_ok=True)

        print(f"Modo liviano: CPU, {args.hilos} hilo(s), pausa {args.pausa} s entre imágenes.")
        clases = CLASES_ANIMALES if args.clases == "animales" else [CLASE_VACA]
        self.detector = Detector(args.yolo, args.conf, clases, args.imgsz)
        self.segmentador = Segmentador(args.sam) if args.mascara == "sam" else None
        self.emb = Embebedor()
        self.galeria = Galeria(self.emb.dim)

        self._cargar_referencias()
        self._cargar_confirmadas()
        self.info_afinado = None
        self.afinar()

        self.frames = sorted([f.name for f in self.dir_frames.iterdir()
                              if f.is_file() and f.suffix.lower() in EXT_IMG], key=orden_natural)
        self.set_frames = set(self.frames)
        self.estado = leer_json(self.dir_trabajo / "estado.json",
                                {"procesados": [], "lote_actual": [], "numero_lote": 0})
        self.etiquetas = leer_json(self.dir_trabajo / "etiquetas.json", {})
        self.excluidas = set(leer_json(self.dir_trabajo / "excluidas.json", []))
        self.lote = None
        self._cache_img = {}
        print(f"Frames: {len(self.frames)} en total, {len(self.estado['procesados'])} ya procesados.")

    def afinar(self):
        """(Re)entrena el ajuste de la huella con las referencias + todo lo confirmado en el campo."""
        if self.args.sin_afinar:
            return
        g = self.galeria
        X = [g.ref_emb] + [v["emb"][None] for v in g.conf.values()]
        y = [m["vaca"] for m in g.ref_meta] + [v["vaca"] for v in g.conf.values()]
        t0 = time.time()
        a = Afinador.entrenar(np.concatenate(X), y)
        g.poner_afinador(a)
        if a is None:
            print("Huella sin afinar (todavía hay muy pocos datos).")
            return
        self.info_afinado = {"muestras": a.n_muestras, "vacas": a.n_vacas, "del_campo": len(g.conf)}
        print(f"Huella afinada con {a.n_muestras} fotos/recortes de {a.n_vacas} vacas "
              f"({len(g.conf)} confirmados en el campo) en {time.time() - t0:.1f} s")

    def completar_siluetas(self, im, ds):
        """Agrega/ajusta el polígono 'poli' de cada detección según el modo de máscara elegido."""
        if self.args.mascara == "no":
            for d in ds:
                d["poli"] = None
        elif self.args.mascara == "sam" and ds:
            for d, p in zip(ds, self.segmentador.siluetas(im, [d["box"] for d in ds])):
                d["poli"] = p
        # modo "yolo": se usa el polígono que ya trae el modelo YOLO-seg (si no es -seg, queda None)
        return ds

    def descansar(self):
        if self.args.pausa > 0:
            time.sleep(self.args.pausa)

    # ---------- persistencia

    def _guardar_estado(self):
        escribir_json(self.dir_trabajo / "estado.json", self.estado)

    def _guardar_etiquetas(self):
        escribir_json(self.dir_trabajo / "etiquetas.json", self.etiquetas)

    def _guardar_confirmadas(self):
        claves = list(self.galeria.conf)
        if claves:
            E = np.stack([self.galeria.conf[k]["emb"] for k in claves])
        else:
            E = np.zeros((0, self.emb.dim), np.float32)
        np.save(self.dir_trabajo / "confirmadas.npy", E)
        escribir_json(self.dir_trabajo / "confirmadas.json",
                      [{"clave": k, "vaca": self.galeria.conf[k]["vaca"], "ruta": self.galeria.conf[k]["ruta"]}
                       for k in claves])

    def _cargar_confirmadas(self):
        meta = leer_json(self.dir_trabajo / "confirmadas.json", [])
        ruta_npy = self.dir_trabajo / "confirmadas.npy"
        if not meta or not ruta_npy.exists():
            return
        E = np.load(ruta_npy)
        if len(E) != len(meta):
            print("  [AVISO] confirmadas.npy y confirmadas.json no coinciden; se ignoran las confirmadas.")
            return
        for m, e in zip(meta, E):
            self.galeria.agregar(m["clave"], e.astype(np.float32), m["vaca"], m["ruta"])
        print(f"Recortes confirmados a mano cargados: {len(meta)}")

    # ---------- referencias

    def _listar_referencias(self):
        """Una entrada por foto original: [(vaca, rel, ruta_a_usar, tipo)]
        tipo = "sf" (PNG sin fondo aprobado) u "orig" (foto original, se le quita el fondo acá)."""
        revision = leer_json(self.dir_sf / "revision.json", {})
        usar_bib = not getattr(self, "sin_biblioteca", False)
        items = []
        for carpeta in sorted([d for d in self.dir_orig.iterdir() if d.is_dir()], key=orden_natural):
            fotos = sorted([f for f in carpeta.iterdir() if f.is_file() and f.suffix.lower() in EXT_IMG],
                           key=orden_natural)
            for f in fotos:
                rel = f.relative_to(self.dir_orig).as_posix()
                if f.name.startswith(PREFIJO_BIB):
                    # recorte de la biblioteca (videos de costado): ya viene sin fondo
                    if usar_bib:
                        items.append((carpeta.name, rel, f, "sf"))
                    continue
                rel_png = Path(rel).with_suffix(".png").as_posix()
                sf = self.dir_sf / rel_png
                aprobada = revision.get(rel_png, revision.get(rel)) == "bien"
                if aprobada and sf.exists():
                    items.append((carpeta.name, rel, sf, "sf"))
                else:
                    items.append((carpeta.name, rel, f, "orig"))

        # Biblioteca ampliada (armada con biblioteca_lados.py): Biblioteca/<vaca>/*.png sin fondo
        dir_bib = getattr(self, "dir_bib", None)
        if dir_bib is not None and dir_bib.is_dir():
            for carpeta in sorted([d for d in dir_bib.iterdir() if d.is_dir() and not d.name.startswith("_")],
                                  key=orden_natural):
                for f in sorted(carpeta.glob("*.png"), key=orden_natural):
                    items.append((carpeta.name, f"{dir_bib.name}/{carpeta.name}/{f.name}", f, "sf"))
        return items

    def _imagen_referencia(self, m):
        """Imagen (PIL RGB, fondo gris) que representa una foto de referencia."""
        if m["tipo"] == "sf":
            im = png_sin_fondo_a_rgb(m["ruta"])
            if im is not None:
                return im
            return abrir_imagen(m["ruta"])
        im = abrir_imagen(m["ruta"])
        if m.get("box"):
            return recortar_con_mascara(im, m["box"], m.get("poli"))
        return im

    def _cargar_referencias(self):
        if not self.dir_orig.is_dir():
            print(f"[ERROR] No existe la carpeta {self.dir_orig.resolve()}")
            sys.exit(1)
        items = self._listar_referencias()
        if not items:
            print(f"[ERROR] No encontré fotos en {self.dir_orig}")
            sys.exit(1)

        firma = hashlib.md5(json.dumps(
            [[v, rel, str(r), t, r.stat().st_mtime_ns, r.stat().st_size] for v, rel, r, t in items]
            + [getattr(self.emb, "firma", MODELO_EMB), self.args.yolo, self.args.mascara, self.args.sam,
               VERSION_CACHE]).encode()).hexdigest()
        ruta_npy = self.dir_trabajo / "cache_referencias.npy"
        ruta_json = self.dir_trabajo / "cache_referencias.json"
        cache = leer_json(ruta_json, {})

        if cache.get("firma") == firma and ruta_npy.exists():
            E = np.load(ruta_npy)
            meta = cache["meta"]
            print("Referencias cargadas desde caché.")
        else:
            print(f"Procesando {len(items)} fotos de referencia (solo la primera vez, de a una)...")
            meta, filas, sin_vaca = [], [], 0
            for n, (vaca, rel, ruta, tipo) in enumerate(items, 1):
                m = {"vaca": vaca, "rel": rel, "ruta": str(ruta), "tipo": tipo, "box": None, "poli": None}
                try:
                    if tipo == "orig":
                        im = abrir_imagen(ruta)
                        ds = self.detector.detectar(im, conf=0.25)
                        if ds:
                            d = max(ds, key=lambda d: (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1]))
                            self.completar_siluetas(im, [d])
                            m["box"], m["poli"] = d["box"], d["poli"]
                        else:
                            sin_vaca += 1
                        del im
                    im = self._imagen_referencia(m)
                    filas.append(self.emb([im])[0])
                    meta.append(m)
                    del im
                except Exception as e:
                    print(f"  [AVISO] No se pudo procesar {ruta}: {e}")
                if n % 10 == 0 or n == len(items):
                    print(f"  {n}/{len(items)}")
                self.descansar()
            E = np.stack(filas) if filas else np.zeros((0, self.emb.dim), np.float32)
            np.save(ruta_npy, E)
            escribir_json(ruta_json, {"firma": firma, "meta": meta})
            if sin_vaca:
                print(f"  {sin_vaca} fotos originales sin vaca detectada: se usan completas.")

        self.galeria.poner_referencias(E.astype(np.float32), meta)
        vacas = sorted({m["vaca"] for m in meta}, key=orden_natural)
        dir_bib = getattr(self, "dir_bib", None)
        pref_bib = f"{dir_bib.name}/" if dir_bib is not None else None
        n_bib = sum(1 for m in meta if Path(m["rel"]).name.startswith(PREFIJO_BIB)
                    or (pref_bib and m["rel"].startswith(pref_bib)))
        n_sf = sum(m["tipo"] == "sf" for m in meta) - n_bib
        n_orig = len(meta) - n_sf - n_bib
        ejemplo = ", ".join(vacas[:10]) + (" ..." if len(vacas) > 10 else "")
        print(f"Referencias: {len(meta)} fotos de {len(vacas)} vacas  ->  {ejemplo}")
        print(f"  {n_sf} sin fondo (aprobadas) + {n_orig} originales (sin fondo todavía no aprobado)"
              + (f" + {n_bib} recortes de la biblioteca (bib_*.png)" if n_bib else ""))
        if n_orig:
            for m in meta:
                if m["tipo"] == "orig":
                    print(f"    · {m['rel']}")
            print("  Cuando las apruebes en corregir_fondo.py, reiniciá este programa y se actualizan solas.")

    # ---------- lotes

    def obtener_lote(self):
        with self.lock:
            if self.lote is None:
                if not self.estado["lote_actual"]:
                    procesados = set(self.estado["procesados"])
                    pendientes = [f for f in self.frames if f not in procesados][:self.args.lote]
                    if not pendientes:
                        return self._lote_json()
                    self.estado["lote_actual"] = pendientes
                    self.estado["numero_lote"] += 1
                    self._guardar_estado()
                self._procesar_lote(self.estado["lote_actual"])
            return self._lote_json()

    def siguiente_lote(self):
        with self.lock:
            self._guardar_ultimo_frame()
            if self.estado["lote_actual"]:
                self.estado["procesados"].extend(self.estado["lote_actual"])
                self.estado["lote_actual"] = []
                self._guardar_estado()
            self.lote = None
            self._cache_img = {k: v for k, v in self._cache_img.items() if k[0] == "r"}
            self.afinar()  # lo confirmado en este lote entrena la huella para el próximo
        return self.obtener_lote()

    def _procesar_lote(self, nombres):
        print(f"\nProcesando lote {self.estado['numero_lote']} ({len(nombres)} frames, de a uno)...")
        t0 = time.time()
        frames, planas = [], []
        for n_i, n in enumerate(nombres, 1):
            if n not in self.set_frames:
                continue
            try:
                im = abrir_imagen(self.dir_frames / n)
            except Exception as e:
                print(f"  [AVISO] No se pudo abrir {n}: {e}")
                continue
            ds = [d for d in self.detector.detectar(im)
                  if min(d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]) >= self.args.min_lado]
            ds = self.completar_siluetas(im, sorted(ds, key=lambda d: d["box"][0]))  # de izquierda a derecha
            fr = {"nombre": n, "ancho": im.width, "alto": im.height, "dets": []}
            recortes = []
            for d in ds:
                det = {"clave": f"{n}|{len(fr['dets'])}", "box": d["box"], "conf": d["conf"]}
                fr["dets"].append(det)
                recortes.append(recortar_con_mascara(im, d["box"], d["poli"]))
            if recortes:
                for det, e in zip(fr["dets"], self.emb(recortes)):
                    det["emb"] = e
                planas.extend(fr["dets"])
            frames.append(fr)
            del im, recortes
            if n_i % 10 == 0:
                print(f"  {n_i}/{len(nombres)} frames")
            self.descansar()
        self.lote = {"frames": frames, "por_clave": {d["clave"]: d for d in planas}}
        self._armar_bloques(frames)
        for pista in self.lote["bloques"]:
            self._recalcular_recorrido(pista)  # lo heredado del lote anterior / lo ya etiquetado
        self._guardar_etiquetas()
        self._guardar_confirmadas()
        self._actualizar_candidatos(todas=True)
        print(f"  {len(planas)} vacas detectadas en {time.time() - t0:.1f} s · "
              f"{len(self.lote['bloques'])} recorridos para etiquetar")

    # ---------- recorridos (la misma vaca a lo largo de los frames del lote)

    def _armar_bloques(self, frames):
        """Une cada detección con la del frame anterior si está casi en el mismo lugar y se ve igual:
        así se arma el recorrido de cada vaca. El primer frame se puede unir con el último del lote
        anterior (y hereda su etiqueta)."""
        bloques, heredadas, n = {}, {}, 0
        previos, t_prev = [], None
        ult = self.estado.get("ultimo_frame")
        if ult:
            previos = [{"box": u["box"], "emb": np.asarray(u["emb"], np.float32), "pista": f"ant{k}",
                        "etiqueta": u.get("etiqueta")} for k, u in enumerate(ult["dets"])]
            t_prev = ult["t"]
        for fr in frames:
            t = tiempo_de(fr["nombre"])
            cands = previos if t_prev is not None and 0 <= t - t_prev <= BLOQUE_HUECO else []
            pares = []
            for j, d in enumerate(fr["dets"]):
                if "emb" not in d:
                    continue
                for k, p in enumerate(cands):
                    sup, sim = iou(d["box"], p["box"]), float(d["emb"] @ p["emb"])
                    if sup >= BLOQUE_IOU and sim >= BLOQUE_SIM:
                        pares.append((sup + sim, j, k))
            usados_d, usados_p = set(), set()
            for _, j, k in sorted(pares, reverse=True):
                if j in usados_d or k in usados_p:
                    continue
                usados_d.add(j)
                usados_p.add(k)
                d, p = fr["dets"][j], cands[k]
                d["pista"] = p["pista"]
                if p.get("etiqueta") and d["pista"].startswith("ant"):
                    heredadas[d["pista"]] = p["etiqueta"]
            for j, d in enumerate(fr["dets"]):
                if "emb" not in d:
                    continue
                if j not in usados_d:
                    n += 1
                    d["pista"] = f"b{n}"
                bloques.setdefault(d["pista"], []).append(d["clave"])
            if any("emb" in d for d in fr["dets"]):
                previos = [{"box": d["box"], "emb": d["emb"], "pista": d["pista"]}
                           for d in fr["dets"] if "emb" in d]
                t_prev = t
        self.lote["bloques"] = bloques
        self.lote["heredadas"] = heredadas

    def _miembros(self, pista, con_excluidas=False):
        return [c for c in self.lote["bloques"].get(pista, []) if con_excluidas or c not in self.excluidas]

    def _area(self, clave):
        b = self.lote["por_clave"][clave]["box"]
        return (b[2] - b[0]) * (b[3] - b[1])

    def _utiles(self, pista, maximo=8):
        """Recortes que se usan para comparar: los más grandes del recorrido (se ven mejor)."""
        return sorted(self._miembros(pista), key=lambda c: -self._area(c))[:maximo]

    def _etiqueta_recorrido(self, pista):
        """Etiqueta del recorrido: la que tiene la mayoría de sus frames (o None)."""
        ets = [self.etiquetas[c] for c in self._miembros(pista) if c in self.etiquetas]
        if not ets:
            return None
        cuenta = {}
        for e in ets:
            k = (e["accion"], e.get("vaca"))
            cuenta[k] = cuenta.get(k, 0) + 1
        (accion, vaca), _ = max(cuenta.items(), key=lambda x: x[1])
        # lugar en el ranking: el del recorte representativo, o (etiquetas viejas) el primero que lo tenga
        rep = next((e for e in ets if e.get("rep")), None) or \
            next((e for e in ets if e.get("posicion") is not None and (e["accion"], e.get("vaca")) == (accion, vaca)), None)
        return {"accion": accion, "vaca": vaca, "auto": all(e.get("auto") for e in ets),
                "posicion": rep.get("posicion") if rep else None}

    def _quitar_etiqueta(self, clave):
        previo = self.galeria.quitar(clave)
        if previo:
            Path(previo["ruta"]).unlink(missing_ok=True)
        self.etiquetas.pop(clave, None)

    def _a_galeria(self, clave, vaca):
        """Guarda el recorte y lo suma a la galería (referencias de la vaca)."""
        nombre, i = clave.rsplit("|", 1)
        det = self.lote["por_clave"][clave]
        carpeta = self.dir_trabajo / "recortes" / nombre_seguro(vaca)
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"{Path(nombre).stem}_d{i}.jpg"
        recortar(abrir_imagen(self.dir_frames / nombre), det["box"]).save(ruta, quality=92)
        self.galeria.agregar(clave, det["emb"], vaca, ruta)
        return str(ruta)

    def _elegir_para_galeria(self, claves, maximo=4):
        """Hasta `maximo` recortes del recorrido para la galería: los más grandes, pero distintos entre sí."""
        elegidos = []
        for c in sorted(claves, key=lambda c: -self._area(c)):
            e = self.lote["por_clave"][c]["emb"]
            if all(float(e @ self.lote["por_clave"][o]["emb"]) < NOVEDAD for o in elegidos):
                elegidos.append(c)
            if len(elegidos) >= maximo:
                break
        return elegidos

    def _poner_recorrido(self, pista, accion, vaca=None, auto=False, posicion=None, origen=None):
        """Pone la misma etiqueta en todos los frames del recorrido (los excluidos quedan descartados)."""
        for c in self._miembros(pista, con_excluidas=True):
            self._quitar_etiqueta(c)
        miembros = self._miembros(pista)
        para_galeria = set(self._elegir_para_galeria(miembros)) if accion == "vaca" else set()
        rep = max(miembros, key=self._area) if miembros else None
        for c in self._miembros(pista, con_excluidas=True):
            nombre = c.rsplit("|", 1)[0]
            det = self.lote["por_clave"][c]
            if c in self.excluidas:  # recorte sacado del recorrido (otra vaca, tapada...)
                self.etiquetas[c] = {"accion": "descartar", "lote": self.estado["numero_lote"], "frame": nombre,
                                     "box": det["box"], "ts": round(time.time()), "excluida": True}
                continue
            reg = {"accion": accion, "lote": self.estado["numero_lote"], "frame": nombre, "box": det["box"],
                   "ts": round(time.time()), "recorrido": pista, "rep": c == rep}
            if auto:
                reg.update(auto=True, origen=origen)
            if accion == "vaca":
                reg["vaca"] = vaca
                if c == rep and posicion is not None:
                    reg["posicion"] = posicion
                if c in para_galeria:
                    reg["recorte"] = self._a_galeria(c, vaca)
            self.etiquetas[c] = reg

    def _recalcular_recorrido(self, pista):
        """Al (re)procesar un lote: si el recorrido ya tenía etiqueta manual, la extiende a todos sus
        frames; si no, y viene del lote anterior, hereda la etiqueta de ahí (automática)."""
        et = self._etiqueta_recorrido(pista)
        if et and not et["auto"]:
            self._poner_recorrido(pista, et["accion"], et.get("vaca"), posicion=et.get("posicion"))
            return
        heredada = self.lote["heredadas"].get(pista)
        if heredada:
            self._poner_recorrido(pista, heredada["accion"], heredada.get("vaca"), auto=True, origen="lote anterior")
        elif et:
            for c in self._miembros(pista, con_excluidas=True):
                self._quitar_etiqueta(c)

    def _ocupadas(self, pista):
        """Vacas ya asignadas a OTRO recorrido que aparece en alguno de los mismos frames
        (una vaca no puede estar dos veces en el mismo frame)."""
        frames = {c.rsplit("|", 1)[0] for c in self._miembros(pista, con_excluidas=True)}
        propias = set(self._miembros(pista, con_excluidas=True))
        return {e["vaca"] for c, e in self.etiquetas.items()
                if e.get("accion") == "vaca" and c not in propias and e["frame"] in frames}

    def ranking_recorrido(self, pista, k=5, filtrar_ocupadas=True):
        """[(puntaje, vaca, (tipo, id))]: promedio, sobre los mejores recortes del recorrido, del
        parecido con cada vaca. No se compara consigo mismo."""
        utiles = self._utiles(pista)
        if not utiles:
            return []
        Q = np.stack([self.lote["por_clave"][c]["emb"] for c in utiles])
        res = self.galeria.puntajes_grupo(Q, excluir_claves=self._miembros(pista, con_excluidas=True))
        if filtrar_ocupadas:
            ocupadas = self._ocupadas(pista)
            res = [r for r in res if r[1] not in ocupadas]
        return res if k is None else res[:k]

    def _actualizar_candidatos(self, todas=False):
        self.lote["candidatos"] = self.lote.get("candidatos", {})
        for pista in self.lote["bloques"]:
            et = self._etiqueta_recorrido(pista)
            if todas or not et or et["auto"]:
                self.lote["candidatos"][pista] = [
                    {"vaca": v, "score": round(s, 4), "t": t, "id": i} for s, v, (t, i) in self.ranking_recorrido(pista)]

    def _guardar_ultimo_frame(self):
        """Guarda el último frame con vacas del lote, para encadenar los recorridos con el lote siguiente."""
        if not self.lote:
            return
        con_vacas = [fr for fr in self.lote["frames"] if any("emb" in d for d in fr["dets"])]
        if not con_vacas:
            return
        fr = con_vacas[-1]
        dets = []
        for d in fr["dets"]:
            if "emb" not in d:
                continue
            et = self.etiquetas.get(d["clave"])
            dets.append({"box": [round(v, 1) for v in d["box"]], "emb": [round(float(x), 5) for x in d["emb"]],
                         "etiqueta": {"accion": et["accion"], "vaca": et.get("vaca")}
                         if et and not et.get("excluida") else None})
        self.estado["ultimo_frame"] = {"t": tiempo_de(fr["nombre"]), "dets": dets}

    def _estadisticas(self):
        """Acierto por lote, contando una vez por recorrido elegido a mano (no las automáticas)."""
        por_lote = {}
        for r in self.etiquetas.values():
            if r.get("auto") or r.get("excluida") or ("recorrido" in r and not r.get("rep")):
                continue
            s = por_lote.setdefault(r["lote"], {"lote": r["lote"], "n": 0, "top1": 0, "top5": 0,
                                                "nuevas": 0, "descartadas": 0})
            if r["accion"] == "vaca":
                p = r.get("posicion") or 0
                s["n"] += 1
                s["top1"] += int(p == 1)
                s["top5"] += int(1 <= p <= 5)
                s["nuevas"] += int(p == 0)
            elif r["accion"] == "descartar":
                s["descartadas"] += 1
        return [por_lote[k] for k in sorted(por_lote)]

    def _lote_json(self):
        base = {"total_frames": len(self.frames), "procesados": len(self.estado["procesados"]),
                "afinado": self.info_afinado,
                "estadisticas": self._estadisticas(), "vacas": self.galeria.vacas()}
        if self.lote is None:
            return {**base, "fin": True}
        tam = {f["nombre"]: (f["ancho"], f["alto"]) for f in self.lote["frames"]}
        recorridos = []
        for pista, claves in self.lote["bloques"].items():
            dets = []
            for c in claves:
                d = self.lote["por_clave"][c]
                nombre = c.rsplit("|", 1)[0]
                dets.append({"clave": c, "frame": nombre, "box": [round(v, 1) for v in d["box"]],
                             "ancho": tam[nombre][0], "alto": tam[nombre][1], "excluida": c in self.excluidas})
            recorridos.append({"id": pista, "dets": dets, "etiqueta": self._etiqueta_recorrido(pista),
                               "del_lote_anterior": pista.startswith("ant"),
                               "candidatos": self.lote.get("candidatos", {}).get(pista, []),
                               "mejor": max((c for c in claves if c not in self.excluidas), key=self._area,
                                            default=claves[0])})
        recorridos.sort(key=lambda r: (tiempo_de(r["dets"][0]["frame"]), r["dets"][0]["box"][0]))
        return {**base, "fin": False, "numero": self.estado["numero_lote"], "recorridos": recorridos,
                "frames": [f["nombre"] for f in self.lote["frames"]]}

    # ---------- etiquetado (un recorrido entero de una vez)

    def etiquetar(self, pista, accion, vaca=None):
        with self.lock:
            if self.lote is None or pista not in self.lote["bloques"]:
                raise ValueError("Ese recorrido no está en el lote actual.")
            if accion not in ACCIONES:
                raise ValueError(f"Acción inválida: {accion}")
            vaca = (vaca or "").strip()
            if accion == "vaca" and not vaca:
                raise ValueError("Falta el número de vaca.")
            if accion == "borrar":
                for c in self._miembros(pista, con_excluidas=True):
                    self._quitar_etiqueta(c)
            else:
                posicion = None
                if accion == "vaca":
                    ranking = self.ranking_recorrido(pista, k=None, filtrar_ocupadas=False)
                    posicion = next((n + 1 for n, (_, v, _) in enumerate(ranking) if v == vaca), 0)
                self._poner_recorrido(pista, accion, vaca, posicion=posicion)
            self._guardar_etiquetas()
            self._guardar_confirmadas()
            self._actualizar_candidatos()
            return self._lote_json()

    def excluir(self, clave, si):
        """Saca (o vuelve a poner) un recorte del recorrido: otra vaca mezclada, tapada, mal recortada."""
        with self.lock:
            if self.lote is None or clave not in self.lote["por_clave"]:
                raise ValueError("Ese recorte no está en el lote actual.")
            (self.excluidas.add if si else self.excluidas.discard)(clave)
            escribir_json(self.dir_trabajo / "excluidas.json", sorted(self.excluidas))
            pista = self.lote["por_clave"][clave].get("pista")
            et = self._etiqueta_recorrido(pista)
            if et:  # se vuelve a aplicar la etiqueta con el recorrido corregido
                self._poner_recorrido(pista, et["accion"], et.get("vaca"), auto=et["auto"],
                                      posicion=et.get("posicion"), origen="lote anterior" if et["auto"] else None)
            elif not si:
                self._quitar_etiqueta(clave)
            self._guardar_etiquetas()
            self._guardar_confirmadas()
            self._actualizar_candidatos(todas=True)
            return self._lote_json()

    def _fotos_por_lado(self):
        """{vaca: {"A": índice de referencia, "B": índice}}. Prefiere las fotos de Asignaciones
        (vaca_A / vaca_B) y si no hay, un recorte de los videos de ese lado (bib_LADOA / bib_LADOB)."""
        if getattr(self, "_lados", None) is None:
            # los archivos de la biblioteca ampliada se llaman 1111_1.png...: el nombre original está en _indice.csv
            origen = {}
            dir_bib = getattr(self, "dir_bib", None)
            if dir_bib is not None and (dir_bib / "_indice.csv").is_file():
                with open(dir_bib / "_indice.csv", encoding="utf-8-sig", newline="") as fh:
                    for fila in csv.DictReader(fh):
                        origen[f"{dir_bib.name}/{fila['archivo']}"] = fila["origen"].replace("\\", "/")
            lados = {}
            for i, m in enumerate(self.galeria.ref_meta):
                nombre = Path(origen.get(m["rel"], m["rel"])).name
                stem = Path(nombre).stem.upper()
                if nombre.startswith("bib_LADOA"):
                    lado, prioridad = "A", 1
                elif nombre.startswith("bib_LADOB"):
                    lado, prioridad = "B", 1
                elif stem.endswith("_A"):
                    lado, prioridad = "A", 0
                elif stem.endswith("_B"):
                    lado, prioridad = "B", 0
                elif "_B" in stem[-3:]:  # fotos extra (4345_B2)
                    lado, prioridad = "B", 0.5
                elif m["rel"] in origen:  # de la biblioteca pero sin pista del lado
                    continue
                else:  # vlcsnap: las capturas del lado A miden 832x464, las del B 1024x576
                    try:
                        with Image.open(self.dir_orig / m["rel"]) as im:
                            lado = "B" if im.size[0] >= 1000 else "A"
                    except OSError:
                        lado = "A"
                    prioridad = 0
                previo = lados.setdefault(m["vaca"], {}).get(lado)
                if previo is None or prioridad < previo[1]:
                    lados[m["vaca"]][lado] = (i, prioridad)
            self._lados = {v: {l: i for l, (i, _) in d.items()} for v, d in lados.items()}
        return self._lados

    def todas_json(self, pista):
        """Todas las vacas ordenadas por parecido con el recorrido (para 'ver todas')."""
        with self.lock:
            if self.lote is None or pista not in self.lote["bloques"]:
                raise ValueError("Ese recorrido no está en el lote actual.")
            ranking = self.ranking_recorrido(pista, k=None, filtrar_ocupadas=False)
            ocupadas = self._ocupadas(pista)
            lados = self._fotos_por_lado()
            primera_ref = {}
            for i, m in enumerate(self.galeria.ref_meta):
                primera_ref.setdefault(m["vaca"], i)

            def fotos(v, t=None, i=None):
                f = {l: ["r", j] for l, j in lados.get(v, {}).items()}
                if not f:  # sin foto de ningún lado reconocible: la primera referencia (o lo visto en el campo)
                    ref = primera_ref.get(v)
                    f["otra"] = ["r", ref] if ref is not None else [t, i]
                return f

            vistas = set()
            vacas = []
            for s, v, (t, i) in ranking:
                vistas.add(v)
                vacas.append({"vaca": v, "score": round(s, 4), "fotos": fotos(v, t, i), "ocupada": v in ocupadas})
            vacas += [{"vaca": v, "score": None, "fotos": fotos(v), "ocupada": v in ocupadas}
                      for v in primera_ref if v not in vistas]
            miembros = self._miembros(pista) or self._miembros(pista, True)
            return {"vacas": vacas, "mejor": max(miembros, key=self._area)}

    # ---------- imágenes para la página

    def img_mini(self, nombre):
        if nombre not in self.set_frames:
            return None
        clave = ("mini", nombre)
        if clave not in self._cache_img:
            self._cache_img[clave] = a_jpg(abrir_imagen(self.dir_frames / nombre), 220)
        return self._cache_img[clave]

    def img_recorte(self, clave, lado=480):
        with self.lock:
            det = self.lote["por_clave"].get(clave) if self.lote else None
        if det is None:
            return None
        k = ("rec", clave, lado)
        if k not in self._cache_img:
            nombre = clave.rsplit("|", 1)[0]
            self._cache_img[k] = a_jpg(recortar(abrir_imagen(self.dir_frames / nombre), det["box"]), lado)
        return self._cache_img[k]

    def img_galeria(self, tipo, ident):
        if tipo == "r":
            clave = ("r", ident)
            if clave not in self._cache_img:
                m = self.galeria.ref_meta[int(ident)]
                self._cache_img[clave] = a_jpg(self._imagen_referencia(m), 300)
            return self._cache_img[clave]
        item = self.galeria.conf.get(ident)
        if item is None or not Path(item["ruta"]).exists():
            return None
        return a_jpg(abrir_imagen(item["ruta"]), 300)


# ----------------------------------------------------------------------------- servidor web

def crear_web(ident):
    from flask import Flask, Response, abort, jsonify, request, send_file

    web = Flask(__name__)
    html = Path(__file__).with_name("interfaz.html")

    def jpg(datos):
        if datos is None:
            abort(404)
        return Response(datos, mimetype="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @web.get("/")
    def inicio():
        return send_file(html)

    @web.get("/api/lote")
    def api_lote():
        return jsonify(ident.obtener_lote())

    @web.post("/api/siguiente")
    def api_siguiente():
        return jsonify(ident.siguiente_lote())

    @web.post("/api/etiquetar")
    def api_etiquetar():
        d = request.get_json(force=True)
        try:
            return jsonify(ident.etiquetar(d.get("recorrido"), d.get("accion"), d.get("vaca")))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @web.post("/api/excluir")
    def api_excluir():
        d = request.get_json(force=True)
        try:
            return jsonify(ident.excluir(d.get("clave"), bool(d.get("excluir"))))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @web.get("/api/todas")
    def api_todas():
        try:
            return jsonify(ident.todas_json(request.args.get("recorrido", "")))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @web.get("/img/frame/<path:nombre>")
    def img_frame(nombre):
        if nombre not in ident.set_frames:
            abort(404)
        return send_file(ident.dir_frames.resolve() / nombre, max_age=3600)

    @web.get("/img/mini/<path:nombre>")
    def img_mini(nombre):
        return jpg(ident.img_mini(nombre))

    @web.get("/img/recorte")
    def img_recorte():
        lado = min(max(int(request.args.get("lado", 480)), 80), 800)
        return jpg(ident.img_recorte(request.args.get("clave", ""), lado))

    @web.get("/img/galeria")
    def img_galeria():
        try:
            return jpg(ident.img_galeria(request.args.get("t"), request.args.get("id", "")))
        except (IndexError, ValueError):
            abort(404)

    return web


def main():
    p = argparse.ArgumentParser(description="Identificación asistida de vacas por lotes (liviano, sin fondo).")
    p.add_argument("--frames", default="frames_desfile_camaras")
    p.add_argument("--sin-fondo", default="Asignaciones_sin_fondo")
    p.add_argument("--originales", default="Asignaciones (Verificado)")
    p.add_argument("--biblioteca", default="")
    p.add_argument("--trabajo", default="identificacion")
    p.add_argument("--lote", type=int, default=20)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--min-lado", type=int, default=70)
    p.add_argument("--clases", choices=["animales", "vaca"], default="animales")
    p.add_argument("--imgsz", type=int, default=1280)
    p.add_argument("--sin-afinar", action="store_true")
    p.add_argument("--yolo", default="yolo11s.pt")
    p.add_argument("--mascara", choices=["sam", "yolo", "no"], default="sam")
    p.add_argument("--sam", default="sam2.1_t.pt")
    p.add_argument("--hilos", type=int, default=2)
    p.add_argument("--pausa", type=float, default=0.1)
    p.add_argument("--puerto", type=int, default=5000)
    args = p.parse_args()

    for carpeta, que in [(args.frames, "frames"), (args.originales, "fotos originales"),
                         (args.sin_fondo, "fotos sin fondo")]:
        if not Path(carpeta).is_dir():
            print(f"[ERROR] No existe la carpeta de {que}: {Path(carpeta).resolve()}")
            sys.exit(1)
    if not Path(__file__).with_name("interfaz.html").exists():
        print("[ERROR] Falta interfaz.html al lado de este script.")
        sys.exit(1)

    ident = Identificador(args)
    web = crear_web(ident)
    url = f"http://127.0.0.1:{args.puerto}"
    print(f"\nListo. Abrí {url} en el navegador (Ctrl+C para cortar).")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    web.run(host="127.0.0.1", port=args.puerto, threaded=True)


if __name__ == "__main__":
    main()
