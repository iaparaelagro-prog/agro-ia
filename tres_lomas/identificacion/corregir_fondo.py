"""
corregir_fondo.py
Corrige las fotos de Asignaciones que marcaste como "Mal" en la revisión de fondos.

Cómo funciona:
  - Usa SAM (Segment Anything): en vez de adivinar qué vaca es, se le marcan puntos:
      · puntos VERDES  = "esta es la vaca"
      · puntos ROJOS   = "esto NO" (otra vaca, una persona, una baranda)
    y recorta la silueta completa de esa vaca (con patas y cabeza).
  - Claude ya miró las 22 fotos malas y dejó cargados los puntos de cada una (PUNTOS_INICIALES),
    así que al arrancar las corrige todas solas.
  - Después se abre una página para revisar. Si alguna sigue mal, la arreglás con clics:
      · clic izquierdo      -> agrega punto verde (vaca)
      · clic derecho        -> agrega punto rojo (no)
      · clic sobre un punto -> lo borra
    y se recalcula sola.
  - Los resultados reemplazan los PNG en "Asignaciones_sin_fondo" (mismo formato que quitar_fondo.py)
    y lo que aprobás queda como "bien" en revision.json. Las fotos originales NO se tocan.

Liviano: una foto por vez, fotos achicadas a 1600 px, 2 núcleos.

Uso (desde la carpeta desfile_tres_lomas):
    python corregir_fondo.py
Se abre http://127.0.0.1:5002 (Ctrl+C para cortar). Se puede cortar y retomar.

Opciones:
    --referencias  carpeta con las fotos originales  (default: "Asignaciones (Verificado)")
    --salida       carpeta de quitar_fondo.py        (default: Asignaciones_sin_fondo)
    --sam          modelo SAM                        (default: sam2.1_t.pt; alternativa: mobile_sam.pt)
    --max-lado     tamaño máximo de las fotos en px  (default: 1600)
    --hilos        núcleos del procesador            (default: 2)
    --rehacer      vuelve a aplicar los puntos iniciales aunque ya estén corregidas
    --puerto       puerto de la página               (default: 5002)
"""

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

LADO_VISTA = 600

# Puntos marcados por Claude mirando las hojas de fotos malas.
# Coordenadas relativas: x e y van de 0 a 1 (0,0 = arriba a la izquierda). l=1 vaca, l=0 no.
def _p(pos, neg=()):
    return [{"x": x, "y": y, "l": 1} for x, y in pos] + [{"x": x, "y": y, "l": 0} for x, y in neg]

PUNTOS_INICIALES = {
    "3855/3855_A.png": _p([(0.30, 0.46), (0.12, 0.53), (0.59, 0.59)], [(0.89, 0.44), (0.72, 0.35)]),
    "3960/3960_A.png": _p([(0.47, 0.52), (0.10, 0.61)], [(0.91, 0.43), (0.81, 0.78)]),
    "4222/4222_A.png": _p([(0.54, 0.46), (0.25, 0.46), (0.57, 0.77)], [(0.84, 0.46), (0.95, 0.51)]),
    "4516/4516_A.png": _p([(0.42, 0.37), (0.12, 0.76), (0.72, 0.85)], [(0.94, 0.28)]),
    "4864/4864_A.png": _p([(0.67, 0.36), (0.54, 0.45)], [(0.22, 0.40)]),
    "4879/4879_A.png": _p([(0.49, 0.44), (0.37, 0.48), (0.59, 0.70)], [(0.86, 0.53)]),
    "4901/4901_A.png": _p([(0.54, 0.46), (0.40, 0.48)], [(0.22, 0.48)]),
    "4924/4924_A.png": _p([(0.42, 0.43), (0.59, 0.52)], [(0.04, 0.52), (0.96, 0.52)]),
    "4963/4963_A.png": _p([(0.37, 0.60), (0.25, 0.55)], [(0.81, 0.51)]),
    "5027/5027_A.png": _p([(0.35, 0.46), (0.17, 0.54), (0.44, 0.87)], [(0.81, 0.46)]),
    "5191/5191_A.png": _p([(0.30, 0.40), (0.12, 0.54)], [(0.86, 0.40), (0.69, 0.32)]),
    "5242/5242_A.png": _p([(0.47, 0.44), (0.35, 0.48), (0.54, 0.79)], [(0.07, 0.48), (0.84, 0.44)]),
    "5401/5401_A.png": _p([(0.37, 0.46), (0.25, 0.48), (0.30, 0.83)], [(0.89, 0.53), (0.79, 0.79)]),
    "X805/X805_A.png": _p([(0.47, 0.48), (0.30, 0.69)], [(0.04, 0.52), (0.95, 0.43)]),
    "XX50/XX50_A.png": _p([(0.40, 0.42), (0.59, 0.55), (0.10, 0.45)], [(0.80, 0.55)]),
    "5234/5234_B.png": _p([(0.62, 0.29), (0.14, 0.57), (0.59, 0.77)]),
    "5116/5116_B.png": _p([(0.59, 0.49), (0.42, 0.54), (0.33, 0.69)], [(0.40, 0.43), (0.12, 0.54)]),
    "4885/4885_B.png": _p([(0.67, 0.39), (0.47, 0.53), (0.23, 0.53)], [(0.12, 0.18), (0.05, 0.31)]),
    "4454/vlcsnap-2026-10-05-20h17m48s672.png": _p([(0.32, 0.46), (0.48, 0.50)], [(0.82, 0.46)]),
    "3791/3791_B.png": _p([(0.66, 0.42), (0.34, 0.58), (0.23, 0.66)], [(0.07, 0.34)]),
    "3782/3782_B.png": _p([(0.57, 0.28), (0.43, 0.50), (0.10, 0.44)], [(0.02, 0.19), (0.25, 0.13)]),
    "3739/3739_B.png": _p([(0.48, 0.54), (0.66, 0.50), (0.28, 0.58)], [(0.95, 0.48), (0.91, 0.78)]),
}


# ----------------------------------------------------------------------------- utilidades

def leer_json(ruta, defecto):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def escribir_json(ruta, datos):
    ruta = Path(ruta)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ruta)


def abrir(ruta, max_lado):
    im = Image.open(ruta)
    im.draft("RGB", (max_lado, max_lado))
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    im.thumbnail((max_lado, max_lado))
    return im


def orden_natural(texto):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(texto))]


# ----------------------------------------------------------------------------- recorte con SAM

class Recortador:
    def __init__(self, args):
        import cv2
        import torch
        from ultralytics import SAM
        torch.set_num_threads(args.hilos)
        cv2.setNumThreads(1)
        self.cv2 = cv2
        self.torch = torch
        self.args = args
        self.dir_ref = Path(args.referencias)
        self.dir_salida = Path(args.salida)
        print(f"Cargando SAM ({args.sam})...")
        self.sam = SAM(args.sam)
        self.lock = threading.Lock()

    def _limpiar(self, mask, pos_px):
        """Deja la parte principal de la máscara + los pedazos donde hay puntos verdes; cierra agujeritos."""
        cv2 = self.cv2
        m = mask.astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
        if n <= 2:
            return m.astype(bool)
        areas = stats[1:, cv2.CC_STAT_AREA]
        mayor = areas.max()
        quedan = {i + 1 for i, a in enumerate(areas) if a >= 0.30 * mayor}
        for x, y in pos_px:
            if lab[y, x]:
                quedan.add(int(lab[y, x]))
        return np.isin(lab, list(quedan))

    def recortar(self, rel, puntos, resultados):
        pos = [p for p in puntos if p["l"] == 1]
        if not pos:
            raise ValueError("Poné al menos un punto verde sobre la vaca.")
        im = abrir(self.dir_ref / rel, self.args.max_lado)
        W, H = im.size

        def px(p):
            return [int(min(W - 1, max(0, p["x"] * W))), int(min(H - 1, max(0, p["y"] * H)))]

        coords = [px(p) for p in puntos]
        labels = [p["l"] for p in puntos]

        with self.lock, self.torch.inference_mode():
            r = self.sam(im, points=[coords], labels=[labels], device="cpu", verbose=False)[0]
        if r.masks is None or len(r.masks.data) == 0:
            raise ValueError("SAM no devolvió ninguna máscara; probá con otros puntos.")
        m = r.masks.data[0].numpy() > 0.5
        del r
        if m.shape != (H, W):
            m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((W, H), Image.NEAREST)) > 127
        m = self._limpiar(m, [px(p) for p in pos])

        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            raise ValueError("La máscara quedó vacía; probá con otros puntos.")
        x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        alfa = Image.fromarray(m.astype(np.uint8) * 255, "L").filter(ImageFilter.GaussianBlur(1.2))
        del m

        mx, my = int((x2 - x1) * 0.03), int((y2 - y1) * 0.03)
        caja = (max(0, x1 - mx), max(0, y1 - my), min(W, x2 + mx), min(H, y2 + my))
        rgba = im.crop(caja)
        rgba.putalpha(alfa.crop(caja))

        base = Path(rel).with_suffix("")
        png_rel = Path(rel).with_suffix(".png").as_posix()
        vista_orig = (Path("_vistas") / f"{base}_orig.jpg").as_posix()
        vista_sf = (Path("_vistas") / f"{base}_sf.webp").as_posix()
        for sub in (png_rel, vista_orig, vista_sf):
            (self.dir_salida / sub).parent.mkdir(parents=True, exist_ok=True)

        rgba.save(self.dir_salida / png_rel)
        rgba.thumbnail((LADO_VISTA, LADO_VISTA))
        rgba.save(self.dir_salida / vista_sf, "WEBP", quality=80)

        box = [float(x1), float(y1), float(x2), float(y2)]
        ImageDraw.Draw(im).rectangle(box, outline=(230, 40, 40), width=max(4, W // 150))
        im.thumbnail((LADO_VISTA, LADO_VISTA))
        im.save(self.dir_salida / vista_orig, "JPEG", quality=82)

        previo = resultados.get(rel, {})
        resultados[rel] = {"vaca": previo.get("vaca", Path(rel).parts[0]), "estado": "ok",
                           "n_vacas": previo.get("n_vacas", 1), "box": box, "png": png_rel,
                           "vista_orig": vista_orig, "vista_sf": vista_sf, "metodo": "sam"}


# ----------------------------------------------------------------------------- página

HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Corregir fondos</title>
<style>
  :root{--bg:#f3f0e8;--tinta:#1f2a1f;--suave:#6b7466;--borde:#ddd6c8;--verde:#2f7d4a;--rojo:#b8452f}
  *{box-sizing:border-box}
  body{margin:0;font-family:"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--tinta)}
  header{position:sticky;top:0;z-index:5;background:var(--tinta);color:#fff;display:flex;gap:10px;
         align-items:center;padding:10px 16px;flex-wrap:wrap}
  header h1{font-size:16px;margin:0 8px 0 0}
  .chip{background:rgba(255,255,255,.13);padding:3px 10px;border-radius:999px;font-size:13px}
  .ayuda{font-size:12px;opacity:.8}
  main{padding:16px;display:flex;flex-direction:column;gap:16px;max-width:1400px;margin:0 auto}
  .item{background:#fff;border:2px solid var(--borde);border-radius:10px;padding:12px 14px}
  .item.bien{border-color:var(--verde);background:#f4faf5}
  .item.mal{border-color:var(--rojo)}
  .item h2{margin:0 0 8px;font-size:15px}
  .item h2 small{color:var(--suave);font-weight:400}
  .par{display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:start}
  .lienzo{position:relative;cursor:crosshair;line-height:0;border-radius:6px;overflow:hidden;background:#222}
  .lienzo img{width:100%;display:block;user-select:none;-webkit-user-drag:none}
  .punto{position:absolute;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;border:2px solid #fff;
         box-shadow:0 0 0 1px rgba(0,0,0,.6);cursor:pointer}
  .punto.si{background:#22c55e} .punto.no{background:#ef4444}
  .sf{width:100%;min-height:200px;max-height:520px;object-fit:contain;border-radius:6px;display:block;
      background-color:#fff;
      background-image:linear-gradient(45deg,#ddd 25%,transparent 25%),linear-gradient(-45deg,#ddd 25%,transparent 25%),
                       linear-gradient(45deg,transparent 75%,#ddd 75%),linear-gradient(-45deg,transparent 75%,#ddd 75%);
      background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}
  .pie{display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap}
  .estado{flex:1;font-size:13px;color:var(--suave);min-width:160px}
  .estado.error{color:var(--rojo)}
  button{font:inherit;cursor:pointer;border:1px solid var(--borde);background:#fff;color:var(--tinta);border-radius:8px;padding:6px 14px}
  .b-bien.on{background:var(--verde);color:#fff;border-color:var(--verde)}
  .b-mal.on{background:var(--rojo);color:#fff;border-color:var(--rojo)}
</style>
</head>
<body>
<header>
  <h1>Corregir fondos</h1>
  <span class="chip" id="c-prog"></span>
  <span class="ayuda">Clic izquierdo: <b style="color:#4ade80">● vaca</b> · Clic derecho: <b style="color:#f87171">● no</b> · Clic en un punto: borrarlo · Se recalcula solo</span>
</header>
<main id="lista">Cargando…</main>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let items = [];
const timers = {};

async function cargar(){
  items = await (await fetch("/api/lista")).json();
  $("#lista").innerHTML = items.map((it, i) => `
    <section class="item ${it.revision || ""}" data-i="${i}">
      <h2>#${i + 1} · Vaca ${esc(it.vaca)} <small>${esc(it.rel)}</small></h2>
      <div class="par">
        <div class="lienzo"><img src="/img/orig?rel=${encodeURIComponent(it.rel)}" draggable="false"><div class="puntos"></div></div>
        <img class="sf" src="/img/sinfondo?rel=${encodeURIComponent(it.rel)}&v=${it.v}" alt="">
      </div>
      <div class="pie">
        <span class="estado">${it.v ? "Corregida automáticamente — revisá el resultado" : "Marcá la vaca con un clic"}</span>
        <button data-acc="limpiar">Borrar puntos</button>
        <button data-acc="bien" class="b-bien ${it.revision === "bien" ? "on" : ""}">✔ Queda bien</button>
        <button data-acc="mal" class="b-mal ${it.revision === "mal" ? "on" : ""}">✘ Sigue mal</button>
      </div>
    </section>`).join("");
  document.querySelectorAll(".item").forEach(pintarPuntos);
  contador();
}

function contador(){
  const bien = items.filter(i => i.revision === "bien").length;
  $("#c-prog").textContent = `${bien} / ${items.length} aprobadas`;
}

function pintarPuntos(card){
  const it = items[+card.dataset.i];
  card.querySelector(".puntos").innerHTML = it.puntos.map((p, k) =>
    `<div class="punto ${p.l ? "si" : "no"}" data-k="${k}" style="left:${p.x*100}%;top:${p.y*100}%"></div>`).join("");
}

function estado(card, txt, error){
  const e = card.querySelector(".estado");
  e.textContent = txt;
  e.classList.toggle("error", !!error);
}

function marcar(card, valor){
  const it = items[+card.dataset.i];
  it.revision = valor;
  card.classList.remove("bien", "mal");
  if(valor) card.classList.add(valor);
  card.querySelector(".b-bien").classList.toggle("on", valor === "bien");
  card.querySelector(".b-mal").classList.toggle("on", valor === "mal");
  contador();
}

function programar(card){
  const i = +card.dataset.i;
  clearTimeout(timers[i]);
  estado(card, "Esperando…");
  timers[i] = setTimeout(() => calcular(card), 600);
}

async function calcular(card){
  const it = items[+card.dataset.i];
  if(!it.puntos.some(p => p.l)){ estado(card, "Poné al menos un punto verde sobre la vaca.", true); return; }
  estado(card, "Calculando…");
  try{
    const r = await fetch("/api/calcular", {method:"POST", headers:{"Content-Type":"application/json"},
                                            body: JSON.stringify({rel: it.rel, puntos: it.puntos})});
    const j = await r.json();
    if(!r.ok) throw new Error(j.error || "Error");
    it.v = j.v;
    card.querySelector(".sf").src = `/img/sinfondo?rel=${encodeURIComponent(it.rel)}&v=${j.v}`;
    marcar(card, null);
    estado(card, "Listo — revisá el resultado");
  }catch(e){ estado(card, e.message, true); }
}

async function guardarRevision(card, valor){
  const it = items[+card.dataset.i];
  const v = it.revision === valor ? null : valor;
  const r = await fetch("/api/revisar", {method:"POST", headers:{"Content-Type":"application/json"},
                                         body: JSON.stringify({rel: it.rel, valor: v})});
  if(!r.ok){ alert("No se pudo guardar"); return; }
  marcar(card, v);
}

function agregarPunto(ev, l){
  const lienzo = ev.target.closest(".lienzo");
  if(!lienzo) return false;
  ev.preventDefault();
  const card = lienzo.closest(".item");
  const it = items[+card.dataset.i];
  const punto = ev.target.closest(".punto");
  if(punto){
    it.puntos.splice(+punto.dataset.k, 1);
  }else{
    const b = lienzo.getBoundingClientRect();
    it.puntos.push({x: +((ev.clientX - b.left) / b.width).toFixed(4), y: +((ev.clientY - b.top) / b.height).toFixed(4), l});
  }
  pintarPuntos(card);
  programar(card);
  return true;
}

document.addEventListener("click", ev => {
  if(agregarPunto(ev, 1)) return;
  const b = ev.target.closest("[data-acc]");
  if(!b) return;
  const card = b.closest(".item");
  const it = items[+card.dataset.i];
  if(b.dataset.acc === "limpiar"){ it.puntos = []; pintarPuntos(card); estado(card, "Marcá la vaca con un clic"); }
  else guardarRevision(card, b.dataset.acc);
});
document.addEventListener("contextmenu", ev => { agregarPunto(ev, 0); });

cargar();
</script>
</body>
</html>
"""


def servir(args, rec, lista, resultados, revision, correcciones, vacas):
    from flask import Flask, Response, abort, jsonify, request, send_file

    web = Flask(__name__)
    dir_salida = Path(args.salida).resolve()
    lock = threading.Lock()
    cache = {}

    def guardar_todo():
        escribir_json(dir_salida / "resultados.json", resultados)
        escribir_json(dir_salida / "revision.json", revision)
        escribir_json(dir_salida / "correcciones.json", correcciones)

    @web.get("/")
    def inicio():
        return Response(HTML, mimetype="text/html")

    @web.get("/api/lista")
    def api_lista():
        return jsonify([{"rel": rel, "vaca": vacas.get(rel, rel.split("/")[0]),
                         "puntos": correcciones.get(rel, {}).get("puntos", []),
                         "v": correcciones.get(rel, {}).get("v", 0),
                         "revision": revision.get(rel)} for rel in lista])

    @web.post("/api/calcular")
    def api_calcular():
        d = request.get_json(force=True)
        rel, puntos = d.get("rel"), d.get("puntos") or []
        if rel not in lista:
            return jsonify({"error": "foto desconocida"}), 400
        try:
            puntos = [{"x": float(p["x"]), "y": float(p["y"]), "l": 1 if p["l"] else 0} for p in puntos]
            rec.recortar(rel, puntos, resultados)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        with lock:
            v = int(time.time() * 1000)
            correcciones[rel] = {"puntos": puntos, "v": v}
            revision.pop(rel, None)
            guardar_todo()
        return jsonify({"ok": True, "v": v})

    @web.post("/api/revisar")
    def api_revisar():
        d = request.get_json(force=True)
        rel, valor = d.get("rel"), d.get("valor")
        if rel not in lista:
            return jsonify({"error": "foto desconocida"}), 400
        with lock:
            if valor in ("bien", "mal"):
                revision[rel] = valor
            else:
                revision.pop(rel, None)
            guardar_todo()
        return jsonify({"ok": True})

    @web.get("/img/orig")
    def img_orig():
        rel = request.args.get("rel", "")
        if rel not in lista:
            abort(404)
        if rel not in cache:
            im = abrir(Path(args.referencias) / rel, 1000)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            cache[rel] = buf.getvalue()
        return Response(cache[rel], mimetype="image/jpeg")

    @web.get("/img/sinfondo")
    def img_sinfondo():
        reg = resultados.get(request.args.get("rel", ""))
        if not reg or not reg.get("vista_sf") or not (dir_salida / reg["vista_sf"]).exists():
            abort(404)
        return send_file(dir_salida / reg["vista_sf"], max_age=0)

    url = f"http://127.0.0.1:{args.puerto}"
    print(f"\nAbrí {url} en el navegador (Ctrl+C para cortar).")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    web.run(host="127.0.0.1", port=args.puerto, threaded=True)


def main():
    p = argparse.ArgumentParser(description="Corrige con SAM las fotos de fondo marcadas como Mal.")
    p.add_argument("--referencias", default="Asignaciones (Verificado)")
    p.add_argument("--salida", default="Asignaciones_sin_fondo")
    p.add_argument("--sam", default="sam2.1_t.pt")
    p.add_argument("--max-lado", type=int, default=1600)
    p.add_argument("--hilos", type=int, default=2)
    p.add_argument("--rehacer", action="store_true")
    p.add_argument("--puerto", type=int, default=5002)
    args = p.parse_args()

    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[var] = str(args.hilos)

    dir_ref, dir_salida = Path(args.referencias), Path(args.salida)
    if not dir_ref.is_dir():
        print(f"[ERROR] No existe la carpeta: {dir_ref.resolve()}")
        sys.exit(1)
    if not (dir_salida / "revision.json").exists():
        print(f"[ERROR] No encontré {dir_salida / 'revision.json'}. Primero corré quitar_fondo.py y marcá las fotos.")
        sys.exit(1)

    resultados = leer_json(dir_salida / "resultados.json", {})
    revision = leer_json(dir_salida / "revision.json", {})
    correcciones = leer_json(dir_salida / "correcciones.json", {})

    # Fotos a corregir: las marcadas "mal" + las que ya se corrigieron antes (para seguir viéndolas)
    lista = sorted({r for r, v in revision.items() if v == "mal"} | set(correcciones), key=orden_natural)
    lista = [r for r in lista if (dir_ref / r).exists()]
    if not lista:
        print("No hay fotos marcadas como Mal para corregir.")
        return
    vacas = {r: resultados.get(r, {}).get("vaca", r.split("/")[0]) for r in lista}
    print(f"Fotos a corregir: {len(lista)}")

    rec = Recortador(args)

    auto = [r for r in lista if r in PUNTOS_INICIALES and (args.rehacer or r not in correcciones)]
    if auto:
        print(f"Aplicando los puntos marcados por Claude a {len(auto)} fotos...")
    for n, rel in enumerate(auto, 1):
        try:
            rec.recortar(rel, PUNTOS_INICIALES[rel], resultados)
            correcciones[rel] = {"puntos": PUNTOS_INICIALES[rel], "v": int(time.time() * 1000)}
            revision.pop(rel, None)  # queda pendiente de revisar
            print(f"  {n}/{len(auto)}  {rel}  ok")
        except Exception as e:
            print(f"  {n}/{len(auto)}  {rel}  [AVISO] {e}")
        escribir_json(dir_salida / "resultados.json", resultados)
        escribir_json(dir_salida / "revision.json", revision)
        escribir_json(dir_salida / "correcciones.json", correcciones)

    servir(args, rec, lista, resultados, revision, correcciones, vacas)


if __name__ == "__main__":
    main()
