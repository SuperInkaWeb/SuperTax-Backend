"""
Cola de descargas SUNAT sobre Postgres (reemplaza el runner en memoria).

- `encolar_job`: cifra la config, guarda el Excel en storage y crea el job.
- `procesar_job`: lo ejecuta el worker; corre el motor Playwright verbatim
  alimentándolo con *shims* que escriben logs/progreso en Postgres y consultan
  la señal de cancelar en Postgres, así el motor NO se modifica.
"""
import json
import logging
import os
import shutil
import time
import uuid

from sqlalchemy import select

from src.modules.sunat.infrastructure.comprobante_xml import enriquecer_resultados
from src.modules.sunat.infrastructure.jobs import (
    DESCARGAS_DIR,
    _guardar_resultado_job,
    _make_persist_drive_token,
)
from src.modules.sunat.infrastructure.models import SunatJobLogModel, SunatJobModel, SunatJobStatus
from src.modules.sunat.infrastructure.repositories import SqlSunatJobRepository
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.security import decrypt_field, encrypt_field
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tasks import submit as submit_background

logger = logging.getLogger("sunat.job_queue")

_MAX_MSG = 2000
_POOL = "sunat"


class _PgLogWriter:
    """Escribe líneas de log/progreso del job en Postgres (una sesión dedicada)."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._db = SessionLocal()

    def write(self, kind: str, msg) -> None:
        self._db.add(
            SunatJobLogModel(job_id=self.job_id, kind=kind, message=str(msg)[:_MAX_MSG])
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


class _Sink:
    """Adapta el writer a la interfaz `.put()` que espera el motor (queue-like)."""

    def __init__(self, writer: _PgLogWriter, kind: str) -> None:
        self._writer = writer
        self._kind = kind

    def put(self, msg) -> None:
        self._writer.write(self._kind, msg)


class _PgCancelFlag:
    """Interfaz `.is_set()` que consulta la señal de cancelar en Postgres (cacheada)."""

    _INTERVALO = 2.0

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._last = 0.0
        self._val = False

    def is_set(self) -> bool:
        if self._val:
            return True
        ahora = time.monotonic()
        if ahora - self._last < self._INTERVALO:
            return self._val
        self._last = ahora
        db = SessionLocal()
        try:
            self._val = bool(
                db.scalar(
                    select(SunatJobModel.cancel_requested).where(
                        SunatJobModel.job_id == self.job_id
                    )
                )
            )
        finally:
            db.close()
        return self._val


def encolar_job(
    db,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    config: dict,
    excel_local_path: str,
) -> str:
    """Cifra la config, sube el Excel a storage y crea el job en la cola."""
    job_id = str(uuid.uuid4())
    with open(excel_local_path, "rb") as f:
        contenido = f.read()
    excel_path = f"sunat/uploads/{company_id}/{job_id}.xlsx"
    storage.save(excel_path, contenido)
    config_enc = encrypt_field(json.dumps(config))
    SqlSunatJobRepository(db).create(company_id, user_id, job_id, config_enc, excel_path)
    try:
        os.unlink(excel_local_path)
    except OSError:
        pass
    return job_id


def procesar_job(storage: FileStorage, job_id: str) -> None:
    """Ejecuta el job (worker): descarga el Excel, corre el motor y marca el estado."""
    db = SessionLocal()
    try:
        job = SqlSunatJobRepository(db).get(job_id)
        if job is None:
            return
        company_id = job.company_id
        user_id = job.created_by_id
        excel_path = job.excel_path
        config = json.loads(decrypt_field(job.config_enc))
    finally:
        db.close()

    job_dir = os.path.join(DESCARGAS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    excel_local = os.path.join(job_dir, "input.xlsx")
    with open(excel_local, "wb") as f:
        f.write(storage.read(excel_path))

    writer = _PgLogWriter(job_id)
    cancelar = _PgCancelFlag(job_id)
    full_config = {
        **config,
        "excel": excel_local,
        "descargas": job_dir,
        "_cancelar": cancelar,
        "_persist_drive_token": _make_persist_drive_token(user_id),
    }

    estado = SunatJobStatus.completado
    try:
        from src.modules.sunat.infrastructure.automation import automatizar

        resultados = automatizar(full_config, _Sink(writer, "log"), _Sink(writer, "progress")) or []
        # Enriquece con la descripción de cada comprobante (parseando sus XML)
        # mientras los temporales aún existen, antes de la limpieza del finally.
        resultados = enriquecer_resultados(resultados, job_dir)
        _guardar_resultado_job(job_id, company_id, user_id, resultados)
        if cancelar.is_set():
            estado = SunatJobStatus.cancelado
        elif not resultados:
            # El motor terminó sin procesar ningún comprobante: login, Excel o
            # navegador fallaron (en esos casos el motor devuelve []). Se refleja
            # como error real en vez de un "completado" engañoso con 0 resultados.
            writer.write("log", "[ x ] No se descargó ningún comprobante; el job queda en error.")
            estado = SunatJobStatus.error
        else:
            estado = SunatJobStatus.completado
    except Exception as exc:
        writer.write("log", f"[ x ] El proceso terminó inesperadamente: {str(exc)[:120]}")
        logger.error("Job SUNAT %s falló", job_id, exc_info=True)
        estado = SunatJobStatus.error
    finally:
        writer.close()
        shutil.rmtree(job_dir, ignore_errors=True)
        try:
            storage.delete(excel_path)
        except Exception:
            logger.warning("No se pudo borrar el excel del job %s", job_id)
        db_final = SessionLocal()
        try:
            SqlSunatJobRepository(db_final).set_status(job_id, estado)
        finally:
            db_final.close()


def _despachar(job_id: str) -> None:
    """Corre dentro del pool: reclama el job y lo procesa. Si otro proceso ya lo
    tomó (o ya no está en cola), no hace nada."""
    db = SessionLocal()
    try:
        reclamado = SqlSunatJobRepository(db).claim(job_id)
    finally:
        db.close()
    if reclamado:
        procesar_job(get_storage(), job_id)


def encolar_ejecucion(job_id: str) -> None:
    """Despacha el job al pool on-demand del proceso web (no hay worker que sondee).
    La concurrencia la limita `SUNAT_MAX_CONCURRENCY`."""
    submit_background(_POOL, settings.SUNAT_MAX_CONCURRENCY, _despachar, job_id)


def recuperar_pendientes() -> None:
    """Se ejecuta al arrancar el web (una sola instancia web asumida):
    - marca como `error` los jobs que quedaron en `procesando` (interrumpidos por
      un redeploy: nadie los retoma);
    - re-despacha los que quedaron `en_cola` (encolados pero no procesados, p.ej.
      si el web murió justo tras encolar)."""
    db = SessionLocal()
    try:
        repo = SqlSunatJobRepository(db)
        interrumpidos = repo.marcar_estado_masivo(
            SunatJobStatus.procesando, SunatJobStatus.error
        )
        pendientes = repo.ids_por_estado(SunatJobStatus.en_cola)
    finally:
        db.close()
    if interrumpidos:
        logger.warning("Marcados %s job(s) interrumpidos como error", interrumpidos)
    for job_id in pendientes:
        encolar_ejecucion(job_id)
    if pendientes:
        logger.info("Re-despachados %s job(s) que estaban en cola", len(pendientes))
