"""
extraer_frames.py
Extrae frames de un video (o de todos los videos de una carpeta) a una tasa fija.
Por defecto: 5 frames por segundo, guardados como JPG.

Uso:
    python extraer_frames.py "C:\\ruta\\al\\video.mp4"
    python extraer_frames.py "C:\\ruta\\al\\video.mp4" --fps 5 --salida "C:\\ruta\\frames"
    python extraer_frames.py "C:\\ruta\\carpeta_con_videos"

Requisito:
    pip install opencv-python
"""

import argparse
import sys
from pathlib import Path

import cv2

EXTENSIONES_VIDEO = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv", ".mts", ".ts", ".3gp"}


def guardar_imagen(ruta: Path, frame, calidad: int) -> bool:
    """Guarda la imagen soportando rutas con tildes/ñ en Windows (cv2.imwrite falla con esas)."""
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    if not ok:
        return False
    buffer.tofile(str(ruta))
    return True


def extraer_frames(video: Path, carpeta_salida: Path, fps_objetivo: float, calidad: int) -> int:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"  [ERROR] No se pudo abrir el video: {video}")
        return 0

    fps_video = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if not fps_video or fps_video <= 0:
        print("  [AVISO] El video no informa sus FPS, asumo 30.")
        fps_video = 30.0

    duracion = total_frames / fps_video if total_frames > 0 else 0
    print(f"  FPS del video: {fps_video:.2f} | Frames totales: {total_frames} | Duración: {duracion:.1f} s")

    if fps_objetivo > fps_video:
        print(f"  [AVISO] Pediste {fps_objetivo} fps pero el video tiene {fps_video:.2f}; se guardan todos los frames.")
        fps_objetivo = fps_video

    carpeta_salida.mkdir(parents=True, exist_ok=True)

    intervalo = 1.0 / fps_objetivo   # segundos entre frames guardados
    proximo_tiempo = 0.0
    indice = 0
    guardados = 0
    nombre_base = video.stem

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        tiempo = indice / fps_video
        # Pequeña tolerancia para errores de redondeo
        if tiempo + 1e-6 >= proximo_tiempo:
            segundos = int(tiempo)
            ms = int(round((tiempo - segundos) * 1000))
            nombre = f"{nombre_base}_{guardados:06d}_t{segundos:05d}s{ms:03d}.jpg"
            if guardar_imagen(carpeta_salida / nombre, frame, calidad):
                guardados += 1
            else:
                print(f"  [ERROR] No se pudo guardar el frame {indice}")
            proximo_tiempo += intervalo

        indice += 1
        if total_frames > 0 and indice % 500 == 0:
            print(f"  Progreso: {indice}/{total_frames} frames leídos ({indice * 100 // total_frames}%)")

    cap.release()
    return guardados


def main():
    parser = argparse.ArgumentParser(description="Extrae frames de video a una tasa fija (por defecto 5 fps).")
    parser.add_argument("entrada", help="Ruta a un video o a una carpeta con videos")
    parser.add_argument("--fps", type=float, default=5.0, help="Frames por segundo a extraer (default: 5)")
    parser.add_argument("--salida", default=None,
                        help="Carpeta de salida (default: carpeta 'frames_<nombre>' al lado del video)")
    parser.add_argument("--calidad", type=int, default=95, help="Calidad JPG 1-100 (default: 95)")
    args = parser.parse_args()

    entrada = Path(args.entrada)
    if not entrada.exists():
        print(f"No existe: {entrada}")
        sys.exit(1)

    if entrada.is_dir():
        videos = sorted(p for p in entrada.iterdir() if p.suffix.lower() in EXTENSIONES_VIDEO)
        if not videos:
            print(f"No encontré videos en {entrada}")
            sys.exit(1)
    else:
        videos = [entrada]

    total = 0
    for video in videos:
        if args.salida:
            salida = Path(args.salida) / video.stem if len(videos) > 1 else Path(args.salida)
        else:
            salida = video.parent / f"frames_{video.stem}"

        print(f"\nProcesando: {video.name}")
        print(f"  Guardando en: {salida}")
        n = extraer_frames(video, salida, args.fps, args.calidad)
        print(f"  Listo: {n} frames guardados.")
        total += n

    print(f"\nTerminado. Total de frames guardados: {total}")


if __name__ == "__main__":
    main()