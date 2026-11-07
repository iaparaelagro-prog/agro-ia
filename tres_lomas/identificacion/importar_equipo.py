"""
importar_equipo.py
Trae a campo.py lo que el equipo marcó en la página compartida "Identificación en equipo".

Antes: en la página tocá "Bajar resultados" (baja resultados_equipo.json) y dejá el archivo en
esta carpeta (o pasale la ruta). CERRÁ campo.py antes de correr esto: campo.py reescribe
etiquetas.json mientras está abierto y pisaría lo importado.

Uso (desde la carpeta desfile_tres_lomas):
    python importar_equipo.py [resultados_equipo.json] [--pisar]

    --pisar   si un recorrido ya tenía etiqueta en esta compu y el equipo puso otra, gana la del
              equipo (por defecto se respeta la de esta compu y se avisa)
Guarda una copia de seguridad de etiquetas.json y postergados.json antes de cambiarlos.
La próxima vez que abras campo.py, los recorridos marcados por el equipo entran a la galería
(y la huella se re-afina con ellos).
"""

import argparse
import json
import shutil
import time
from pathlib import Path

DIR = Path("campo")
MOTIVOS = {"chica", "no_se_ve", "no_es_vaca", "amontonadas", "otro"}


def leer(ruta, defecto):
    ruta = Path(ruta)
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else defecto


def main():
    p = argparse.ArgumentParser(description="Importa las etiquetas del equipo a campo/.")
    p.add_argument("archivo", nargs="?", default="resultados_equipo.json")
    p.add_argument("--pisar", action="store_true")
    a = p.parse_args()

    res = leer(a.archivo, None)
    if res is None:
        raise SystemExit(f"[ERROR] No encuentro {Path(a.archivo).resolve()}. Bajalo desde la página (botón 'Bajar resultados').")
    recs = leer(DIR / "recorridos.json", {})
    ets = leer(DIR / "etiquetas.json", {})
    post = leer(DIR / "postergados.json", {})

    sello = time.strftime("%Y%m%d_%H%M%S")
    for nombre in ("etiquetas.json", "postergados.json"):
        if (DIR / nombre).exists():
            shutil.copy2(DIR / nombre, DIR / f"{nombre}.antes_equipo_{sello}")

    nuevas = iguales = conflicto = pisadas = no_existe = 0
    for rid, e in res.get("etiquetas", {}).items():
        if rid not in recs:
            no_existe += 1
            continue
        accion = e.get("accion")
        if accion not in ("vaca", "descartar", "nose") or (accion == "vaca" and not str(e.get("vaca", "")).strip()):
            continue
        et = {"accion": accion, "ts": round(e.get("ts", time.time() * 1000) / 1000), "equipo": e.get("por")}
        if accion == "vaca":
            et.update(vaca=str(e["vaca"]).strip(), posicion=int(e.get("posicion") or 0))
        elif accion == "descartar":
            et["motivo"] = e.get("motivo") if e.get("motivo") in MOTIVOS else "otro"
        previa = ets.get(rid)
        if previa is None:
            ets[rid] = et
            nuevas += 1
        elif previa.get("accion") == et["accion"] and previa.get("vaca") == et.get("vaca"):
            iguales += 1
        elif a.pisar:
            ets[rid] = et
            pisadas += 1
        else:
            conflicto += 1
            print(f"  {rid}: en esta compu dice {previa.get('vaca') or previa.get('accion')}, "
                  f"el equipo dice {et.get('vaca') or et['accion']} (se deja el de esta compu)")
    for rid, si in res.get("despues", {}).items():
        if rid in recs and rid not in ets:
            post[rid] = bool(si)

    for nombre, datos in (("etiquetas.json", ets), ("postergados.json", post)):
        tmp = DIR / f"{nombre}.tmp"
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(DIR / nombre)
    print(f"Listo: {nuevas} etiquetas nuevas, {iguales} ya estaban igual, {pisadas} reemplazadas, "
          f"{conflicto} con diferencias (se respetó esta compu), {no_existe} de recorridos que ya no existen.")
    print("Ahora podés abrir campo.py.")


if __name__ == "__main__":
    main()
