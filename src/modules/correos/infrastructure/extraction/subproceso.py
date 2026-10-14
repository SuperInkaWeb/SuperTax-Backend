"""
Ejecución del OCR/IA pesado en un subproceso efímero (espejo de datafilter/ingesta).

El reconocimiento de un adjunto carga pymupdf/opencv/tesseract y rasteriza imágenes
grandes: en el proceso web —always-on— esa RAM no se devolvería al SO. Correrlo en
un subproceso que muere al terminar libera el 100% de su memoria y acota el costo
(Railway factura por RAM·segundo). El proceso web es el único que toca la BD y R2.
"""
from __future__ import annotations

import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from src.modules.correos.infrastructure.extraction.pipeline import extraer_en_subproceso

# "spawn" (no "fork"): intérprete limpio, sin heredar locks del proceso web.
_MP_CONTEXT = multiprocessing.get_context("spawn")


def ejecutar(payload: dict) -> dict:
    """Corre la extracción (CPU/RAM-bound) en un subproceso efímero y devuelve su
    resultado. El subproceso muere al salir del `with`, liberando su memoria al SO."""
    with ProcessPoolExecutor(max_workers=1, mp_context=_MP_CONTEXT) as executor:
        return executor.submit(extraer_en_subproceso, payload).result()
