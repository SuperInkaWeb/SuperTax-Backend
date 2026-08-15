"""
Worker de extracción de documentos del Scanner (cola sobre Postgres).

Toma el job 'en_cola' más antiguo con `FOR UPDATE SKIP LOCKED` (permite correr
varios workers en paralelo sin doble-procesar), lo marca 'procesando' y ejecuta
el OCR/extracción. Se despliega como un proceso aparte de la API.

    python -m workers.scanner_worker
"""
import logging
import time

from sqlalchemy import select

import src.models_registry  # noqa: F401  (registra todos los modelos: FKs a core.*)
from src.modules.scanner.application.jobs import procesar_job
from src.modules.scanner.infrastructure.models import ScannerJobModel, ScannerJobStatus
from src.platform.database.session import SessionLocal
from src.platform.storage import get_storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner.worker")

POLL_SECONDS = 5


def _claim_next_job() -> int | None:
    db = SessionLocal()
    try:
        row = db.execute(
            select(ScannerJobModel)
            .where(ScannerJobModel.status == ScannerJobStatus.en_cola)
            .order_by(ScannerJobModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = ScannerJobStatus.procesando
        db.commit()
        return row.id
    finally:
        db.close()


def _process(job_id: int) -> None:
    db = SessionLocal()
    try:
        procesar_job(db, get_storage(), job_id)
    finally:
        db.close()


def run() -> None:
    logger.info("Worker Scanner iniciado (poll cada %ss)", POLL_SECONDS)
    while True:
        # Un fallo transitorio no debe tumbar el worker; los errores de un job
        # concreto ya los captura procesar_job y los marca en el job.
        try:
            job_id = _claim_next_job()
            if job_id is None:
                time.sleep(POLL_SECONDS)
                continue
            logger.info("Procesando job #%s", job_id)
            _process(job_id)
        except Exception:
            logger.exception("Error en el ciclo del worker; se reintenta tras la pausa")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
