"""
Ejecución de un escaneo de buzón (runner on-demand, corre en un hilo del web).

Flujo: reclamar el job → token válido → listar correos con adjuntos → por cada
correo: listar adjuntos → descargar (con tope) → validar → deduplicar (sha256) →
guardar en R2 → persistir correo+adjunto → actualizar contadores y progreso. Es
I/O-bound (API del buzón + R2); el OCR/IA pesado llega en la Fase 4 (subproceso).

Idempotente y tolerante: cada fallo de un correo/adjunto se registra y el escaneo
continúa; un cancel durante el proceso lo detiene dejando el job en 'cancelled'.
"""
import logging

from src.modules.correos.application import extraction_service, mailbox_oauth, token_service
from src.modules.correos.infrastructure import mail_client, validator
from src.modules.correos.infrastructure.models import ScanPhase
from src.modules.correos.infrastructure.repositories import (
    SqlAttachmentRepository,
    SqlErrorRepository,
    SqlMailboxRepository,
    SqlMessageRepository,
    SqlScanRepository,
)
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.storage import get_storage

logger = logging.getLogger("correos.scan")

_EXT = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_CONTADORES_CERO = {
    "mensajes_revisados": 0, "mensajes_con_adjuntos": 0, "adjuntos_descargados": 0,
    "adjuntos_rechazados": 0, "adjuntos_duplicados": 0, "errores": 0,
    # Fase de extracción (OCR/IA):
    "registros_extraidos": 0, "registros_en_revision": 0, "adjuntos_sin_datos": 0,
    "llamadas_ia": 0, "errores_extraccion": 0,
}


def _key(company_id: int, job_id: int, sha256: str, ext: str) -> str:
    return f"correos/{company_id}/{job_id}/{sha256}{ext}"


def ejecutar_escaneo(job_id: int) -> None:
    """Target del pool on-demand: procesa el escaneo de principio a fin."""
    db = SessionLocal()
    meta = SqlScanRepository(db).claim(job_id)
    if meta is None:
        db.close()
        return
    company_id, connection_id, params = meta
    try:
        _procesar(db, job_id, company_id, connection_id, params)
    except mailbox_oauth.OAuthError as exc:
        SqlScanRepository(db).set_failed(job_id, "auth", exc.message)
    except mail_client.MailError as exc:
        SqlScanRepository(db).set_failed(job_id, "provider", exc.message)
    except Exception as exc:  # cualquier otro: FAILED con mensaje corto
        logger.exception("Escaneo %s falló", job_id)
        SqlScanRepository(db).set_failed(job_id, "error", str(exc))
    finally:
        db.close()


def _procesar(db, job_id: int, company_id: int, connection_id: int, params: dict) -> None:
    scan_repo = SqlScanRepository(db)
    conn = SqlMailboxRepository(db).get(connection_id, company_id)
    if conn is None:
        scan_repo.set_failed(job_id, "no_connection", "La conexión de buzón ya no existe.")
        return

    token = token_service.access_token_valido(db, conn)
    client = mail_client.cliente_de(conn.provider)
    limite = int(params.get("limite_de_mensajes", 100))
    carpeta = str(params.get("carpeta", "INBOX"))
    desde = _fecha(params.get("desde"))
    hasta = _fecha(params.get("hasta"))
    tope_bytes = settings.CORREOS_MAX_ATTACHMENT_MB * 1024 * 1024
    contadores = dict(_CONTADORES_CERO)
    storage = get_storage()
    msg_repo = SqlMessageRepository(db)
    att_repo = SqlAttachmentRepository(db)
    err_repo = SqlErrorRepository(db)

    mensajes = client.listar_mensajes(
        token, desde=desde, hasta=hasta, limite=limite, carpeta=carpeta
    )
    scan_repo.update_progress(job_id, phase=ScanPhase.downloading.value, progress=10,
                              counters=contadores)

    for resumen in mensajes:
        if scan_repo.cancel_requested(job_id):
            return  # se deja en 'cancelled'
        contadores["mensajes_revisados"] += 1
        try:
            refs = client.listar_adjuntos(token, resumen.provider_id)
        except mail_client.MailError as exc:
            contadores["errores"] += 1
            err_repo.add(company_id=company_id, job_id=job_id, stage="listing",
                         code="listar_adjuntos", message=exc.message, retryable=exc.retryable)
            refs = []
        if refs:
            contadores["mensajes_con_adjuntos"] += 1
        mensaje_model = None
        for ref in refs:
            try:
                data = client.descargar_adjunto(
                    token, message_id=resumen.provider_id,
                    attachment_id=ref.attachment_id, limite_bytes=tope_bytes,
                )
            except mail_client.MailError as exc:
                contadores["errores"] += 1
                err_repo.add(company_id=company_id, job_id=job_id, stage="downloading",
                             code="descargar", message=exc.message, retryable=exc.retryable)
                continue
            res = validator.validar(data, nombre=ref.name, tamano_maximo=tope_bytes)
            if not res.ok:
                contadores["adjuntos_rechazados"] += 1
                err_repo.add(company_id=company_id, job_id=job_id, stage="downloading",
                             code=res.motivo or "rechazado", message=f"{ref.name}: {res.motivo}")
                continue
            if att_repo.existe(company_id, res.sha256):
                contadores["adjuntos_duplicados"] += 1
                continue
            if mensaje_model is None:
                mensaje_model = msg_repo.upsert(
                    company_id=company_id, job_id=job_id, provider=conn.provider,
                    provider_id=resumen.provider_id, sender=resumen.sender,
                    subject=resumen.subject, received_at=resumen.received_at,
                )
            key = _key(company_id, job_id, res.sha256, _EXT.get(res.mime, ""))
            storage.save(key, data)
            att_repo.create(
                company_id=company_id, message_id=mensaje_model.id, original_name=ref.name,
                storage_key=key, mime_type=res.mime, size_bytes=len(data), sha256=res.sha256,
            )
            contadores["adjuntos_descargados"] += 1

        progreso = min(80, int((contadores["mensajes_revisados"] / max(1, limite)) * 70) + 10)
        scan_repo.update_progress(job_id, phase=ScanPhase.downloading.value,
                                  progress=progreso, counters=contadores)

    if scan_repo.cancel_requested(job_id):
        return
    # Fase de extracción: OCR/IA sobre los adjuntos descargados (80% → 99%).
    extraction_service.extraer_adjuntos(db, job_id, company_id, contadores, scan_repo)
    if scan_repo.cancel_requested(job_id):
        return
    scan_repo.set_completed(job_id, contadores)
    logger.info("Escaneo %s OK: %s", job_id, contadores)


def _fecha(valor):
    from datetime import date
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        return None
