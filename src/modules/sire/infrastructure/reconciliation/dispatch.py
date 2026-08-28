"""
Despacho on-demand de las conciliaciones SIRE (sin worker que sondea).

Cuando el usuario crea o reanuda una conciliación, la API la encola y la despacha
aquí mismo, en el pool de hilos acotado del proceso web. Así no hay un worker
sondeando la base 24/7 (Neon puede suspenderse cuando no hay actividad).

Mismo motor que el worker (`orchestrator.procesar_job`, asíncrono, con su
subproceso efímero para el motor); solo cambia quién lo dispara.
"""
import asyncio
import logging

from src.modules.sire.domain.entities import JobStatus
from src.modules.sire.infrastructure.reconciliation.orchestrator import procesar_job
from src.modules.sire.infrastructure.repositories import SqlReconciliationRepository
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.tasks import submit as submit_background

logger = logging.getLogger("sire.dispatch")

_POOL = "sire"


def _despachar(job_id: int) -> None:
    """Corre dentro del pool: reclama el job y lo procesa. Si otro proceso ya lo
    tomó (o ya no está en cola), no hace nada."""
    db = SessionLocal()
    try:
        reclamado = SqlReconciliationRepository(db).claim(job_id)
    finally:
        db.close()
    if not reclamado:
        return
    db = SessionLocal()
    try:
        asyncio.run(procesar_job(db, job_id))
    finally:
        db.close()


def encolar_ejecucion(job_id: int) -> None:
    """Despacha la conciliación al pool on-demand del proceso web. La concurrencia
    la limita `SIRE_MAX_CONCURRENCY`."""
    submit_background(_POOL, settings.SIRE_MAX_CONCURRENCY, _despachar, job_id)


def recuperar_pendientes() -> None:
    """Al arrancar el web (una sola instancia asumida): marca `error` los jobs que
    quedaron en `procesando` (interrumpidos por un redeploy) y re-despacha los que
    quedaron `en_cola`."""
    db = SessionLocal()
    try:
        repo = SqlReconciliationRepository(db)
        interrumpidos = repo.marcar_estado_masivo(
            JobStatus.procesando,
            JobStatus.error,
            mensaje="El proceso se interrumpió (reinicio del servidor). Vuelve a intentarlo.",
        )
        pendientes = repo.ids_por_estado(JobStatus.en_cola)
    finally:
        db.close()
    if interrumpidos:
        logger.warning("SIRE: %s job(s) interrumpidos marcados como error", interrumpidos)
    for job_id in pendientes:
        encolar_ejecucion(job_id)
    if pendientes:
        logger.info("SIRE: re-despachados %s job(s) que estaban en cola", len(pendientes))
