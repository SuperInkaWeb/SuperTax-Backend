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
from src.modules.scanner.infrastructure.models import ScannerJobModel
from src.modules.scanner.infrastructure.repositories import SqlScannerJobRepository
from src.platform.storage.base import FileStorage

logger = logging.getLogger("scanner.jobs")


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
