"""
Despacho on-demand de la ingesta (sin worker que sondea), espejo de sire/dispatch.

Al subir un archivo, la API encola aquí la conversión y la despacha en el pool de
hilos acotado del proceso web. El trabajo pesado (DuckDB) corre en un SUBPROCESO
efímero que muere al terminar, devolviendo el 100% de su RAM al SO; el proceso web
es el único que escribe el estado del dataset en la BD.
"""
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from src.modules.datafilter.application.ingest import convertir, friendly_error
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.tasks import submit as submit_background

logger = logging.getLogger("datafilter.dispatch")

_POOL = "datafilter"
# "spawn" (no "fork") para arrancar un intérprete limpio y evitar deadlocks.
_MP_CONTEXT = multiprocessing.get_context("spawn")


def _en_subproceso(payload: dict) -> dict:
    """Corre la conversión (CPU/RAM-bound) en un subproceso efímero y devuelve su
    resumen. El subproceso muere al salir del `with`, liberando su memoria al SO."""
    with ProcessPoolExecutor(max_workers=1, mp_context=_MP_CONTEXT) as executor:
        return executor.submit(convertir, payload).result()


def _procesar(dataset_id: int) -> None:
    """Corre en el pool de hilos: reclama el dataset y lo convierte. Si otro ya lo
    tomó (o no está en cola), no hace nada."""
    db = SessionLocal()
    try:
        meta = SqlDatasetRepository(db).claim(dataset_id)
    finally:
        db.close()
    if meta is None:
        return
    company_id, extension, active_sheet = meta

    payload = {
        "company_id": company_id,
        "dataset_id": dataset_id,
        "extension": extension,
        "sheet": active_sheet,
    }
    try:
        result = _en_subproceso(payload)
    except Exception as exc:  # el hijo falló o murió (OOM) → FAILED con mensaje corto
        logger.exception("Ingesta del dataset %s falló", dataset_id)
        db = SessionLocal()
        try:
            SqlDatasetRepository(db).set_failed(dataset_id, friendly_error(exc))
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        SqlDatasetRepository(db).set_ready(dataset_id, result)
        logger.info("Ingesta OK: dataset %s (%s filas)", dataset_id, result["row_count"])
    finally:
        db.close()


def encolar_ingesta(dataset_id: int) -> None:
    """Despacha la ingesta al pool on-demand del web; la concurrencia la limita
    `DATAFILTER_MAX_CONCURRENCY` (el resto espera turno)."""
    submit_background(_POOL, settings.DATAFILTER_MAX_CONCURRENCY, _procesar, dataset_id)


def recuperar_pendientes() -> None:
    """Al arrancar el web: marca `failed` los datasets que quedaron en `processing`
    (interrumpidos por un redeploy) y re-despacha los que quedaron en `pending`."""
    db = SessionLocal()
    try:
        repo = SqlDatasetRepository(db)
        interrumpidos = repo.marcar_interrumpidos()
        pendientes = repo.ids_pendientes()
    finally:
        db.close()
    if interrumpidos:
        logger.warning("DataFilter: %s ingesta(s) interrumpidas marcadas como error", interrumpidos)
    for dataset_id in pendientes:
        encolar_ingesta(dataset_id)
    if pendientes:
        logger.info("DataFilter: re-despachadas %s ingesta(s) en cola", len(pendientes))
