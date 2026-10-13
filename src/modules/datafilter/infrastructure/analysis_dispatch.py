"""
Despacho on-demand de la materialización de datasets derivados (columnas
calculadas, buscar/reemplazar, eliminar duplicados guardados como dataset nuevo).

Espejo de `ingest_dispatch`: el web planifica el SQL (lee metadatos del origen),
corre el COPY pesado en un SUBPROCESO efímero que muere al terminar (devolviendo su
RAM al SO) y es el único que escribe el estado del dataset derivado en la BD.
"""
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from src.modules.datafilter.application import analysis
from src.modules.datafilter.application.ingest import friendly_error
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.tasks import submit as submit_background

logger = logging.getLogger("datafilter.analysis")

_POOL = "datafilter"
# "spawn" (no "fork") para arrancar un intérprete limpio y evitar deadlocks.
_MP_CONTEXT = multiprocessing.get_context("spawn")


def _en_subproceso(payload: dict) -> dict:
    """Corre la materialización (CPU/RAM-bound) en un subproceso efímero y devuelve su
    resumen. El subproceso muere al salir del `with`, liberando su memoria al SO."""
    with ProcessPoolExecutor(max_workers=1, mp_context=_MP_CONTEXT) as executor:
        return executor.submit(analysis.materializar, payload).result()


def _procesar(new_id: int, source_id: int, company_id: int, spec: dict) -> None:
    """Corre en el pool de hilos: planifica el SQL, materializa en un subproceso y
    persiste el estado del dataset derivado."""
    db = SessionLocal()
    try:
        payload = analysis.planificar(db, source_id, company_id, new_id, spec)
    except Exception as exc:  # error de validación/plan → FAILED con mensaje corto
        logger.exception("No se pudo planificar el dataset derivado %s", new_id)
        _marcar_fallido(new_id, friendly_error(exc))
        return
    finally:
        db.close()

    try:
        result = _en_subproceso(payload)
    except Exception as exc:  # el hijo falló o murió (OOM) → FAILED con mensaje corto
        logger.exception("Materialización del dataset %s falló", new_id)
        _marcar_fallido(new_id, friendly_error(exc))
        return

    db = SessionLocal()
    try:
        SqlDatasetRepository(db).set_ready(new_id, result)
        logger.info("Dataset derivado %s listo (%s filas)", new_id, result["row_count"])
    finally:
        db.close()


def _marcar_fallido(new_id: int, error: str) -> None:
    db = SessionLocal()
    try:
        SqlDatasetRepository(db).set_failed(new_id, error)
    finally:
        db.close()


def encolar_materializacion(
    new_id: int, source_id: int, company_id: int, spec: dict
) -> None:
    """Despacha la materialización al pool on-demand del web; la concurrencia la limita
    `DATAFILTER_MAX_CONCURRENCY` (comparte cupo con la ingesta)."""
    submit_background(
        _POOL, settings.DATAFILTER_MAX_CONCURRENCY, _procesar,
        new_id, source_id, company_id, spec,
    )
