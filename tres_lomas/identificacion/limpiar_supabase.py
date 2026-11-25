"""
limpiar_supabase.py
Libera espacio en el bucket "imagenesvacas" de Supabase borrando las imágenes MÁS VIEJAS hasta que el
total quede por debajo de un objetivo (por defecto 80 GB, para dejar margen bajo los 100 GB del plan Pro).
Conserva siempre las más nuevas.

Seguridad:
  - Por defecto NO borra nada: solo muestra qué borraría (cuántas, de qué fechas, cuánto libera).
  - Para borrar de verdad hay que agregar --borrar y confirmar escribiendo BORRAR.
  - Nunca borra los frames donde se detectó una monta (frames_formateados con tiene_monta = 1, ni su
    frame original): sirven para entrenar y para ver las montas en la web.
  - Borra por la API de Storage (así se libera el espacio de verdad; borrar con SQL deja los archivos).
  - Nunca borra lo subido en los últimos 14 días (cambiable con --conservar-dias).
  - Guarda la lista de lo borrado en borrados_supabase_<fecha>.txt.

Requisitos:
  - El proyecto tiene que estar destrabado (límite de gasto desactivado); si no, Supabase responde 402.
  - Hace falta la clave service_role (la anon de la web no puede borrar). Ponela en supabase.env:
        SUPABASE_URL=https://msnhpytgbdfpgopiqohl.supabase.co
        SUPABASE_KEY=<clave service_role>

Uso (desde la carpeta desfile_tres_lomas):
    python limpiar_supabase.py --env supabase.env                 # simula
    python limpiar_supabase.py --env supabase.env --objetivo 70   # simula dejando 70 GB
    python limpiar_supabase.py --env supabase.env --borrar        # borra (pide confirmación)
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from inventario_supabase import BUCKET, RE_DIA, Supa, buscar_claves  # noqa: E402

RE_RUTA_PUBLICA = re.compile(r"/object/(?:public|sign|authenticated)/" + BUCKET + r"/([^?]+)")


def listar_todo(sb):
    """[(ruta, tamaño, fecha)] de todo el bucket."""
    archivos = []

    def rec(prefijo, prof=0):
        for it in sb.listar(prefijo):
            nombre = it.get("name", "")
            ruta = f"{prefijo}/{nombre}" if prefijo else nombre
            if it.get("id") is None:
                if prof < 6:
                    rec(ruta, prof + 1)
            else:
                meta = it.get("metadata") or {}
                fecha = it.get("created_at") or it.get("updated_at") or ""
                if not fecha:
                    m = RE_DIA.search(ruta)
                    fecha = m.group(1) if m else ""
                archivos.append((ruta, meta.get("size", 0) or 0, fecha))
                if len(archivos) % 5000 == 0:
                    print(f"  ... {len(archivos)} archivos", flush=True)

    rec("")
    return archivos


def ruta_de(valor):
    """Ruta dentro del bucket a partir de lo que guarda la tabla (URL pública o ruta)."""
    if not valor:
        return None
    m = RE_RUTA_PUBLICA.search(valor)
    if m:
        return urllib.request.unquote(m.group(1))
    return valor.lstrip("/").removeprefix(BUCKET + "/")


def protegidos(sb):
    """Rutas de los frames con monta (formateado + original): no se borran."""
    ff = sb.filas("frames_formateados", "ruta_archivo,frame_crudo_id,tiene_monta&tiene_monta=eq.1")
    rutas = {ruta_de(f.get("ruta_archivo")) for f in ff}
    ids = sorted({f.get("frame_crudo_id") for f in ff if f.get("frame_crudo_id") is not None})
    for i in range(0, len(ids), 200):
        lote = ",".join(str(x) for x in ids[i:i + 200])
        for fc in sb.filas("frames_crudos", f"ruta_archivo&id=in.({lote})"):
            rutas.add(ruta_de(fc.get("ruta_archivo")))
    rutas.discard(None)
    return rutas


def borrar(sb, rutas):
    hechos = 0
    for i in range(0, len(rutas), 500):
        lote = rutas[i:i + 500]
        for intento in range(3):
            try:
                sb._pedido("DELETE", f"/storage/v1/object/{BUCKET}", {"prefixes": lote})
                break
            except urllib.error.HTTPError as e:
                if intento == 2:
                    raise
                print(f"  reintento ({e.code})...")
                time.sleep(3)
        hechos += len(lote)
        print(f"  borrados {hechos}/{len(rutas)}", flush=True)
    return hechos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None)
    ap.add_argument("--objetivo", type=float, default=80.0, help="GB que deben quedar como máximo")
    ap.add_argument("--conservar-dias", type=int, default=14,
                    help="nunca borrar lo subido en los últimos N días (default 14)")
    ap.add_argument("--borrar", action="store_true")
    args = ap.parse_args()

    url, key, origen = buscar_claves(args.env)
    print(f"Usando las claves de {origen} (no se muestran).")
    sb = Supa(url, key)
    try:
        print("Listando el bucket (puede tardar unos minutos)...")
        archivos = listar_todo(sb)
    except urllib.error.HTTPError as e:
        if e.code == 402:
            raise SystemExit("Supabase sigue restringido (402). Desactivá el límite de gasto en Billing, "
                             "esperá unos minutos y volvé a correr.")
        raise SystemExit(f"No pude listar el bucket (error {e.code}). ¿Es la clave service_role?")

    total = sum(s for _, s, _ in archivos)
    print(f"\nEn el bucket: {len(archivos)} archivos, {total / 1e9:.1f} GB")
    objetivo = args.objetivo * 1e9
    if total <= objetivo:
        print(f"Ya está por debajo de {args.objetivo:.0f} GB: no hace falta borrar nada.")
        return

    prot = protegidos(sb)
    print(f"Frames con monta protegidos (no se borran): {len(prot)}")

    archivos.sort(key=lambda a: a[2])  # más viejos primero
    limite = (datetime.now() - timedelta(days=args.conservar_dias)).strftime("%Y-%m-%d")
    print(f"Se conserva todo lo subido desde el {limite} (últimos {args.conservar_dias} días).")
    a_borrar, liberado = [], 0
    for ruta, s, fecha in archivos:
        if total - liberado <= objetivo:
            break
        if ruta in prot:
            continue
        if fecha and fecha[:10].replace("-", "") >= limite.replace("-", ""):
            break  # de acá en adelante son las recientes: no se tocan
        a_borrar.append((ruta, s, fecha))
        liberado += s
    if total - liberado > objetivo:
        print("[AVISO] Aun borrando todo lo no protegido no se llega al objetivo.")

    quedan = [a for a in archivos if a[0] not in {r for r, _, _ in a_borrar}]
    por_tipo = Counter(r.split("/")[0] for r, _, _ in a_borrar)
    print("\n== Qué se borraría ==")
    print(f"  {len(a_borrar)} archivos, {liberado / 1e9:.1f} GB")
    if a_borrar:
        print(f"  desde {a_borrar[0][2][:16]} hasta {a_borrar[-1][2][:16]}")
    print(f"  por carpeta: {dict(por_tipo)}")
    print("\n== Qué queda ==")
    print(f"  {len(quedan)} archivos, {(total - liberado) / 1e9:.1f} GB")
    if quedan:
        fechas = sorted(f for _, _, f in quedan if f)
        if fechas:
            print(f"  desde {fechas[0][:16]} hasta {fechas[-1][:16]} (más los frames con monta)")

    if not args.borrar:
        print("\nSimulación: no se borró nada. Para borrar, agregá --borrar.")
        return
    resp = input(f"\nVas a borrar {len(a_borrar)} archivos ({liberado / 1e9:.1f} GB) para siempre. "
                 "Escribí BORRAR para confirmar: ")
    if resp.strip() != "BORRAR":
        print("Cancelado: no se borró nada.")
        return
    registro = RAIZ / f"borrados_supabase_{datetime.now():%Y%m%d_%H%M}.txt"
    registro.write_text("\n".join(f"{f}\t{s}\t{r}" for r, s, f in a_borrar), encoding="utf-8")
    print(f"Lista guardada en {registro.name}. Borrando...")
    n = borrar(sb, [r for r, _, _ in a_borrar])
    print(f"\nListo: {n} archivos borrados, {liberado / 1e9:.1f} GB liberados.")
    print("El panel de Supabase puede tardar un rato en mostrar el uso nuevo. "
          "Ojo: las filas de frames_crudos / frames_formateados de esas fotos siguen en la base.")


if __name__ == "__main__":
    main()
