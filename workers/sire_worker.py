"""
Worker de conciliaciones SIRE (cola sobre Postgres).

Toma el job 'en_cola' más antiguo con `FOR UPDATE SKIP LOCKED` (permite correr
varios workers en paralelo sin doble-procesar), lo marca 'procesando' y ejecuta
la orquestación. Se despliega como un proceso aparte de la API.

    python -m workers.sire_worker
"""
import asyncio
import logging
import time

from sqlalchemy import select

from src.modules.sire.domain.entities import JobStatus
from src.modules.sire.infrastructure.models import ReconciliationJobModel
from src.modules.sire.infrastructure.reconciliation.orchestrator import procesar_job
from src.platform.database.session import SessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sire.worker")

POLL_SECONDS = 5


def _claim_next_job() -> int | None:
    db = SessionLocal()
    try:
        row = db.execute(
            select(ReconciliationJobModel)
            .where(ReconciliationJobModel.status == JobStatus.en_cola)
            .order_by(ReconciliationJobModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = JobStatus.procesando
        db.commit()
        return row.id
    finally:
        db.close()


def _process(job_id: int) -> None:
    db = SessionLocal()
    try:
        asyncio.run(procesar_job(db, job_id))
    finally:
        db.close()


def run() -> None:
    logger.info("Worker SIRE iniciado (poll cada %ss)", POLL_SECONDS)
    while True:
        # Un fallo transitorio (p. ej. caída momentánea de la DB) no debe tumbar
        # el worker: se registra y se reintenta tras la pausa. Los errores de un
        # job concreto ya los captura procesar_job y los marca como 'error'.
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
