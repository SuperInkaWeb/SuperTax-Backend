"""
Worker de descargas SUNAT (cola sobre Postgres).

Toma el job 'en_cola' más antiguo con `FOR UPDATE SKIP LOCKED` (permite correr
varios workers en paralelo sin doble-procesar), lo marca 'procesando' y corre la
automatización Playwright. Se despliega como un proceso aparte de la API.

    python -m workers.sunat_worker
"""
import logging
import time

from sqlalchemy import select

import src.models_registry  # noqa: F401  (registra todos los modelos: FKs a core.*)
from src.modules.sunat.infrastructure.job_queue import procesar_job
from src.modules.sunat.infrastructure.models import SunatJobModel, SunatJobStatus
from src.platform.database.session import SessionLocal
from src.platform.storage import get_storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sunat.worker")

POLL_SECONDS = 5


def _claim_next_job() -> str | None:
    db = SessionLocal()
    try:
        row = db.execute(
            select(SunatJobModel)
            .where(SunatJobModel.status == SunatJobStatus.en_cola)
            .order_by(SunatJobModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = SunatJobStatus.procesando
        db.commit()
        return row.job_id
    finally:
        db.close()


def _marcar_interrumpidos() -> None:
    """Al arrancar, marca como 'error' los jobs que quedaron en 'procesando' por un
    reinicio del worker: nadie los retoma (solo se reclaman los 'en_cola'). Asume
    un único worker por cola. El detalle del error vive en la tabla de logs."""
    db = SessionLocal()
    try:
        stale = (
            db.execute(
                select(SunatJobModel).where(
                    SunatJobModel.status == SunatJobStatus.procesando
                )
            )
            .scalars()
            .all()
        )
        for job in stale:
            job.status = SunatJobStatus.error
        if stale:
            db.commit()
            logger.warning("Marcados %s job(s) interrumpidos como error", len(stale))
    finally:
        db.close()


def run() -> None:
    logger.info("Worker SUNAT iniciado (poll cada %ss)", POLL_SECONDS)
    _marcar_interrumpidos()
    while True:
        # Un fallo transitorio no debe tumbar el worker; los errores de un job
        # concreto ya los captura procesar_job y los marca en el job.
        try:
            job_id = _claim_next_job()
            if job_id is None:
                time.sleep(POLL_SECONDS)
                continue
            logger.info("Procesando job %s", job_id)
            procesar_job(get_storage(), job_id)
        except Exception:
            logger.exception("Error en el ciclo del worker; se reintenta tras la pausa")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
