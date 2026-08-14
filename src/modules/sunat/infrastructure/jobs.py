"""
Helpers de infraestructura de los jobs de descarga SUNAT.

Resuelve el Excel de entrada (subida / caché de preview / Drive), persiste el
resultado del job y refresca el token de Drive. La ejecución de los jobs vive en
la cola sobre Postgres (`job_queue.py`) que consume un worker aparte.

La automatización pesada (Playwright/Google) se importa de forma perezosa.
"""
import json
import logging
import os
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


def _limpiar_previews_viejos() -> None:
    while True:
        time.sleep(30 * 60)
        ahora = time.monotonic()
        for pid, (ruta, ts) in list(_preview_files.items()):
            if ahora - ts > _PREVIEW_TTL:
                _preview_files.pop(pid, None)
                try:
                    os.unlink(ruta)
                except OSError:
                    pass


threading.Thread(target=_limpiar_previews_viejos, daemon=True).start()


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
