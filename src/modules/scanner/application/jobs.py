"""
Casos de uso de la cola de extracción del Scanner.

`encolar_documento` (lado API): valida, guarda el archivo en storage y crea el job
`en_cola`. `procesar_job` (lado worker): descarga el archivo, ejecuta el pipeline
de OCR y marca el job como completado o con error.
"""
import logging
import os
import tempfile
import uuid

from sqlalchemy.orm import Session

from src.modules.scanner.application.extraccion import (
    ScannerExtractionError,
    procesar_archivo,
    validar_subida,
)
from src.modules.scanner.infrastructure.models import ScannerJobModel, ScannerJobStatus
from src.modules.scanner.infrastructure.repositories import SqlScannerJobRepository
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tasks import submit as submit_background

logger = logging.getLogger("scanner.jobs")

_POOL = "scanner"


def encolar_documento(
    db: Session,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    nombre_archivo: str,
    contenido: bytes,
    tipo_forzado: str | None = None,
) -> ScannerJobModel:
    """Valida y encola un documento para extracción por el worker."""
    validar_subida(nombre_archivo, contenido)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in nombre_archivo)
    storage_path = f"scanner/uploads/{company_id}/{uuid.uuid4().hex}_{safe}"
    storage.save(storage_path, contenido)
    return SqlScannerJobRepository(db).create(
        company_id=company_id,
        user_id=user_id,
        nombre_archivo=nombre_archivo,
        storage_path=storage_path,
        tipo_forzado=tipo_forzado or None,
    )


def procesar_job(db: Session, storage: FileStorage, job_id: int) -> None:
    """Procesa un job encolado: descarga, extrae y marca el resultado."""
    repo = SqlScannerJobRepository(db)
    job = db.get(ScannerJobModel, job_id)
    if job is None:
        return

    ext = os.path.splitext(job.nombre_archivo or "")[1].lower()
    ruta_local: str | None = None
    try:
        contenido = storage.read(job.storage_path)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(contenido)
            ruta_local = tmp.name

        doc = procesar_archivo(
            db, storage, job.company_id, job.created_by_id, job.nombre_archivo, ruta_local,
            tipo_forzado=job.tipo_forzado,
        )
        repo.mark_completado(job_id, doc.id)
    except ScannerExtractionError as exc:
        repo.mark_error(job_id, str(exc))
    except Exception:
        logger.exception("Job de scanner #%s falló", job_id)
        repo.mark_error(job_id, "Error inesperado al procesar el documento.")
    finally:
        if ruta_local and os.path.exists(ruta_local):
            os.remove(ruta_local)
        # El archivo subido ya no se necesita: el documento tiene su propia copia.
        try:
            storage.delete(job.storage_path)
        except Exception:
            logger.warning("No se pudo borrar el archivo subido del job #%s", job_id)


def _despachar(job_id: int) -> None:
    """Corre dentro del pool: reclama el job y lo procesa. Si otro proceso ya lo
    tomó (o ya no está en cola), no hace nada."""
    db = SessionLocal()
    try:
        reclamado = SqlScannerJobRepository(db).claim(job_id)
    finally:
        db.close()
    if not reclamado:
        return
    db = SessionLocal()
    try:
        procesar_job(db, get_storage(), job_id)
    finally:
        db.close()


def encolar_ejecucion(job_id: int) -> None:
    """Despacha la extracción al pool on-demand del proceso web (sin worker que
    sondea). La concurrencia la limita `SCANNER_MAX_CONCURRENCY`."""
    submit_background(_POOL, settings.SCANNER_MAX_CONCURRENCY, _despachar, job_id)


def recuperar_pendientes() -> None:
    """Al arrancar el web (una sola instancia asumida): marca `error` los jobs que
    quedaron en `procesando` (interrumpidos por un redeploy) y re-despacha los que
    quedaron `en_cola`."""
    db = SessionLocal()
    try:
        repo = SqlScannerJobRepository(db)
        interrumpidos = repo.marcar_estado_masivo(
            ScannerJobStatus.procesando,
            ScannerJobStatus.error,
            mensaje="El proceso se interrumpió (reinicio del servidor). Vuelve a intentarlo.",
        )
        pendientes = repo.ids_por_estado(ScannerJobStatus.en_cola)
    finally:
        db.close()
    if interrumpidos:
        logger.warning("Scanner: %s job(s) interrumpidos marcados como error", interrumpidos)
    for job_id in pendientes:
        encolar_ejecucion(job_id)
    if pendientes:
        logger.info("Scanner: re-despachados %s job(s) que estaban en cola", len(pendientes))
