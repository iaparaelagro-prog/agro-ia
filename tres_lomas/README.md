# Tres Lomas: identificación de vacas y pruebas de montas

Herramientas armadas a partir del desfile de Tres Lomas (video `desfile_camaras.mp4`, 124 vacas con fotos
de costado lado A / lado B) para construir la galería de vacas, identificar vacas vistas desde arriba en el
campo y entrenar los modelos de identificación que usa FORMATEADOR.

**Qué hay en git:** el código, las etiquetas que hizo el equipo (`etiquetas/`) y los pesos entrenados
(`identificacion/modelos/`, con Git LFS). **Qué NO hay:** las imágenes y el video (frames, fotos de
referencia lado A / B, recortes) ni los datasets armados: están en la compu
(`Escritorio/Vaquitas/desfile_tres_lomas`), en el servidor de DigitalOcean y en Google Drive
(`Mi unidad/vacas/`). Los scripts esperan encontrar los datos en la carpeta desde donde se ejecutan.

## Pesos entrenados

Van en `identificacion/modelos/` y se guardan con **Git LFS** (configurado en `.gitattributes` de la raíz).
Para bajarlos: `git lfs install` una vez y después `git pull` (o `git lfs pull` si el repo ya estaba clonado).

| Archivo | Qué es |
|---|---|
| `resnet50_triplet_vacas.pth` | ResNet50 Triplet entrenado (el modelo de identificación que usamos). Se carga con `EmbeddingExtractor` de `servicioSeguimientoIdentificacion`. |
| `dinov2_vacas.pt` | DINOv2 entrenado; `campo.py` lo usa si está en la carpeta de datos. |

Los **checkpoints** para seguir entrenando desde donde quedó (incluyen el estado del optimizador) son más
grandes y están en Google Drive: `resultado_resnet50/checkpoint_como_dinov2.pt` (284 MB) y
`resultado_dinov2/checkpoint.pt` (144 MB). Los notebooks los retoman si los encuentran en la carpeta de salida.
Las métricas del DINOv2 están en `notebooks/metricas_dinov2.json`.

## Etiquetas del equipo (`etiquetas/`)

Es el trabajo manual de identificación; con esto + los frames se puede rearmar el dataset de entrenamiento.

| Archivo | Qué es |
|---|---|
| `campo_equipo/etiquetas.json` | Lo que marcó el equipo en la página "Vacas en el campo": por recorrido (`r00002`, ...) qué vaca es, o si se descartó / no se sabe. 578 recorridos. |
| `campo_equipo/recorridos.json` | Qué frames y recuadros forman cada recorrido. |
| `campo_equipo/resultado_por_frame.csv` | Etiqueta por frame y recuadro; es lo que lee `preparar_dinov2.py`. |
| `campo_equipo/problemas.json`, `postergados.json`, `estado.json` | Frames marcados con problema (p. ej. dos vacas), recorridos postergados y estado de la página. |
| `grupos.json` | Orden verificado de paso de las vacas por tanda en el desfile. |
| `biblioteca_lados/` | Etiquetas de la etapa anterior (recortes de costado confirmados para ampliar la galería). |

Para usarlas con los scripts, copiá `campo_equipo/` como `campo_servidor/` dentro de la carpeta de datos
(es el nombre que esperan `preparar_dinov2.py` y `campo.py`).

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

## Supabase

- `identificacion/inventario_supabase.py`: cuenta qué hay en Supabase (tablas, montas, imágenes del bucket
  `imagenesvacas` por cámara y día) para decidir qué usar para entrenar. No cambia nada.
- `identificacion/limpiar_supabase.py`: libera espacio del bucket borrando las imágenes más viejas.
  Por defecto solo simula; nunca borra frames con monta ni lo de los últimos 14 días. Necesita la clave
  `service_role` en un `supabase.env` (que no va a git).

## Servidor (DigitalOcean)

Ver `identificacion/servidor/GUIA.md`. La página del equipo corre `campo.py` como servicio `campo`.

## Montas

`montas/armar_montas.py`: junta en `MONTAS/<día>/` todos los frames de las carpetas de pruebas de montas
(`detecciones_monta_*`), sin repetir y prefiriendo la versión limpia de `dataset_crudo`.
