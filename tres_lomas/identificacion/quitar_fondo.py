"""
quitar_fondo.py  (versión liviana)
Le quita el fondo a las fotos de referencia (Asignaciones) dejando solo la vaca más centrada,
y abre una página para revisar cada foto: original vs. sin fondo, con botones Bien / Mal.

Pensado para no exigir la compu:
  - Procesa de a UNA foto por vez.
  - Achica cada foto a 1600 px de lado mayor antes de procesarla (las fotos de celular suelen
    ser de 4000 px; para identificar vacas sobra con 1600).
  - Usa pocos núcleos del procesador (2 por defecto) y hace una pausa cada 50 fotos.
  - Guarda vistas previas chiquitas en disco, así la página no carga las fotos grandes.
  - Muestra la RAM que está usando.

Cómo funciona:
  - YOLO de segmentación detecta las vacas y su silueta.
  - Si en la foto hay varias, se queda con la que tiene el centro más cerca del centro de la foto.
  - Guarda cada resultado como PNG transparente en "Asignaciones_sin_fondo" (misma estructura de carpetas).
    Las fotos originales NO se tocan.
  - En la página, la foto original muestra en rojo la vaca elegida.
  - Lo que marcás (Bien / Mal) se guarda en "Asignaciones_sin_fondo/revision.json".

Se puede retomar: si se corta, al volver a correrlo sigue con las fotos que faltan
y conserva lo que ya marcaste.

Uso (desde la carpeta desfile_tres_lomas):
    python quitar_fondo.py
Se abre http://127.0.0.1:5001 (Ctrl+C para cortar).

Opciones:
    --referencias  carpeta con las fotos de cada vaca  (default: "Asignaciones (Verificado)")
    --salida       carpeta para las fotos sin fondo    (default: Asignaciones_sin_fondo)
    --max-lado     tamaño máximo de las fotos en px    (default: 1600)
    --hilos        núcleos del procesador a usar       (default: 2)
    --tanda        fotos entre pausa y pausa           (default: 50)
    --pausa        segundos de descanso entre tandas   (default: 3)
    --yolo         modelo de segmentación              (default: yolo11n-seg.pt, el más liviano)
    --conf         confianza mínima                    (default: 0.25)
    --rehacer      vuelve a procesar todas las fotos
    --solo-revisar no procesa nada, solo abre la página
    --puerto       puerto de la página                 (default: 5001)
"""

import argparse
import gc
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

EXT_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
CLASE_VACA = 19  # "cow" en COCO
LADO_VISTA = 600


# ----------------------------------------------------------------------------- utilidades

def orden_natural(texto):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(texto))]


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


def ram_mb():
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 ** 2
    except Exception:
        return None


def abrir(ruta, max_lado):
    im = Image.open(ruta)
    im.draft("RGB", (max_lado, max_lado))  # en JPG decodifica directo a menor tamaño (ahorra RAM)
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB")
    im.thumbnail((max_lado, max_lado))
    return im


def id_desde_nombre(stem):
    m = re.match(r"^\s*(?:vaca|rp|caravana)?[\s_#-]*(\d+)", stem, re.I)
    if m:
        return m.group(1)
    s = re.sub(r"[\s_-]*\(\d+\)$", "", stem)
    s = re.sub(r"[\s_-]+\d{1,2}$", "", s)
    return s.strip() or stem


def listar_referencias(carpeta):
    """Devuelve [(vaca, ruta, rel)] con rel = ruta relativa con '/'."""
    archivos = [f for f in carpeta.rglob("*") if f.is_file() and f.suffix.lower() in EXT_IMG
                and not f.name.startswith("bib_")]  # bib_*.png = recortes de biblioteca_lados.py, ya sin fondo
    items = []
    for f in sorted(archivos, key=orden_natural):
        rel = f.relative_to(carpeta)
        vaca = rel.parts[0] if len(rel.parts) > 1 else id_desde_nombre(f.stem)
        items.append((vaca, f, rel.as_posix()))
    return items


def elegir_central(cajas, ancho, alto):
    cx, cy = ancho / 2, alto / 2
    distancias = [(((x1 + x2) / 2 - cx) / ancho) ** 2 + (((y1 + y2) / 2 - cy) / alto) ** 2
                  for x1, y1, x2, y2 in cajas]
    return int(np.argmin(distancias))


# ----------------------------------------------------------------------------- proceso

def procesar(args, items, dir_salida):
    ruta_res = dir_salida / "resultados.json"
    resultados = leer_json(ruta_res, {})
    rels = {rel for _, _, rel in items}
    resultados = {k: v for k, v in resultados.items() if k in rels}

    def falta(rel):
        if args.rehacer or rel not in resultados:
            return True
        reg = resultados[rel]
        if not reg.get("vista_orig") or not (dir_salida / reg["vista_orig"]).exists():
            return True
        return reg["estado"] == "ok" and not (dir_salida / reg["png"]).exists()

    pendientes = [it for it in items if falta(it[2])]
    if not pendientes:
        print("Todas las fotos ya estaban procesadas.")
        return resultados

    import torch
    from ultralytics import YOLO
    torch.set_num_threads(args.hilos)

    print(f"Cargando modelo de segmentación ({args.yolo})...")
    modelo = YOLO(args.yolo)
    print(f"Quitando fondo a {len(pendientes)} fotos (de a una)...")
    t0 = time.time()

    for n, (vaca, ruta, rel) in enumerate(pendientes, 1):
        base = Path(rel).with_suffix("")
        vista_orig = (Path("_vistas") / f"{base}_orig.jpg").as_posix()
        vista_sf = (Path("_vistas") / f"{base}_sf.webp").as_posix()
        reg = {"vaca": vaca, "estado": "sin_vaca", "n_vacas": 0, "box": None,
               "png": None, "vista_orig": vista_orig, "vista_sf": None}
        try:
            im = abrir(ruta, args.max_lado)
        except Exception as e:
            print(f"  [AVISO] No se pudo abrir {rel}: {e}")
            reg["estado"] = "error"
            reg["vista_orig"] = None
            resultados[rel] = reg
            continue

        with torch.inference_mode():
            r = modelo.predict(im, classes=[CLASE_VACA], conf=args.conf, imgsz=640,
                               retina_masks=True, device="cpu", verbose=False)[0]

        box = None
        if r.masks is not None and len(r.boxes):
            cajas = r.boxes.xyxy.numpy()
            j = elegir_central(cajas, im.width, im.height)
            m = r.masks.data[j].numpy()
            if m.shape != (im.height, im.width):
                m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize(im.size, Image.BILINEAR)) / 255.0
            alfa = Image.fromarray(((m > 0.5) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.5))
            del m

            x1, y1, x2, y2 = (float(v) for v in cajas[j])
            box = [x1, y1, x2, y2]
            mx, my = (x2 - x1) * 0.03, (y2 - y1) * 0.03
            recorte = (int(max(0, x1 - mx)), int(max(0, y1 - my)),
                       int(min(im.width, x2 + mx)), int(min(im.height, y2 + my)))
            rgba = im.crop(recorte)
            rgba.putalpha(alfa.crop(recorte))
            del alfa

            png_rel = Path(rel).with_suffix(".png").as_posix()
            destino = dir_salida / png_rel
            destino.parent.mkdir(parents=True, exist_ok=True)
            rgba.save(destino)

            rgba.thumbnail((LADO_VISTA, LADO_VISTA))
            destino_sf = dir_salida / vista_sf
            destino_sf.parent.mkdir(parents=True, exist_ok=True)
            rgba.save(destino_sf, "WEBP", quality=80)
            del rgba
            reg.update(estado="ok", n_vacas=int(len(cajas)), box=box, png=png_rel, vista_sf=vista_sf)
        del r

        if box:
            ImageDraw.Draw(im).rectangle(box, outline=(230, 40, 40), width=max(4, im.width // 150))
        im.thumbnail((LADO_VISTA, LADO_VISTA))
        destino_o = dir_salida / vista_orig
        destino_o.parent.mkdir(parents=True, exist_ok=True)
        im.save(destino_o, "JPEG", quality=82)
        del im

        resultados[rel] = reg
        if n % 10 == 0 or n == len(pendientes):
            escribir_json(ruta_res, resultados)
            gc.collect()

        if n % args.tanda == 0 or n == len(pendientes):
            restante = (time.time() - t0) / n * (len(pendientes) - n)
            ram = ram_mb()
            print(f"  {n}/{len(pendientes)}  | faltan ~{restante / 60:.1f} min"
                  + (f"  | RAM: {ram:.0f} MB" if ram else ""))
            if args.pausa and n < len(pendientes):
                time.sleep(args.pausa)

    escribir_json(ruta_res, resultados)
    del modelo
    gc.collect()
    return resultados


# ----------------------------------------------------------------------------- página

HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revisión de fondos</title>
<style>
  :root{--bg:#f3f0e8;--tinta:#1f2a1f;--suave:#6b7466;--borde:#ddd6c8;--verde:#2f7d4a;--rojo:#b8452f}
  *{box-sizing:border-box}
  body{margin:0;font-family:"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--tinta)}
  header{position:sticky;top:0;z-index:5;background:var(--tinta);color:#fff;display:flex;gap:10px;
         align-items:center;padding:10px 16px;flex-wrap:wrap}
  header h1{font-size:16px;margin:0 8px 0 0}
  .chip{background:rgba(255,255,255,.13);padding:3px 10px;border-radius:999px;font-size:13px}
  .espacio{flex:1}
  button{font:inherit;cursor:pointer;border:1px solid var(--borde);background:#fff;color:var(--tinta);
         border-radius:8px;padding:6px 14px}
  header button{background:transparent;color:#fff;border-color:rgba(255,255,255,.35)}
  header button.activo{background:#fff;color:var(--tinta);font-weight:600}
  main{padding:16px;display:flex;flex-direction:column;gap:16px;max-width:1500px;margin:0 auto}
  .vaca{background:#fff;border:1px solid var(--borde);border-radius:10px;padding:12px 14px}
  .vaca h2{margin:0 0 10px;font-size:16px}
  .fotos{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:12px}
  .foto{border:2px solid var(--borde);border-radius:8px;padding:8px}
  .foto.bien{border-color:var(--verde);background:#f1f9f3}
  .foto.mal{border-color:var(--rojo);background:#fcf0ed}
  .par{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  figure{margin:0}
  .par img{width:100%;height:240px;object-fit:contain;border-radius:6px;background:#222;display:block}
  .par img.tablero{background-color:#fff;
    background-image:linear-gradient(45deg,#ddd 25%,transparent 25%),linear-gradient(-45deg,#ddd 25%,transparent 25%),
                     linear-gradient(45deg,transparent 75%,#ddd 75%),linear-gradient(-45deg,transparent 75%,#ddd 75%);
    background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}
  figcaption{font-size:12px;color:var(--suave);margin-top:3px}
  .sinvaca{height:240px;display:flex;align-items:center;justify-content:center;background:#eee;border-radius:6px;
           color:var(--rojo);font-size:13px;text-align:center;padding:10px}
  .pie{display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap}
  .info{font-size:12px;color:var(--suave);flex:1;min-width:150px;word-break:break-all}
  .b-bien.on{background:var(--verde);color:#fff;border-color:var(--verde)}
  .b-mal.on{background:var(--rojo);color:#fff;border-color:var(--rojo)}
  .vacio{color:var(--suave);text-align:center;padding:40px}
</style>
</head>
<body>
<header>
  <h1>Revisión de fondos · Asignaciones</h1>
  <span class="chip" id="c-total"></span>
  <span class="chip" id="c-bien"></span>
  <span class="chip" id="c-mal"></span>
  <span class="chip" id="c-pend"></span>
  <span class="espacio"></span>
  <span>Mostrar:</span>
  <button data-f="todas" class="activo">Todas</button>
  <button data-f="pend">Pendientes</button>
  <button data-f="mal">Mal</button>
  <button data-f="sinvaca">Sin vaca detectada</button>
</header>
<main id="lista"><div class="vacio">Cargando…</div></main>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let datos = [], filtro = "todas";

async function cargar(){
  datos = await (await fetch("/api/datos")).json();
  render();
}

function pasaFiltro(it){
  if(filtro === "pend") return !it.revision;
  if(filtro === "mal") return it.revision === "mal";
  if(filtro === "sinvaca") return it.estado !== "ok";
  return true;
}

function contadores(){
  const bien = datos.filter(d => d.revision === "bien").length;
  const mal = datos.filter(d => d.revision === "mal").length;
  $("#c-total").textContent = `${datos.length} fotos`;
  $("#c-bien").textContent = `✔ ${bien} bien`;
  $("#c-mal").textContent = `✘ ${mal} mal`;
  $("#c-pend").textContent = `${datos.length - bien - mal} pendientes`;
}

function tarjeta(it){
  const u = encodeURIComponent(it.rel);
  const sinFondo = it.estado === "ok"
    ? `<img class="tablero" src="/img/sinfondo?rel=${u}" loading="lazy">`
    : `<div class="sinvaca">${it.estado === "error" ? "No se pudo abrir la foto" : "No se detectó ninguna vaca en esta foto"}</div>`;
  const original = it.estado === "error"
    ? `<div class="sinvaca">Sin vista previa</div>`
    : `<img src="/img/orig?rel=${u}" loading="lazy">`;
  const extra = it.n_vacas > 1 ? ` · ${it.n_vacas} vacas en la foto, se eligió la del centro` : "";
  return `
    <div class="foto ${it.revision || ""}" data-rel="${esc(it.rel)}">
      <div class="par">
        <figure>${original}<figcaption>Original (en rojo la vaca elegida)</figcaption></figure>
        <figure>${sinFondo}<figcaption>Sin fondo</figcaption></figure>
      </div>
      <div class="pie">
        <span class="info">${esc(it.rel)}${extra}</span>
        <button class="b-bien ${it.revision === "bien" ? "on" : ""}" data-valor="bien">✔ Bien</button>
        <button class="b-mal ${it.revision === "mal" ? "on" : ""}" data-valor="mal">✘ Mal</button>
      </div>
    </div>`;
}

function render(){
  contadores();
  const grupos = new Map();
  for(const it of datos){
    if(!pasaFiltro(it)) continue;
    if(!grupos.has(it.vaca)) grupos.set(it.vaca, []);
    grupos.get(it.vaca).push(it);
  }
  if(!grupos.size){ $("#lista").innerHTML = `<div class="vacio">No hay fotos para mostrar con este filtro.</div>`; return; }
  $("#lista").innerHTML = [...grupos].map(([vaca, its]) => `
    <section class="vaca">
      <h2>Vaca ${esc(vaca)} <small style="color:var(--suave);font-weight:400">· ${its.length} foto(s)</small></h2>
      <div class="fotos">${its.map(tarjeta).join("")}</div>
    </section>`).join("");
}

document.addEventListener("click", async ev => {
  const f = ev.target.closest("header [data-f]");
  if(f){
    filtro = f.dataset.f;
    document.querySelectorAll("header [data-f]").forEach(b => b.classList.toggle("activo", b === f));
    render();
    window.scrollTo(0, 0);
    return;
  }
  const b = ev.target.closest("[data-valor]");
  if(!b) return;
  const card = b.closest(".foto");
  const it = datos.find(d => d.rel === card.dataset.rel);
  const valor = it.revision === b.dataset.valor ? null : b.dataset.valor;   // otro clic = desmarcar
  const r = await fetch("/api/revisar", {method:"POST", headers:{"Content-Type":"application/json"},
                                          body: JSON.stringify({rel: it.rel, valor})});
  if(!r.ok){ alert("No se pudo guardar"); return; }
  it.revision = valor;
  card.classList.remove("bien", "mal");
  if(valor) card.classList.add(valor);
  card.querySelector(".b-bien").classList.toggle("on", valor === "bien");
  card.querySelector(".b-mal").classList.toggle("on", valor === "mal");
  contadores();
});

cargar();
</script>
</body>
</html>
"""


def servir(args, items, resultados, dir_salida):
    from flask import Flask, Response, abort, jsonify, request, send_file

    web = Flask(__name__)
    ruta_rev = dir_salida / "revision.json"
    revision = leer_json(ruta_rev, {})
    rels = {rel for _, _, rel in items}
    lock = threading.Lock()
    base = dir_salida.resolve()

    @web.get("/")
    def inicio():
        return Response(HTML, mimetype="text/html")

    @web.get("/api/datos")
    def api_datos():
        salida = []
        for vaca, _, rel in items:
            reg = resultados.get(rel)
            if reg is None:
                continue
            salida.append({"rel": rel, "vaca": vaca, "estado": reg["estado"],
                           "n_vacas": reg["n_vacas"], "revision": revision.get(rel)})
        return jsonify(salida)

    @web.post("/api/revisar")
    def api_revisar():
        d = request.get_json(force=True)
        rel, valor = d.get("rel"), d.get("valor")
        if rel not in rels:
            return jsonify({"error": "foto desconocida"}), 400
        with lock:
            if valor in ("bien", "mal"):
                revision[rel] = valor
            else:
                revision.pop(rel, None)
            escribir_json(ruta_rev, revision)
        return jsonify({"ok": True})

    def enviar_vista(campo):
        reg = resultados.get(request.args.get("rel", ""))
        if not reg or not reg.get(campo):
            abort(404)
        ruta = base / reg[campo]
        if not ruta.exists():
            abort(404)
        return send_file(ruta, max_age=3600)

    @web.get("/img/orig")
    def img_orig():
        return enviar_vista("vista_orig")

    @web.get("/img/sinfondo")
    def img_sinfondo():
        return enviar_vista("vista_sf")

    url = f"http://127.0.0.1:{args.puerto}"
    print(f"\nAbrí {url} en el navegador (Ctrl+C para cortar).")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    web.run(host="127.0.0.1", port=args.puerto, threaded=True)


def main():
    p = argparse.ArgumentParser(description="Quita el fondo a las fotos de referencia y abre la revisión (liviano).")
    p.add_argument("--referencias", default="Asignaciones (Verificado)")
    p.add_argument("--salida", default="Asignaciones_sin_fondo")
    p.add_argument("--max-lado", type=int, default=1600)
    p.add_argument("--hilos", type=int, default=2)
    p.add_argument("--tanda", type=int, default=50)
    p.add_argument("--pausa", type=float, default=3)
    p.add_argument("--yolo", default="yolo11n-seg.pt")
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--rehacer", action="store_true")
    p.add_argument("--solo-revisar", action="store_true")
    p.add_argument("--puerto", type=int, default=5001)
    args = p.parse_args()

    # Limitar núcleos ANTES de importar torch/ultralytics
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[var] = str(args.hilos)

    dir_ref = Path(args.referencias)
    if not dir_ref.is_dir():
        print(f"[ERROR] No existe la carpeta: {dir_ref.resolve()}")
        sys.exit(1)
    dir_salida = Path(args.salida)
    dir_salida.mkdir(parents=True, exist_ok=True)

    items = listar_referencias(dir_ref)
    if not items:
        print(f"[ERROR] No hay imágenes en {dir_ref}")
        sys.exit(1)
    vacas = sorted({v for v, _, _ in items}, key=orden_natural)
    print(f"Referencias: {len(items)} fotos de {len(vacas)} vacas")

    if args.solo_revisar:
        resultados = leer_json(dir_salida / "resultados.json", {})
    else:
        resultados = procesar(args, items, dir_salida)
    ok = sum(1 for r in resultados.values() if r["estado"] == "ok")
    print(f"Con vaca recortada: {ok}  |  Sin vaca detectada o con error: {len(resultados) - ok}")

    servir(args, items, resultados, dir_salida)


if __name__ == "__main__":
    main()