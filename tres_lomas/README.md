# Tres Lomas: identificación de vacas y pruebas de montas

Herramientas armadas a partir del desfile de Tres Lomas (video `desfile_camaras.mp4`, 124 vacas con fotos
de costado lado A / lado B) para construir la galería de vacas, identificar vacas vistas desde arriba en el
campo y entrenar los modelos de identificación que usa FORMATEADOR.

**Acá va solo el código.** Los datos (video, frames, fotos de referencia, recortes, etiquetas), los
datasets y los modelos entrenados (`*.pt`, `*.pth`) NO van a git: están en la compu de Diego
(`Escritorio/Vaquitas/desfile_tres_lomas`), en el servidor de DigitalOcean y en Google Drive
(`Mi unidad/vacas/`). Los scripts esperan encontrar los datos en la carpeta desde donde se ejecutan.

## Estructura

```
tres_lomas/
  identificacion/        scripts de Python + páginas web (se corren desde la carpeta de datos)
    servidor/            instalación y mantenimiento de la página en DigitalOcean
  notebooks/             entrenamiento en Google Colab (GPU)
  montas/                herramientas para las pruebas de detección de montas
```

## Flujo de trabajo (en orden)

| Paso | Script | Qué hace |
|---|---|---|
| 1 | `frames.py` | Saca frames del video del desfile (5 por segundo). |
| 2 | `borrar_sin_vacas.py` | Borra los frames sin vacas (YOLO). |
| 3 | `quitar_fondo.py` + `interfaz.html` | Quita el fondo de las fotos de referencia (lado A / B) y permite revisarlas. |
| 4 | `exportar_mal.py`, `corregir_fondo.py` | Corrige con SAM las que salieron mal. |
| 5 | `biblioteca_lados.py` + `interfaz_lados.html`, `armar_biblioteca.py` | Amplía la galería con más recortes de costado de cada vaca (`bib_*.png`). |
| 6 | `identificador.py` | Base común: detector (YOLO11), silueta (SAM 2.1), huella (DINOv2), galería y ajuste LDA. |
| 7 | `campo.py` + `interfaz_campo.html` | Página "Vacas en el campo": arma **recorridos** (cada pasada de una vaca por la cámara), calcula la huella del mejor frame y sugiere qué vaca es; el equipo confirma. |
| 8 | `exportar_equipo.py`, `importar_equipo.py` | Llevar y traer el trabajo del equipo. |
| 9 | `preparar_dinov2.py` | Arma el dataset de entrenamiento desde lo etiquetado (filtra choques y frames con problema, separa la prueba por recorrido, balancea) → `dataset_dinov2.zip`. |
| 10 | `notebooks/entrenar_dinov2.ipynb` | Entrena DINOv2 en Colab → `dinov2_vacas.pt`. |
| 11 | `notebooks/entrenar_resnet50.ipynb` | Entrena el ResNet50 Triplet de FORMATEADOR con los mismos datos y la misma prueba → `resnet50_triplet_vacas.pth` (se carga directo con `EmbeddingExtractor` de `servicioSeguimientoIdentificacion`). |

`campo.py` usa por defecto `dinov2_vacas.pt` si está en la carpeta (`--modelo-huella original` para el
DINOv2 de fábrica). Al cambiar de modelo recalcula solo las huellas guardadas.

## Resultados (noviembre 2026)

Prueba: 88 recorridos de 45 vacas que ningún modelo vio al entrenar; 124 vacas candidatas.
"A" = galería con fotos de referencia + recortes del campo. "B" = solo fotos de referencia.

| Modelo | A: acierto 1° (top 5) | B: acierto 1° (top 5) |
|---|---|---|
| DINOv2 original | 61% (81%) | 3% (11%) |
| DINOv2 entrenado | 73% (89%) | 74% (91%) |
| ResNet50 ImageNet | 64% | 2% |
| ResNet50 entrenado | 72% (90%) | 74% (88%) |

Con 88 recorridos el margen es de ±5 puntos: los dos modelos entrenados rinden igual.

## Servidor (DigitalOcean)

Ver `identificacion/servidor/GUIA.md`. La página del equipo corre `campo.py` como servicio `campo`.

## Montas

`montas/armar_montas.py`: junta en `MONTAS/<día>/` todos los frames de las carpetas de pruebas de montas
(`detecciones_monta_*`), sin repetir y prefiriendo la versión limpia de `dataset_crudo`.
