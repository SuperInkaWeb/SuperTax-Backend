"""
Runner de jobs de descarga SUNAT (en memoria + hilos + colas para SSE).

Modelo heredado del proyecto SUNAT: cada job corre en un hilo con Playwright y
emite logs/progreso por colas que el endpoint SSE transmite al navegador. La
automatización pesada (Playwright/Google) se importa de forma perezosa, así el
API arranca sin requerir el navegador instalado.

Vive en memoria del proceso: válido con un solo proceso de API (misma restricción
que el caché de tokens). Para varias réplicas habría que respaldarlo en Redis.
"""
import json
import logging
import os
import queue
import shutil
import tempfile
import threading
import time
import uuid

from fastapi import UploadFile

from src.modules.sunat.infrastructure.models import DriveTokenModel, JobResultModel
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.security import encrypt_field

_log = logging.getLogger("sunat.jobs")

DESCARGAS_DIR = settings.DESCARGAS_DIR or os.path.join(
    tempfile.gettempdir(), "SUNAT_Descargas"
)
os.makedirs(DESCARGAS_DIR, exist_ok=True)

MAX_EXCEL_BYTES = 10 * 1024 * 1024  # 10 MB

jobs: dict = {}
JOB_TTL_SEGUNDOS = 2 * 60 * 60

_preview_files: dict = {}  # preview_id → (ruta, monotonic_ts)
_PREVIEW_TTL = 2 * 60 * 60


def guardar_preview(ruta: str) -> str:
    pid = str(uuid.uuid4())
    _preview_files[pid] = (ruta, time.monotonic())
    return pid


def consumir_preview(preview_id: str) -> str | None:
    entry = _preview_files.pop(preview_id, None)
    if not entry:
        return None
    ruta, ts = entry
    if time.monotonic() - ts > _PREVIEW_TTL or not os.path.exists(ruta):
        try:
            os.unlink(ruta)
        except OSError:
            pass
        return None
    return ruta


def _limpiar_jobs_viejos() -> None:
    while True:
        time.sleep(30 * 60)
        ahora = time.monotonic()
        for jid in [
            j for j, v in list(jobs.items())
            if ahora - v.get("created_at", ahora) > JOB_TTL_SEGUNDOS
        ]:
            jobs.pop(jid, None)
        for pid, (ruta, ts) in list(_preview_files.items()):
            if ahora - ts > _PREVIEW_TTL:
                _preview_files.pop(pid, None)
                try:
                    os.unlink(ruta)
                except OSError:
                    pass


threading.Thread(target=_limpiar_jobs_viejos, daemon=True).start()


def _make_persist_drive_token(company_id: int):
    """Callback para que la automatización guarde el access token de Drive renovado."""

    def _persist(nuevo_access_token: str) -> None:
        db = SessionLocal()
        try:
            token = (
                db.query(DriveTokenModel)
                .filter(DriveTokenModel.company_id == company_id)
                .first()
            )
            if token is None:
                token = DriveTokenModel(company_id=company_id)
                db.add(token)
            token.access_token_enc = encrypt_field(nuevo_access_token)
            db.commit()
        finally:
            db.close()

    return _persist


def _guardar_resultado_job(
    job_id: str, company_id: int, user_id: int, resultados: list
) -> None:
    if not resultados:
        return
    db = SessionLocal()
    try:
        db.add(
            JobResultModel(
                job_id=job_id,
                company_id=company_id,
                created_by_id=user_id,
                resultados=json.dumps(resultados),
            )
        )
        db.commit()
    except Exception as exc:  # el resultado es best-effort; no debe tumbar el job
        _log.warning("No se pudo guardar el resultado del job %s: %s", job_id, exc)
    finally:
        db.close()


async def excel_a_tmp(
    excel: UploadFile | None,
    excel_link: str,
    drive_access: str,
    drive_refresh: str,
) -> str:
    """Descarga o lee el Excel a un archivo temporal. Lanza ValueError si falla."""
    if excel_link.strip():
        from src.modules.sunat.infrastructure.automation.drive import descargar_excel

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        tmp.close()
        try:
            descargar_excel(excel_link.strip(), tmp.name, drive_access, drive_refresh)
        except Exception as exc:
            os.unlink(tmp.name)
            raise ValueError(f"No se pudo descargar el Excel de Drive: {exc}")
        return tmp.name
    if excel:
        if not (excel.filename or "").lower().endswith((".xlsx", ".xls")):
            raise ValueError("El archivo debe ser un Excel (.xlsx o .xls)")
        content = await excel.read()
        if len(content) > MAX_EXCEL_BYTES:
            raise ValueError("El archivo es demasiado grande (máximo 10 MB)")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        tmp.write(content)
        tmp.close()
        return tmp.name
    raise ValueError("Debes subir un archivo Excel o proporcionar un enlace de Drive")


def crear_job(company_id: int, user_id: int, config: dict, excel_path: str) -> str:
    """Crea la entrada del job, lanza el hilo con Playwright y devuelve el job_id."""
    from src.modules.sunat.infrastructure.automation import automatizar

    job_id = str(uuid.uuid4())
    log_q: queue.Queue = queue.Queue()
    prog_q: queue.Queue = queue.Queue()
    cancelar = threading.Event()

    jobs[job_id] = {
        "log_q": log_q,
        "prog_q": prog_q,
        "log_buf": [],
        "cancelar": cancelar,
        "created_at": time.monotonic(),
        "status": "running",
        "user_id": user_id,
        "company_id": company_id,
    }

    job_dir = os.path.join(DESCARGAS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    full_config = {
        **config,
        "excel": excel_path,
        "descargas": job_dir,
        "_cancelar": cancelar,
        "_persist_drive_token": _make_persist_drive_token(company_id),
    }

    def run() -> None:
        try:
            resultados = automatizar(full_config, log_q, prog_q)
            _guardar_resultado_job(job_id, company_id, user_id, resultados or [])
        except Exception as exc:
            log_q.put(f"[ x ] El proceso terminó inesperadamente: {str(exc)[:120]}")
            _log.error("Job %s falló inesperadamente", job_id, exc_info=True)
        finally:
            jobs[job_id]["status"] = "done"
            if os.path.exists(excel_path):
                os.unlink(excel_path)
            shutil.rmtree(job_dir, ignore_errors=True)

    threading.Thread(target=run, daemon=True).start()
    return job_id
