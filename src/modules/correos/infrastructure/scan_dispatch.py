"""
Despacho on-demand del escaneo (sin worker que sondea, espejo de sire/sunat).

Al lanzar un escaneo, la API lo encola en el pool de hilos acotado del proceso web
(`CORREOS_MAX_CONCURRENCY`); el resto espera turno. El escaneo es I/O-bound (API del
buzón + R2); el proceso web escribe el estado/progreso en la BD.
"""
import logging

from src.modules.correos.application.scan import ejecutar_escaneo
from src.modules.correos.infrastructure.repositories import SqlScanRepository
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.tasks import submit as submit_background

logger = logging.getLogger("correos.dispatch")

_POOL = "correos"


def encolar_escaneo(job_id: int) -> None:
    """Despacha el escaneo al pool on-demand del web; `CORREOS_MAX_CONCURRENCY` acota
    cuántos corren a la vez (el resto espera turno)."""
    submit_background(_POOL, settings.CORREOS_MAX_CONCURRENCY, ejecutar_escaneo, job_id)


def recuperar_pendientes() -> None:
    """Al arrancar el web: marca `failed` los escaneos que quedaron a medias (redeploy)
    y re-despacha los que quedaron en cola."""
    db = SessionLocal()
    try:
        repo = SqlScanRepository(db)
        interrumpidos = repo.marcar_interrumpidos()
        pendientes = repo.ids_pendientes()
    finally:
        db.close()
    if interrumpidos:
        logger.warning("Correos: %s escaneo(s) interrumpidos marcados como error", interrumpidos)
    for job_id in pendientes:
        encolar_escaneo(job_id)
    if pendientes:
        logger.info("Correos: re-despachados %s escaneo(s) en cola", len(pendientes))
