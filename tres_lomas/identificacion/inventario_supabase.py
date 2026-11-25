"""
inventario_supabase.py
Cuenta qué hay en Supabase para decidir cómo usarlo para entrenar. NO baja ni cambia nada.

Muestra:
  - Cuántas filas tiene cada tabla (vacas, frames_crudos, frames_formateados, montas, ...).
  - Cómo están las montas (pendiente / identificada / fallida, con y sin bbox_vaca).
  - Qué imágenes hay en el bucket "imagenesvacas": cuántas y cuánto pesan, por carpeta, por cámara
    y por día.
Deja el resumen en inventario_supabase.txt (sin claves) para pasárselo a Claude.

Las claves las toma del .env de algún servicio de FORMATEADOR (SUPABASE_URL y SUPABASE_KEY), o de
agro-ia/.env.local. No se imprimen ni se guardan.

Uso (desde la carpeta desfile_tres_lomas):
    python inventario_supabase.py
    python inventario_supabase.py --env C:\\ruta\\al\\.env      (si las claves están en otro lado)
"""

import argparse
import json
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
BUCKET = "imagenesvacas"
TABLAS = ["tambos", "parcelas", "camaras", "vacas", "frames_crudos", "frames_formateados", "montas",
          "inseminaciones"]
RE_DIA = re.compile(r"(20\d{6})")


def leer_env(ruta):
    """Lee un .env aunque esté guardado en UTF-8 con BOM o en UTF-16 (pasa con PowerShell)."""
    crudo = Path(ruta).read_bytes()
    if crudo.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in crudo[:20]:
        texto = crudo.decode("utf-16", errors="ignore")
    else:
        texto = crudo.decode("utf-8-sig", errors="ignore")
    datos = {}
    for linea in texto.replace("\x00", "").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def buscar_claves(env_arg):
    candidatos = [Path(env_arg)] if env_arg else []
    vaq = RAIZ.parent
    candidatos += sorted(vaq.glob("FORMATEADOR/*/.env")) + sorted(vaq.glob("FORMATEADOR/.env"))
    candidatos += [vaq / "agro-ia" / ".env.local"]
    for c in candidatos:
        if c.is_file():
            d = leer_env(c)
            print(f"  {c}: variables encontradas -> {', '.join(sorted(d)) or '(ninguna)'}")
            url = d.get("SUPABASE_URL") or d.get("NEXT_PUBLIC_SUPABASE_URL")
            key = d.get("SUPABASE_KEY") or d.get("SUPABASE_SERVICE_ROLE_KEY") or d.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
            if url and key and "your-project" not in url:
                return url.rstrip("/"), key, c
    revisados = [str(c) for c in candidatos]
    raise SystemExit("No encontré SUPABASE_URL y SUPABASE_KEY. Revisé:\n  " + "\n  ".join(revisados) +
                     "\nPasá la ruta del .env con --env, o creá uno (ver instrucciones).")


class Supa:
    def __init__(self, url, key):
        self.url, self.key = url, key

    def _pedido(self, metodo, ruta, cuerpo=None, extra=None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        h.update(extra or {})
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(self.url + ruta, data=datos, headers=h, method=metodo)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.headers, json.loads(r.read() or b"null")

    def contar(self, tabla, filtro=""):
        try:
            h, _ = self._pedido("GET", f"/rest/v1/{tabla}?select=*{filtro}",
                                extra={"Prefer": "count=exact", "Range": "0-0"})
            rango = h.get("Content-Range", "")
            return int(rango.split("/")[-1]) if "/" in rango and rango.split("/")[-1] != "*" else None
        except urllib.error.HTTPError as e:
            return f"error {e.code}"

    def filas(self, tabla, columnas, limite=100000):
        out, desde = [], 0
        while desde < limite:
            try:
                _, d = self._pedido("GET", f"/rest/v1/{tabla}?select={columnas}",
                                    extra={"Range": f"{desde}-{desde + 999}"})
            except urllib.error.HTTPError:
                break
            if not d:
                break
            out += d
            if len(d) < 1000:
                break
            desde += 1000
        return out

    def listar(self, prefijo):
        out, offset = [], 0
        while True:
            _, d = self._pedido("POST", f"/storage/v1/object/list/{BUCKET}",
                                {"prefix": prefijo, "limit": 1000, "offset": offset,
                                 "sortBy": {"column": "name", "order": "asc"}})
            out += d or []
            if not d or len(d) < 1000:
                return out
            offset += 1000


def recorrer(sb, prefijo, archivos, profundidad=0):
    for it in sb.listar(prefijo):
        nombre = it.get("name", "")
        ruta = f"{prefijo}/{nombre}" if prefijo else nombre
        if it.get("id") is None:  # carpeta
            if profundidad < 6:
                recorrer(sb, ruta, archivos, profundidad + 1)
        else:
            archivos.append((ruta, (it.get("metadata") or {}).get("size", 0) or 0))
        if len(archivos) and len(archivos) % 5000 == 0:
            print(f"  ... {len(archivos)} archivos contados", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None)
    args = ap.parse_args()
    url, key, origen = buscar_claves(args.env)
    print(f"Usando las claves de {origen} (no se muestran).")
    sb = Supa(url, key)
    lineas = []

    def p(t=""):
        print(t)
        lineas.append(t)

    p("== Tablas ==")
    for t in TABLAS:
        p(f"  {t:20s} {sb.contar(t)}")

    p("\n== Montas ==")
    montas = sb.filas("montas", "estado_id,bbox_vaca,bbox_monta,confianza,fecha_monta,vaca_id")
    if montas:
        p(f"  total leídas: {len(montas)}")
        p(f"  por estado: {dict(Counter(m.get('estado_id') for m in montas))}")
        p(f"  con bbox_vaca: {sum(1 for m in montas if m.get('bbox_vaca'))}")
        p(f"  con vaca asignada: {sum(1 for m in montas if m.get('vaca_id'))}")
        dias = Counter(str(m.get('fecha_monta') or '')[:10] for m in montas)
        p(f"  días con montas: {len(dias)}  (de {min(dias)} a {max(dias)})")
    p("\n== Frames formateados ==")
    ff = sb.filas("frames_formateados", "tiene_monta")
    if ff:
        p(f"  tiene_monta: {dict(Counter(f.get('tiene_monta') for f in ff))}")

    p(f"\n== Bucket {BUCKET} ==")
    print("Recorriendo el bucket (puede tardar unos minutos)...")
    archivos = []
    try:
        recorrer(sb, "", archivos)
    except urllib.error.HTTPError as e:
        p(f"  No pude listar el bucket (error {e.code}). Si la clave es 'anon', usá la service_role.")
    if archivos:
        total = sum(s for _, s in archivos)
        p(f"  archivos: {len(archivos)}   peso total: {total / 1e9:.2f} GB")
        por_carpeta = defaultdict(lambda: [0, 0])
        por_dia = Counter()
        for ruta, s in archivos:
            partes = ruta.split("/")
            clave = "/".join(partes[:3]) if len(partes) > 3 else "/".join(partes[:-1]) or "(raíz)"
            por_carpeta[clave][0] += 1
            por_carpeta[clave][1] += s
            m = RE_DIA.search(partes[-1]) or RE_DIA.search(ruta)
            por_dia[(partes[0], m.group(1) if m else "sin fecha")] += 1
        p("\n  Por carpeta (tipo/tambo/cámara):")
        for k in sorted(por_carpeta):
            n, s = por_carpeta[k]
            p(f"    {k:55s} {n:>8} archivos  {s / 1e6:>9.0f} MB")
        p("\n  Por día:")
        for (tipo, dia), n in sorted(por_dia.items()):
            p(f"    {tipo:25s} {dia}  {n:>7}")
        ej = [r for r, _ in archivos[:3]] + [r for r, _ in archivos[-3:]]
        p("\n  Ejemplos de nombres: " + " | ".join(ej))

    salida = RAIZ / "inventario_supabase.txt"
    salida.write_text("\n".join(lineas), encoding="utf-8")
    print(f"\nResumen guardado en {salida}")


if __name__ == "__main__":
    main()
