"""
campo.py
Identificación de vacas en el video del campo POR RECORRIDO: cada vaca se sigue a lo largo de los
frames (seguimiento por centroide) y se etiqueta una sola vez su recorrido completo.

Cómo funciona:
  1. Un proceso en segundo plano recorre los frames en orden (de a uno, liviano):
       - detecta los animales con YOLO (desde arriba YOLO confunde vacas con pájaros, ovejas...,
         por eso se aceptan todas las clases de animales) y descarta las vacas muy chicas del fondo;
       - SEGUIMIENTO POR CENTROIDE: cada detección se une al recorrido cuyo centro (más la
         velocidad que traía) le queda más cerca. No usa la huella: es rápido.
       - un recorrido se "cierra" cuando la vaca no aparece por más de 1,5 s; ahí se calculan las
         huellas (SAM + DINOv2) SOLO de sus mejores recortes (4 por defecto) y queda listo.
       - recorridos de 1 o 2 frames se descartan solos.
       - recorridos donde las vacas están amontonadas (recuadros encimados o muy pegados) van solos
         a "Para después".
  2. La página (http://127.0.0.1:5004) muestra los recorridos listos; los nuevos van apareciendo.
     Para cada recorrido: todos sus recortes, las 5 vacas más probables y "ver todas" (T).
     Elegís el número una vez y vale para todo el recorrido.
     Botones: UNIR dos recorridos (son la misma vaca), DIVIDIR en el frame que estás viendo,
     dejar PARA DESPUÉS.
  3. Cada 5 recorridos confirmados la huella se re-afina (LDA) con todo lo confirmado.
  4. "Exportar" guarda campo/resultado_por_frame.csv (frame, recuadro, vaca).
  5. Fotos por vaca: en recorridas_campo/<número de vaca>/ queda cada frame de los recorridos que
     marcaste, con el recuadro de la vaca dibujado y su número. Se va llenando sola mientras
     trabajás y al cerrar (Ctrl+C) termina de guardar lo que falte. Si cambiás una etiqueta, las
     fotos se mueven/borran solas.

Dónde queda todo guardado (se guarda solo, en cada clic):
    campo/              etiquetas.json, problemas.json, postergados.json (lo que marcás a mano)
                        recorridos.json, detecciones.json, emb.npy, estado.json (el avance)
    recorridas_campo/   las fotos por vaca

Uso (desde la carpeta desfile_tres_lomas):
    python campo.py
Se puede cortar con Ctrl+C: la próxima vez sigue donde quedó.

Opciones:
    --frames         carpeta de frames                   (default: frames_desfile_camaras)
    --trabajo        carpeta donde se guarda todo        (default: campo)
    --por-recorrido  recortes por recorrido para la huella (default: 4)
    --min-frames     recorridos más cortos se descartan  (default: 3)
    --min-lado       vacas más chicas (px) se ignoran     (default: 70)
    --hueco          segundos sin ver la vaca para cerrar el recorrido (default: 1.5)
    --hilos / --pausa / --puerto                          (default: 2 / 0.1 / 5004)
    --red            que entre el equipo desde otras compus de la misma red WiFi (muestra la dirección)
                     Cada persona pone su nombre; un recorrido que alguien está mirando aparece como
                     "lo mira Fulano" y "Siguiente" lo saltea, para no hacer dos veces el mismo.
    --clave          contraseña para entrar (o variable de entorno CAMPO_CLAVE); sin ella no pide nada
    --sin-navegador  no abrir el navegador al arrancar (para el servidor: ver servidor/GUIA.md)
    --fotos          carpeta de fotos por vaca           (default: recorridas_campo)
    --ancho-fotos    ancho de esas fotos en píxeles      (default: 1280)
    --modelo-huella  pesos de DINOv2 entrenado con las vacas (default: dinov2_vacas.pt; si no existe,
                     o con "--modelo-huella original", usa el DINOv2 de fábrica)
                     Al cambiar de modelo, la primera vez que arranca recalcula las huellas de todos
                     los recortes ya procesados (SAM + DINOv2): puede tardar 1 a 2 horas en el servidor.
                     Lo marcado a mano no se toca.
"""

import identificador as I  # primero: limita los hilos de CPU antes de cargar torch

import argparse
import csv
import json
import math
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np

DIST_MAX = 0.6        # distancia máxima centro-predicción, en fracciones de la diagonal del recuadro
TAM_MIN, TAM_MAX = 0.5, 2.0   # cambio de tamaño permitido entre frames seguidos
AMONTONADO_IOU = 0.15  # dos vacas con recuadros así de encimados = amontonadas
AMONTONADO_FRAC = 0.4  # recorrido "amontonado" si al menos esta fracción de sus frames lo está
REAFINAR_CADA = 5
TOMA_SEG = 600        # un recorrido abierto por alguien del equipo queda "tomado" 10 minutos

# Qué puede estar mal en UN frame del recorrido
MOTIVOS_FRAME = {
    "cortada": "La vaca está cortada (recuadro incompleto)",
    "dos_vacas": "Hay dos vacas en el recuadro",
    "otra_vaca": "Es otra vaca (el seguimiento se confundió)",
    "no_se_ve": "No se ve bien (tapada, borrosa, de espaldas)",
}
# Por qué se descarta un recorrido entero
MOTIVOS_DESCARTE = {
    "chica": "Muy chica / lejos: no se distingue",
    "no_se_ve": "Tapada o no se ve bien",
    "no_es_vaca": "No es una vaca (error del detector)",
    "amontonadas": "Vacas amontonadas: no se puede separar",
    "otro": "Otro motivo",
}


def guardar_compacto(ruta, datos):
    """JSON compacto, forzado al disco y reemplazado de golpe (seguro ante apagones)."""
    ruta = Path(ruta)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(datos, ensure_ascii=False, separators=(",", ":")))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(ruta)


def centro(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def diag(b):
    return math.hypot(b[2] - b[0], b[3] - b[1])


def area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


class Referencias:
    """Las mismas referencias que identificador.py (usa su caché: carpeta identificacion)."""
    _listar_referencias = I.Identificador._listar_referencias
    _imagen_referencia = I.Identificador._imagen_referencia
    _cargar_referencias = I.Identificador._cargar_referencias
    completar_siluetas = I.Identificador.completar_siluetas
    descansar = I.Identificador.descansar

    def __init__(self, args, campo):
        self.args = args
        self._campo = campo
        self.dir_sf = Path("Asignaciones_sin_fondo")
        self.dir_orig = Path("Asignaciones (Verificado)")
        self.dir_bib = None
        self.dir_trabajo = Path("identificacion")
        self.dir_trabajo.mkdir(exist_ok=True)
        self.emb = campo.emb
        self.galeria = campo.galeria
        self._cargar_referencias()

    @property
    def detector(self):
        return self._campo.detector

    @property
    def segmentador(self):
        return self._campo.segmentador


class Campo:
    def __init__(self, args):
        import torch
        torch.set_num_threads(args.hilos)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        self.args = args
        self.lock = threading.RLock()       # datos
        self.lock_mod = threading.Lock()    # modelos (los usa el proceso de fondo y "dividir")
        self.dir = Path(args.trabajo)
        self.dir.mkdir(exist_ok=True)
        self.dir_frames = Path(args.frames)
        self.frames = sorted([f.name for f in self.dir_frames.iterdir()
                              if f.is_file() and f.suffix.lower() in I.EXT_IMG], key=I.orden_natural)
        self.set_frames = set(self.frames)

        print(f"Modo liviano: CPU, {args.hilos} hilo(s), pausa {args.pausa} s entre frames.")
        # mismos parámetros que identificador.py (para compartir la caché de referencias)
        args.yolo, args.mascara, args.sam, args.conf = "yolo11s.pt", "sam", "sam2.1_t.pt", 0.25
        self.detector = I.Detector(args.yolo, args.conf, I.CLASES_ANIMALES, 1280)
        self.segmentador = I.Segmentador(args.sam)
        pesos = None if str(args.modelo_huella).lower() in ("", "original") else args.modelo_huella
        self.emb = I.Embebedor(pesos)
        self.galeria = I.Galeria(self.emb.dim)
        self.refs = Referencias(args, self)

        # ---- estado guardado
        self.dets = I.leer_json(self.dir / "detecciones.json", {})          # clave -> datos
        self.recs = I.leer_json(self.dir / "recorridos.json", {})           # id -> recorrido
        self.etiquetas = I.leer_json(self.dir / "etiquetas.json", {})       # id -> etiqueta
        self.estado = I.leer_json(self.dir / "estado.json", {"siguiente": 0, "contador": 0, "activos": []})
        self.emb_idx = I.leer_json(self.dir / "emb_idx.json", {})           # clave -> fila
        ruta = self.dir / "emb.npy"
        self.E = np.load(ruta) if ruta.exists() and self.emb_idx else np.zeros((0, self.emb.dim), np.float32)
        if len(self.E) < len(self.emb_idx):  # se cortó a mitad de guardado: se descartan las que faltan
            self.emb_idx = {k: v for k, v in self.emb_idx.items() if v < len(self.E)}
        self.tam = tuple(self.estado.get("tam", (1920, 1080)))
        # lo que marcás a mano se guarda en archivos chicos, al instante, en cada clic
        self.problemas = I.leer_json(self.dir / "problemas.json", {})       # clave -> {motivo, rec}
        self.postergados = I.leer_json(self.dir / "postergados.json", {})   # id -> True/False
        self.tomados = {}   # id -> {"nombre", "ts"}: quién del equipo lo está mirando (solo en memoria)
        self._reparar()
        self._revisar_modelo_huella()

        self._confirmadas_a_galeria()
        self.desde_afinado = 0
        self.info_afinado = None
        self.afinar()
        self._cache_img = {}
        self.velocidad = None
        self.parar = False
        self.fotos_pendientes = threading.Event()
        self.fotos_pendientes.set()  # al arrancar: completar las fotos que falten
        listos = sum(r["estado"] == "listo" for r in self.recs.values())
        print(f"Frames: {len(self.frames)} · ya procesados {self.estado['siguiente']} · recorridos listos {listos}")

    # ------------------------------------------------------------------ guardar

    def _reparar(self):
        """Si la compu se apagó a mitad de un guardado, deja todo coherente: descarta las detecciones
        de frames que figuran como no procesados (se vuelven a procesar) y lo que apunte a ellas."""
        pos = {n: i for i, n in enumerate(self.frames)}
        sig = self.estado.get("siguiente", 0)
        de_mas = [c for c, d in self.dets.items() if pos.get(d["frame"], 10 ** 9) >= sig]
        for c in de_mas:
            del self.dets[c]
        for rid in list(self.recs):
            r = self.recs[rid]
            vistas = set()
            r["claves"] = [c for c in r["claves"] if c in self.dets and not (c in vistas or vistas.add(c))]
            if not r["claves"]:
                del self.recs[rid]
        vivos = set(self.recs)
        self.estado["activos"] = [a for a in self.estado.get("activos", []) if a["id"] in vivos]
        self.emb_idx = {c: i for c, i in self.emb_idx.items() if c in self.dets}
        for rid, si in self.postergados.items():
            if rid in self.recs:
                self.recs[rid]["despues"] = si
        # que un recorrido nuevo nunca reciba el número de uno viejo que ya tenía etiqueta
        usados = [int(r[1:]) for r in list(self.recs) + list(self.etiquetas) if r[1:].isdigit()]
        self.estado["contador"] = max([self.estado.get("contador", 0)] + usados)
        if de_mas:
            print(f"  (reparado: {len(de_mas)} detecciones a medio guardar se vuelven a procesar)")

    def guardar(self, todo=True):
        """todo=True: todo (lo hace el proceso de fondo cada 25 frames, unir/dividir y al salir).
        todo=False: solo lo que marcás a mano (etiquetas, marcas, para después): rápido y seguro."""
        with self.lock:
            I.escribir_json(self.dir / "etiquetas.json", self.etiquetas)
            I.escribir_json(self.dir / "problemas.json", self.problemas)
            I.escribir_json(self.dir / "postergados.json", self.postergados)
            if getattr(self, "fotos_pendientes", None) is not None:
                self.fotos_pendientes.set()  # la carpeta recorridas_campo se pone al día sola
            if not todo:
                return
            tmp = self.dir / "emb.npy.tmp"
            with open(tmp, "wb") as f:
                np.save(f, self.E.astype(np.float32))
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.dir / "emb.npy")
            guardar_compacto(self.dir / "emb_idx.json", self.emb_idx)
            guardar_compacto(self.dir / "detecciones.json", self.dets)
            guardar_compacto(self.dir / "recorridos.json", self.recs)
            self.estado["tam"] = list(self.tam)
            I.escribir_json(self.dir / "estado.json", self.estado)  # último: marca hasta dónde está todo guardado

    # ------------------------------------------------------------------ huella / galería

    def _sanas(self, rid):
        """Recortes del recorrido que sirven como huella: con huella calculada y sin problema marcado."""
        return [c for c in self.recs[rid]["claves"] if c in self.emb_idx and c not in self.problemas]

    def _a_galeria(self, rid, vaca):
        for c in self._sanas(rid):
            self.galeria.agregar(c, self.E[self.emb_idx[c]], vaca, "")

    def _confirmadas_a_galeria(self):
        """Los recortes de los recorridos confirmados a mano se suman a la galería."""
        for rid, et in self.etiquetas.items():
            if et.get("accion") == "vaca" and rid in self.recs:
                self._a_galeria(rid, et["vaca"])

    def _sacar_de_galeria(self, rid):
        for c in self.recs.get(rid, {}).get("claves", []):
            self.galeria.quitar(c)

    def afinar(self):
        g = self.galeria
        X = [g.ref_emb] + [v["emb"][None] for v in g.conf.values()]
        y = [m["vaca"] for m in g.ref_meta] + [v["vaca"] for v in g.conf.values()]
        a = I.Afinador.entrenar(np.concatenate(X), y)
        g.poner_afinador(a)
        self.desde_afinado = 0
        if a is not None:
            self.info_afinado = {"vacas": a.n_vacas, "muestras": a.n_muestras, "del_campo": len(g.conf)}
            print(f"Huella afinada: {a.n_muestras} fotos/recortes de {a.n_vacas} vacas ({len(g.conf)} del campo)")

    def _revisar_modelo_huella(self):
        """Si las huellas guardadas (emb.npy) las calculó otro modelo, las recalcula todas con el
        actual: huellas de modelos distintos no se pueden comparar entre sí."""
        ruta = self.dir / "emb_modelo.json"
        if ruta.exists():
            anterior = I.leer_json(ruta, {}).get("firma")
        else:
            # antes de existir este archivo, todas las huellas eran del DINOv2 original
            anterior = I.MODELO_EMB if len(self.emb_idx) else self.emb.firma
        if anterior != self.emb.firma and self.emb_idx:
            self._recalcular_huellas(anterior)
        I.escribir_json(ruta, {"firma": self.emb.firma})

    def _recalcular_huellas(self, anterior):
        claves = sorted(self.emb_idx, key=lambda c: self.emb_idx[c])
        por_frame = {}
        for c in claves:
            por_frame.setdefault(self.dets[c]["frame"], []).append(c)
        print(f"\nCambió el modelo de la huella ({anterior} -> {self.emb.firma}).")
        print(f"Recalculando {len(claves)} huellas de {len(por_frame)} frames. La página arranca al terminar.")
        nuevo = np.zeros((len(claves), self.emb.dim), np.float32)
        fila = {c: i for i, c in enumerate(claves)}
        t0 = time.time()
        for n, (nombre, cs) in enumerate(por_frame.items(), 1):
            im = I.abrir_imagen(self.dir_frames / nombre)
            polis = self.segmentador.siluetas(im, [self.dets[c]["box"] for c in cs])
            recortes = [I.recortar_con_mascara(im, self.dets[c]["box"], p) for c, p in zip(cs, polis)]
            for c, e in zip(cs, self.emb(recortes)):
                nuevo[fila[c]] = e
            del im
            if n % 50 == 0 or n == len(por_frame):
                resta = (time.time() - t0) / n * (len(por_frame) - n) / 60
                print(f"  {n}/{len(por_frame)} frames  (faltan ~{resta:.0f} min)", flush=True)
        self.E, self.emb_idx = nuevo, fila
        self.guardar()
        print("Huellas recalculadas y guardadas.\n")

    def _embeber(self, claves):
        """Huella (SAM + DINOv2) de esas detecciones; agrupa por frame para abrir cada imagen una vez."""
        por_frame = {}
        for c in claves:
            if c not in self.emb_idx:
                por_frame.setdefault(self.dets[c]["frame"], []).append(c)
        for nombre, cs in por_frame.items():
            im = I.abrir_imagen(self.dir_frames / nombre)
            with self.lock_mod:
                polis = self.segmentador.siluetas(im, [self.dets[c]["box"] for c in cs])
                recortes = [I.recortar_con_mascara(im, self.dets[c]["box"], p) for c, p in zip(cs, polis)]
                E = self.emb(recortes)
            with self.lock:
                for c, e in zip(cs, E):
                    self.emb_idx[c] = len(self.E)
                    self.E = np.concatenate([self.E, e[None]])
            del im

    def _elegir(self, claves, k):
        """k recortes del recorrido: entre los más grandes, repartidos en el tiempo."""
        claves = [c for c in claves if c not in self.problemas] or claves
        validas = [c for c in claves if not self.dets[c].get("borde")] or claves
        grandes = sorted(validas, key=lambda c: -area(self.dets[c]["box"]))[:max(k * 3, k)]
        grandes.sort(key=lambda c: self.dets[c]["t"])
        if len(grandes) <= k:
            return grandes
        paso = (len(grandes) - 1) / (k - 1) if k > 1 else 0
        return [grandes[round(i * paso)] for i in range(k)]

    # ------------------------------------------------------------------ seguimiento (fondo)

    def _nuevo_id(self):
        self.estado["contador"] += 1
        return f"r{self.estado['contador']:05d}"

    def _seguir(self, nombre, t, ds):
        """Une cada detección del frame con un recorrido activo (centroide + velocidad)."""
        activos = [a for a in self.estado["activos"] if t - a["t"] <= self.args.hueco]
        cerrados = [a["id"] for a in self.estado["activos"] if t - a["t"] > self.args.hueco]
        pares = []
        for j, d in enumerate(ds):
            cx, cy = centro(d["box"])
            for k, a in enumerate(activos):
                dt = t - a["t"]
                px, py = a["c"][0] + a["v"][0] * dt, a["c"][1] + a["v"][1] * dt
                dist = math.hypot(cx - px, cy - py) / max(diag(d["box"]), 1)
                razon = area(d["box"]) / max(area(a["box"]), 1)
                if dist <= DIST_MAX and TAM_MIN <= razon <= TAM_MAX:
                    pares.append((dist, j, k))
        usados_d, usados_a = set(), set()
        asignacion = {}
        for dist, j, k in sorted(pares):
            if j in usados_d or k in usados_a:
                continue
            usados_d.add(j)
            usados_a.add(k)
            asignacion[j] = k
        nuevos_activos = [a for k, a in enumerate(activos) if k not in usados_a]
        for j, d in enumerate(ds):
            clave = f"{nombre}|{j}"
            encimada = any(I.iou(d["box"], o["box"]) > AMONTONADO_IOU for o in ds if o is not d)
            if j in asignacion:
                a = activos[asignacion[j]]
                dt = max(t - a["t"], 1e-3)
                c = centro(d["box"])
                v = ((c[0] - a["c"][0]) / dt, (c[1] - a["c"][1]) / dt)
                a.update(c=c, v=(0.5 * a["v"][0] + 0.5 * v[0], 0.5 * a["v"][1] + 0.5 * v[1]), t=t, box=d["box"])
                rid = a["id"]
                nuevos_activos.append(a)
            else:
                rid = self._nuevo_id()
                self.recs[rid] = {"claves": [], "estado": "abierto", "despues": False}
                nuevos_activos.append({"id": rid, "c": centro(d["box"]), "v": (0.0, 0.0), "t": t, "box": d["box"]})
            borde = d["box"][0] <= 3 or d["box"][1] <= 3 or d["box"][2] >= self.tam[0] - 3 or d["box"][3] >= self.tam[1] - 3
            self.dets[clave] = {"frame": nombre, "t": round(t, 3), "box": [round(v, 1) for v in d["box"]],
                                "conf": round(d["conf"], 3), "rec": rid, "encimada": encimada, "borde": borde}
            self.recs[rid]["claves"].append(clave)
        self.estado["activos"] = nuevos_activos
        return cerrados

    def _cerrar(self, rid):
        """La vaca dejó de verse: se calcula la huella de sus mejores recortes y queda lista."""
        r = self.recs.get(rid)
        if r is None or r["estado"] != "abierto":
            return
        if len(r["claves"]) < self.args.min_frames:
            r["estado"] = "corto"
            return
        self._embeber(self._elegir(r["claves"], self.args.por_recorrido))
        with self.lock:
            enc = sum(self.dets[c]["encimada"] for c in r["claves"]) / len(r["claves"])
            r["amontonado"] = enc >= AMONTONADO_FRAC
            if r["amontonado"]:
                r["despues"] = True
            r["estado"] = "listo"

    def procesar(self):
        """Hilo de fondo: recorre los frames que faltan."""
        t_inicio, n = time.time(), 0
        while self.estado["siguiente"] < len(self.frames) and not self.parar:
            i = self.estado["siguiente"]
            nombre = self.frames[i]
            try:
                im = I.abrir_imagen(self.dir_frames / nombre)
                self.tam = im.size
                with self.lock_mod:
                    ds = [d for d in self.detector.detectar(im)
                          if min(d["box"][2] - d["box"][0], d["box"][3] - d["box"][1]) >= self.args.min_lado]
                del im
            except Exception as e:
                print(f"  [AVISO] {nombre}: {e}")
                ds = []
            with self.lock:
                cerrados = self._seguir(nombre, I.tiempo_de(nombre), ds)
                self.estado["siguiente"] = i + 1
            for rid in cerrados:
                self._cerrar(rid)
            n += 1
            self.velocidad = (time.time() - t_inicio) / n
            if n % 25 == 0:
                self.guardar()
            if self.args.pausa > 0:
                time.sleep(self.args.pausa)
        if self.estado["siguiente"] >= len(self.frames):  # fin del video: se cierran los que quedan
            for a in list(self.estado["activos"]):
                self._cerrar(a["id"])
            self.estado["activos"] = []
            print("Se terminaron de procesar todos los frames.")
        self.guardar()

    # ------------------------------------------------------------------ consultas

    def _tiempos(self, rid):
        cs = self.recs[rid]["claves"]
        return self.dets[cs[0]]["t"], self.dets[cs[-1]]["t"]

    def _ocupadas(self, rid):
        """Vacas asignadas a otro recorrido que se ve AL MISMO TIEMPO (no puede estar en dos lugares)."""
        t0, t1 = self._tiempos(rid)
        salida = set()
        for oid, et in self.etiquetas.items():
            if oid == rid or et.get("accion") != "vaca" or oid not in self.recs:
                continue
            o0, o1 = self._tiempos(oid)
            if o0 <= t1 and t0 <= o1:
                salida.add(et["vaca"])
        return salida

    def ranking(self, rid, k=5, filtrar=True, solo=None):
        """Vacas más parecidas al recorrido (o solo a los recortes `solo`, p. ej. el frame elegido)."""
        cs = [c for c in solo if c in self.emb_idx] if solo else self._sanas(rid)
        if not cs:
            return []
        Q = self.E[[self.emb_idx[c] for c in cs]]
        res = self.galeria.puntajes_grupo(Q, excluir_claves=self.recs[rid]["claves"])
        if filtrar:
            ocup = self._ocupadas(rid)
            res = [r for r in res if r[1] not in ocup]
        return res if k is None else res[:k]

    def _item(self, rid):
        r = self.recs[rid]
        t0, t1 = self._tiempos(rid)
        mejor = max(r["claves"], key=lambda c: area(self.dets[c]["box"]))
        tom = self.tomados.get(rid)
        return {"id": rid, "n": len(r["claves"]), "t0": t0, "t1": t1, "mejor": mejor,
                "etiqueta": self.etiquetas.get(rid), "amontonado": r.get("amontonado", False),
                "despues": r.get("despues", False),
                "tomado": tom["nombre"] if tom and time.time() - tom["ts"] < TOMA_SEG else None}

    def tomar(self, rid, nombre):
        """Alguien del equipo abrió este recorrido: los demás lo ven como ocupado (uno por persona)."""
        nombre = (nombre or "").strip()[:40]
        if not nombre:
            return {}
        with self.lock:
            for otro in [k for k, t in self.tomados.items() if t["nombre"] == nombre]:
                del self.tomados[otro]
            if rid in self.recs and rid not in self.etiquetas:
                self.tomados[rid] = {"nombre": nombre, "ts": time.time()}
        return {}

    def _estadisticas(self):
        s = {"n": 0, "top1": 0, "top5": 0, "nuevas": 0, "descartados": 0,
             "por_motivo": {k: 0 for k in MOTIVOS_DESCARTE}, "frames_marcados": {k: 0 for k in MOTIVOS_FRAME}}
        for et in self.etiquetas.values():
            if et["accion"] == "vaca":
                p = et.get("posicion") or 0
                s["n"] += 1
                s["top1"] += int(p == 1)
                s["top5"] += int(1 <= p <= 5)
                s["nuevas"] += int(p == 0)
            elif et["accion"] == "descartar":
                s["descartados"] += 1
                m = et.get("motivo", "otro")
                s["por_motivo"][m] = s["por_motivo"].get(m, 0) + 1
        for p in self.problemas.values():
            s["frames_marcados"][p["motivo"]] = s["frames_marcados"].get(p["motivo"], 0) + 1
        return s

    def resumen(self):
        with self.lock:
            listos = [rid for rid, r in self.recs.items() if r["estado"] == "listo"]
            items = sorted((self._item(rid) for rid in listos), key=lambda x: x["t0"])
            return {
                "progreso": {"hechos": self.estado["siguiente"], "total": len(self.frames),
                             "seg_por_frame": round(self.velocidad, 2) if self.velocidad else None,
                             "abiertos": len(self.estado["activos"]),
                             "cortos": sum(r["estado"] == "corto" for r in self.recs.values())},
                "afinado": self.info_afinado, "estadisticas": self._estadisticas(),
                "vacas": self.galeria.vacas(),
                "pendientes": [x for x in items if not x["etiqueta"] and not x["despues"]],
                "hechos": [x for x in items if x["etiqueta"]],
                "despues": [x for x in items if not x["etiqueta"] and x["despues"]],
            }

    def recorrido_json(self, rid):
        with self.lock:
            if rid not in self.recs:
                raise ValueError("Ese recorrido ya no existe (¿se unió o dividió?).")
            r = self.recs[rid]
            # también los frames sacados por "es otra vaca", para poder deshacerlo
            sacados = [c for c, p in self.problemas.items() if p.get("rec") == rid and c not in r["claves"]]
            claves = sorted(r["claves"] + sacados, key=lambda c: (self.dets[c]["t"], c))
            dets = [{"clave": c, "frame": self.dets[c]["frame"], "t": self.dets[c]["t"], "box": self.dets[c]["box"],
                     "huella": c in self.emb_idx, "problema": self.problemas.get(c, {}).get("motivo")} for c in claves]
            cands = [{"vaca": v, "score": round(s, 4), "t": t, "id": i} for s, v, (t, i) in self.ranking(rid)]
            return {**self._item(rid), "dets": dets, "candidatos": cands, "ancho": self.tam[0], "alto": self.tam[1],
                    "motivos_frame": MOTIVOS_FRAME, "motivos_descarte": MOTIVOS_DESCARTE}

    def _fotos_por_lado(self):
        """{vaca: {"A": índice de referencia, "B": índice}}. Prefiere las fotos de Asignaciones
        (vaca_A / vaca_B) y si no hay, un recorte de los videos de ese lado (bib_LADOA / bib_LADOB)."""
        if getattr(self, "_lados", None) is None:
            lados = {}
            for i, m in enumerate(self.galeria.ref_meta):
                nombre = Path(m["rel"]).name
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
                else:  # vlcsnap: las capturas del lado A miden 832x464, las del B 1024x576
                    try:
                        from PIL import Image
                        with Image.open(Path("Asignaciones (Verificado)") / m["rel"]) as im:
                            lado = "B" if im.size[0] >= 1000 else "A"
                    except OSError:
                        lado = "A"
                    prioridad = 0
                previo = lados.setdefault(m["vaca"], {}).get(lado)
                if previo is None or prioridad < previo[1]:
                    lados[m["vaca"]][lado] = (i, prioridad)
            self._lados = {v: {l: i for l, (i, _) in d.items()} for v, d in lados.items()}
        return self._lados

    def _asegurar_huella(self, rid, clave):
        """Calcula la huella de ese frame si todavía no la tiene (SAM + DINOv2, unos segundos).
        Si el recorrido ya está asignado a una vaca, el recorte nuevo se suma a su galería."""
        with self.lock:
            if rid not in self.recs or clave not in self.recs[rid]["claves"]:
                raise ValueError("Ese frame no es de este recorrido.")
            falta = clave not in self.emb_idx
        if falta:
            self._embeber([clave])  # sin tener self.lock tomado (evita trabarse con el proceso de fondo)
            with self.lock:
                et = self.etiquetas.get(rid)
                if et and et.get("accion") == "vaca" and clave not in self.problemas:
                    self.galeria.agregar(clave, self.E[self.emb_idx[clave]], et["vaca"], "")

    def analizar(self, rid, clave):
        """Las 5 vacas más parecidas según UN frame (el que eligió el usuario porque se ve mejor)."""
        self._asegurar_huella(rid, clave)
        with self.lock:
            cands = [{"vaca": v, "score": round(s, 4), "t": t, "id": i}
                     for s, v, (t, i) in self.ranking(rid, solo=[clave])]
            return {"clave": clave, "candidatos": cands, "recorrido": self.recorrido_json(rid)}

    def todas_json(self, rid, clave=None):
        if clave:
            self._asegurar_huella(rid, clave)
        with self.lock:
            if rid not in self.recs:
                raise ValueError("Ese recorrido ya no existe.")
            ranking = self.ranking(rid, k=None, filtrar=False, solo=[clave] if clave else None)
            ocup = self._ocupadas(rid)
            lados = self._fotos_por_lado()
            # vista desde arriba: el recorte del campo más grande de cada vaca ya confirmada
            campo = {}
            for c, item in self.galeria.conf.items():
                if c in self.dets and (item["vaca"] not in campo or area(self.dets[c]["box"]) > area(self.dets[campo[item["vaca"]]]["box"])):
                    campo[item["vaca"]] = c

            def fotos(v):
                f = {l: ["r", i] for l, i in lados.get(v, {}).items()}
                if v in campo:
                    f["campo"] = ["c", campo[v]]
                return f

            vistas, vacas = set(), []
            for s, v, _ in ranking:
                vistas.add(v)
                vacas.append({"vaca": v, "score": round(s, 4), "fotos": fotos(v), "ocupada": v in ocup})
            vacas += [{"vaca": v, "score": None, "fotos": fotos(v), "ocupada": v in ocup}
                      for v in sorted(lados, key=I.orden_natural) if v not in vistas]
            return {"vacas": vacas, "mejor": self._item(rid)["mejor"]}

    # ------------------------------------------------------------------ acciones

    def etiquetar(self, rid, accion, vaca=None, motivo=None, por=None):
        with self.lock:
            self.tomados.pop(rid, None)
            if rid not in self.recs:
                raise ValueError("Ese recorrido ya no existe.")
            vaca = (vaca or "").strip()
            if accion == "vaca" and not vaca:
                raise ValueError("Falta el número de vaca.")
            if accion == "descartar" and motivo and motivo not in MOTIVOS_DESCARTE:
                raise ValueError(f"Motivo inválido: {motivo}")
            self._sacar_de_galeria(rid)
            if accion == "borrar":
                self.etiquetas.pop(rid, None)
            else:
                et = {"accion": accion, "ts": round(time.time())}
                if por and str(por).strip():
                    et["por"] = str(por).strip()[:40]
                if accion == "vaca":
                    rk = self.ranking(rid, k=None, filtrar=False)
                    et.update(vaca=vaca, posicion=next((n + 1 for n, r in enumerate(rk) if r[1] == vaca), 0))
                    self._a_galeria(rid, vaca)
                    self.desde_afinado += 1
                elif accion == "descartar":
                    et["motivo"] = motivo or "otro"
                self.etiquetas[rid] = et
            if self.desde_afinado >= REAFINAR_CADA:
                self.afinar()
            self.guardar(todo=False)
            return self.resumen()

    def postergar(self, rid, si):
        with self.lock:
            if rid not in self.recs:
                raise ValueError("Ese recorrido ya no existe.")
            self.recs[rid]["despues"] = bool(si)
            self.postergados[rid] = bool(si)
            self.guardar(todo=False)
            return self.resumen()

    def marcar(self, rid, clave, motivo):
        """Marca qué está mal en UN frame del recorrido (motivo=None quita la marca).
        - cortada / dos_vacas / no_se_ve: el frame queda en el recorrido pero NO se usa como huella.
        - otra_vaca: el frame se saca del recorrido (no es esta vaca) y no recibe su número."""
        with self.lock:
            if motivo is not None and motivo not in MOTIVOS_FRAME:
                raise ValueError(f"Motivo inválido: {motivo}")
            previo = self.problemas.get(clave)
            if rid not in self.recs or (clave not in self.recs[rid]["claves"] and not (previo and previo.get("rec") == rid)):
                raise ValueError("Ese frame no es de este recorrido.")
            r = self.recs[rid]
            et = self.etiquetas.get(rid)
            self.galeria.quitar(clave)
            if previo and previo["motivo"] == "otra_vaca" and clave not in r["claves"]:  # vuelve al recorrido
                r["claves"] = sorted(r["claves"] + [clave], key=lambda c: (self.dets[c]["t"], c))
                self.dets[clave]["rec"] = rid
            if motivo is None:
                self.problemas.pop(clave, None)
                if et and et.get("accion") == "vaca" and clave in self.emb_idx:
                    self.galeria.agregar(clave, self.E[self.emb_idx[clave]], et["vaca"], "")
            else:
                if motivo == "otra_vaca":
                    if len(r["claves"]) <= 1:
                        raise ValueError("Es el único frame del recorrido: mejor descartá el recorrido entero.")
                    r["claves"] = [c for c in r["claves"] if c != clave]
                    self.dets[clave]["rec"] = None
                self.problemas[clave] = {"motivo": motivo, "rec": rid}
            sin_huella = not self._sanas(rid)
        if sin_huella:  # se quedó sin ningún recorte sano: se calcula la huella de otro
            self._embeber(self._elegir(self.recs[rid]["claves"], self.args.por_recorrido))
            with self.lock:
                if et and et.get("accion") == "vaca":
                    self._a_galeria(rid, et["vaca"])
        with self.lock:
            self.guardar(todo=motivo == "otra_vaca" or (previo or {}).get("motivo") == "otra_vaca")
            return self.recorrido_json(rid)

    def unir(self, a, b):
        """Une dos recorridos (son la misma vaca). Queda el que empieza antes."""
        with self.lock:
            if a not in self.recs or b not in self.recs or a == b:
                raise ValueError("Elegí dos recorridos distintos.")
            if self._tiempos(b)[0] < self._tiempos(a)[0]:
                a, b = b, a
            ea, eb = self.etiquetas.get(a), self.etiquetas.get(b)
            self._sacar_de_galeria(a)
            self._sacar_de_galeria(b)
            claves = sorted(self.recs[a]["claves"] + self.recs[b]["claves"], key=lambda c: (self.dets[c]["t"], c))
            self.recs[a]["claves"] = claves
            self.recs[a]["despues"] = self.recs[a].get("despues") and self.recs[b].get("despues")
            for c in claves:
                self.dets[c]["rec"] = a
            for p in self.problemas.values():
                if p.get("rec") == b:
                    p["rec"] = a
            del self.recs[b]
            self.etiquetas.pop(b, None)
            self.postergados.pop(b, None)
            et = ea or eb
            if et:
                self.etiquetas[a] = et
                if et.get("accion") == "vaca":
                    self._a_galeria(a, et["vaca"])
            else:
                self.etiquetas.pop(a, None)
            self.guardar()
            return {"resumen": self.resumen(), "id": a}

    def dividir(self, rid, clave):
        """Corta el recorrido: desde `clave` en adelante pasa a ser un recorrido nuevo."""
        with self.lock:
            if rid not in self.recs or clave not in self.recs[rid]["claves"]:
                raise ValueError("Ese frame no es de este recorrido.")
            cs = self.recs[rid]["claves"]
            i = cs.index(clave)
            if i == 0:
                raise ValueError("Elegí un frame del medio: desde ese frame empieza el recorrido nuevo.")
            et = self.etiquetas.pop(rid, None)
            self._sacar_de_galeria(rid)
            nuevo = self._nuevo_id()
            self.recs[rid]["claves"], resto = cs[:i], cs[i:]
            self.recs[nuevo] = {"claves": resto, "estado": "listo", "despues": False,
                                "amontonado": self.recs[rid].get("amontonado", False)}
            for c in resto:
                self.dets[c]["rec"] = nuevo
            faltan = [p for p in (rid, nuevo) if not self._sanas(p)]
        for p in faltan:  # la parte que quedó sin huella la calcula ahora
            self._embeber(self._elegir(self.recs[p]["claves"], self.args.por_recorrido))
        with self.lock:
            if et:  # la etiqueta queda en la primera parte; la segunda hay que revisarla
                self.etiquetas[rid] = et
                if et.get("accion") == "vaca":
                    self._a_galeria(rid, et["vaca"])
            self.guardar()
            return {"resumen": self.resumen(), "id": rid, "nuevo": nuevo}

    def exportar(self):
        with self.lock:
            ruta = self.dir / "resultado_por_frame.csv"
            n = 0
            with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["frame", "segundo", "x1", "y1", "x2", "y2", "recorrido", "vaca", "problema"])
                for rid, et in sorted(self.etiquetas.items()):
                    if et.get("accion") != "vaca" or rid not in self.recs:
                        continue
                    for c in self.recs[rid]["claves"]:
                        d = self.dets[c]
                        prob = self.problemas.get(c, {}).get("motivo", "")
                        w.writerow([d["frame"], d["t"], *d["box"], rid, et["vaca"], MOTIVOS_FRAME.get(prob, "")])
                        n += 1
            return {"archivo": str(ruta.resolve()), "filas": n}

    # ------------------------------------------------------------------ fotos por vaca (recorridas_campo)

    def _foto_con_recuadro(self, clave, vaca, destino):
        """Guarda el frame completo con el recuadro de la vaca dibujado y su número."""
        from PIL import ImageDraw, ImageFont
        d = self.dets[clave]
        im = I.abrir_imagen(self.dir_frames / d["frame"])
        dib = ImageDraw.Draw(im)
        prob = self.problemas.get(clave, {}).get("motivo")
        color = (230, 60, 40) if prob else (255, 205, 0)
        x1, y1, x2, y2 = d["box"]
        grosor = max(3, im.width // 300)
        dib.rectangle([x1, y1, x2, y2], outline=color, width=grosor)
        try:
            fuente = ImageFont.truetype("arialbd.ttf", max(22, im.width // 55))
        except OSError:
            fuente = ImageFont.load_default()
        texto = f"#{vaca}" + (f"  ({MOTIVOS_FRAME[prob].split('(')[0].strip().lower()})" if prob else "")
        alto_txt = fuente.size + 10
        tx = min(max(0, x1), im.width - 10)
        ty = y1 - alto_txt if y1 - alto_txt >= 0 else min(y2 + 6, im.height - alto_txt)  # sin lugar arriba: abajo
        caja = dib.textbbox((tx, ty), texto, font=fuente)
        if caja[2] > im.width:  # que no se salga por la derecha
            tx -= caja[2] - im.width + 6
            caja = dib.textbbox((tx, ty), texto, font=fuente)
        dib.rectangle([caja[0] - 4, caja[1] - 3, caja[2] + 4, caja[3] + 3], fill=color)
        dib.text((tx, ty), texto, fill=(0, 0, 0), font=fuente)
        if im.width > self.args.ancho_fotos:
            im = im.resize((self.args.ancho_fotos, round(im.height * self.args.ancho_fotos / im.width)))
        destino.parent.mkdir(parents=True, exist_ok=True)
        im.save(destino, "JPEG", quality=85)

    def sincronizar_fotos(self, mostrar=False):
        """Deja la carpeta de fotos igual a lo marcado: agrega lo nuevo y borra lo que ya no corresponde.
        Lleva un índice (_indice.json) para saber qué archivo es de qué recorrido."""
        raiz = Path(self.args.fotos)
        raiz.mkdir(exist_ok=True)
        indice = I.leer_json(raiz / "_indice.json", {})   # id -> {"vaca", "archivos": {clave: ruta}}
        with self.lock:
            deseado = {rid: {"vaca": et["vaca"], "claves": list(self.recs[rid]["claves"])}
                       for rid, et in self.etiquetas.items() if et.get("accion") == "vaca" and rid in self.recs}
        cambios = 0
        for rid in list(indice):  # 1) borrar lo que ya no corresponde
            m, d = indice[rid], deseado.get(rid)
            if d is None or d["vaca"] != m["vaca"]:
                for ruta in m["archivos"].values():
                    Path(ruta).unlink(missing_ok=True)
                del indice[rid]
                cambios += 1
                continue
            for c in [c for c in m["archivos"] if c not in d["claves"]]:
                Path(m["archivos"].pop(c)).unlink(missing_ok=True)
                cambios += 1
        faltan = sum(len([c for c in d["claves"] if c not in indice.get(rid, {}).get("archivos", {})])
                     for rid, d in deseado.items())
        hechas = 0
        for rid, d in deseado.items():  # 2) agregar lo nuevo
            m = indice.setdefault(rid, {"vaca": d["vaca"], "archivos": {}})
            carpeta = raiz / I.nombre_seguro(d["vaca"])
            for c in d["claves"]:
                if c in m["archivos"] or (self.parar and not mostrar):
                    continue
                det = self.dets[c]
                t = int(det["t"])
                ruta = carpeta / f"{rid}_{t // 60:02d}m{t % 60:02d}s_{Path(det['frame']).stem}.jpg"
                try:
                    self._foto_con_recuadro(c, d["vaca"], ruta)
                    m["archivos"][c] = str(ruta)
                except Exception as e:
                    print(f"  [AVISO] No se pudo guardar la foto de {c}: {e}")
                hechas += 1
                cambios += 1
                if mostrar and hechas % 50 == 0:
                    print(f"  fotos: {hechas}/{faltan}")
                if hechas % 25 == 0:
                    I.escribir_json(raiz / "_indice.json", indice)
                time.sleep(0.02)
        for carpeta in raiz.iterdir():  # carpetas de vacas que quedaron vacías
            if carpeta.is_dir() and not any(carpeta.iterdir()):
                carpeta.rmdir()
        if cambios:
            I.escribir_json(raiz / "_indice.json", indice)
        return hechas

    def guardar_fotos(self):
        """Hilo de fondo: cada vez que marcás algo, actualiza la carpeta de fotos (sin apurar a la compu)."""
        while not self.parar:
            if self.fotos_pendientes.wait(timeout=20):
                self.fotos_pendientes.clear()
                try:
                    self.sincronizar_fotos()
                except Exception as e:
                    print(f"  [AVISO] Fotos por vaca: {e}")

    # ------------------------------------------------------------------ imágenes

    def img_recorte(self, clave, lado=200):
        k = (clave, lado)
        if k not in self._cache_img:
            d = self.dets.get(clave)
            if d is None:
                return None
            im = I.abrir_imagen(self.dir_frames / d["frame"])
            self._cache_img[k] = I.a_jpg(I.recortar(im, d["box"]), lado)
            if len(self._cache_img) > 3000:
                self._cache_img.clear()
        return self._cache_img[k]

    def img_galeria(self, tipo, ident):
        if tipo == "c":
            return self.img_recorte(ident, 300)
        k = ("r", ident)
        if k not in self._cache_img:
            self._cache_img[k] = I.a_jpg(self.refs._imagen_referencia(self.galeria.ref_meta[int(ident)]), 260)
        return self._cache_img[k]


def crear_web(campo, clave=None):
    from flask import Flask, Response, abort, jsonify, request, send_file
    import hmac
    web = Flask(__name__)
    html = Path(__file__).with_name("interfaz_campo.html")

    if clave:
        @web.before_request
        def pedir_clave():
            """Contraseña del equipo (el navegador la pide una vez; el usuario puede ser cualquiera)."""
            a = request.authorization
            if a is None or not hmac.compare_digest((a.password or "").encode(), clave.encode()):
                return Response("Falta la contraseña del equipo.", 401,
                                {"WWW-Authenticate": 'Basic realm="Vacas Tres Lomas", charset="UTF-8"'})

    def jpg(datos):
        if datos is None:
            abort(404)
        return Response(datos, mimetype="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    def accion(fn):
        try:
            return jsonify(fn())
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @web.get("/")
    def inicio():
        return send_file(html)

    @web.get("/api/resumen")
    def api_resumen():
        return jsonify(campo.resumen())

    @web.get("/api/recorrido")
    def api_recorrido():
        return accion(lambda: campo.recorrido_json(request.args.get("id", "")))

    @web.get("/api/todas")
    def api_todas():
        return accion(lambda: campo.todas_json(request.args.get("id", ""), request.args.get("clave") or None))

    @web.post("/api/analizar")
    def api_analizar():
        d = request.get_json(force=True)
        return accion(lambda: campo.analizar(d.get("id"), d.get("clave")))

    @web.post("/api/etiquetar")
    def api_etiquetar():
        d = request.get_json(force=True)
        return accion(lambda: campo.etiquetar(d.get("id"), d.get("accion"), d.get("vaca"), d.get("motivo"), d.get("por")))

    @web.post("/api/tomar")
    def api_tomar():
        d = request.get_json(force=True)
        return accion(lambda: campo.tomar(d.get("id"), d.get("por")))

    @web.post("/api/marcar")
    def api_marcar():
        d = request.get_json(force=True)
        return accion(lambda: campo.marcar(d.get("id"), d.get("clave"), d.get("motivo")))

    @web.post("/api/postergar")
    def api_postergar():
        d = request.get_json(force=True)
        return accion(lambda: campo.postergar(d.get("id"), d.get("si")))

    @web.post("/api/unir")
    def api_unir():
        d = request.get_json(force=True)
        return accion(lambda: campo.unir(d.get("a"), d.get("b")))

    @web.post("/api/dividir")
    def api_dividir():
        d = request.get_json(force=True)
        return accion(lambda: campo.dividir(d.get("id"), d.get("clave")))

    @web.post("/api/exportar")
    def api_exportar():
        return jsonify(campo.exportar())

    @web.get("/img/frame/<path:nombre>")
    def img_frame(nombre):
        if nombre not in campo.set_frames:
            abort(404)
        return send_file(campo.dir_frames.resolve() / nombre, max_age=3600)

    @web.get("/img/recorte")
    def img_recorte():
        lado = min(max(int(request.args.get("lado", 200)), 80), 800)
        return jpg(campo.img_recorte(request.args.get("clave", ""), lado))

    @web.get("/img/galeria")
    def img_galeria():
        try:
            return jpg(campo.img_galeria(request.args.get("t"), request.args.get("id", "")))
        except (IndexError, ValueError, KeyError):
            abort(404)

    return web


def ip_local():
    """La IP de esta compu en la red WiFi (la que usan los demás para entrar)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no manda nada: solo elige la placa de red
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    p = argparse.ArgumentParser(description="Identificación por recorrido en el video del campo.")
    p.add_argument("--frames", default="frames_desfile_camaras")
    p.add_argument("--trabajo", default="campo")
    p.add_argument("--por-recorrido", type=int, default=4)
    p.add_argument("--min-frames", type=int, default=3)
    p.add_argument("--min-lado", type=int, default=70)
    p.add_argument("--hueco", type=float, default=1.5)
    p.add_argument("--hilos", type=int, default=2)
    p.add_argument("--pausa", type=float, default=0.1)
    p.add_argument("--puerto", type=int, default=5004)
    p.add_argument("--fotos", default="recorridas_campo")
    p.add_argument("--ancho-fotos", type=int, default=1280)
    p.add_argument("--red", action="store_true", help="que entren otras compus de la misma red WiFi")
    p.add_argument("--clave", default=os.environ.get("CAMPO_CLAVE"),
                   help="contraseña para entrar a la página (o la variable de entorno CAMPO_CLAVE)")
    p.add_argument("--sin-navegador", action="store_true", help="no abrir el navegador (en un servidor)")
    p.add_argument("--modelo-huella", default="dinov2_vacas.pt",
                   help='pesos de DINOv2 entrenado ("original" = el de fábrica)')
    args = p.parse_args()
    if args.red and not args.clave:
        print("  (aviso: sin --clave, cualquiera en la red WiFi puede entrar y marcar)")
    if not Path(args.frames).is_dir():
        print(f"[ERROR] No existe la carpeta {Path(args.frames).resolve()}")
        sys.exit(1)
    if not Path(__file__).with_name("interfaz_campo.html").exists():
        print("[ERROR] Falta interfaz_campo.html al lado de este script.")
        sys.exit(1)

    campo = Campo(args)
    hilo = threading.Thread(target=campo.procesar, daemon=True)
    hilo.start()
    hilo_fotos = threading.Thread(target=campo.guardar_fotos, daemon=True)
    hilo_fotos.start()
    web = crear_web(campo, args.clave)
    url = f"http://127.0.0.1:{args.puerto}"
    print(f"\nListo. Abrí {url} (los recorridos van apareciendo mientras se procesa). Ctrl+C para cortar.")
    if args.red:
        print(f"Para el equipo (misma red WiFi): http://{ip_local()}:{args.puerto}")
        print("  Si Windows pregunta si Python puede usar la red, aceptá en 'Redes privadas'.")
    if not args.sin_navegador:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        web.run(host="0.0.0.0" if args.red else "127.0.0.1", port=args.puerto, threaded=True)
    finally:
        campo.parar = True
        campo.fotos_pendientes.set()
        hilo.join(timeout=30)
        hilo_fotos.join(timeout=30)
        campo.guardar()
        print(f"Avance guardado en {campo.dir.resolve()}")
        print(f"Terminando de guardar las fotos de las vacas marcadas en {Path(args.fotos).resolve()} ...")
        n = campo.sincronizar_fotos(mostrar=True)
        print(f"Listo ({n} fotos nuevas). Ya podés apagar la compu.")


if __name__ == "__main__":
    main()
