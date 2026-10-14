"""
Reinicio de los datos de escaneo de una empresa (para escanear desde cero).

Borra registros, adjuntos (incluido su archivo en R2), correos y escaneos de la
empresa activa; conserva las conexiones de buzón y la bitácora de auditoría. Es
destructivo e irreversible: la API exige una confirmación explícita y lo audita.

Aislado por `company_id` en cada borrado: un tenant nunca puede tocar los datos de
otro. El archivo en R2 se borra antes que la fila (best-effort): si fallara, la
fila igual se elimina y el objeto queda huérfano, que es preferible a abortar el
reinicio por un objeto que ya nadie referencia.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.models import (
    AttachmentModel,
    EmailMessageModel,
    ExtractedRecordModel,
    ScanJobModel,
)
from src.platform.storage import get_storage

logger = logging.getLogger("correos.reset")


def reiniciar(db: Session, company_id: int) -> dict[str, int]:
    """Borra los datos de escaneo de la empresa. Devuelve cuántas filas de cada tipo
    se eliminaron (para el aviso de la interfaz)."""
    registros = _contar(db, ExtractedRecordModel, company_id)
    mensajes = _contar(db, EmailMessageModel, company_id)
    claves = list(db.scalars(
        select(AttachmentModel.storage_key).where(AttachmentModel.company_id == company_id)
    ))

    _borrar_de_r2(claves)

    # Orden por dependencias (FK): registros → adjuntos → correos → escaneos.
    db.execute(delete(ExtractedRecordModel).where(ExtractedRecordModel.company_id == company_id))
    db.execute(delete(AttachmentModel).where(AttachmentModel.company_id == company_id))
    db.execute(delete(EmailMessageModel).where(EmailMessageModel.company_id == company_id))
    db.execute(delete(ScanJobModel).where(ScanJobModel.company_id == company_id))
    db.commit()

    resumen = {"registros": registros, "adjuntos": len(claves), "mensajes": mensajes}
    logger.info("Correos: datos reiniciados para empresa %s: %s", company_id, resumen)
    return resumen


def _contar(db: Session, modelo, company_id: int) -> int:
    total = db.scalar(
        select(func.count()).select_from(modelo).where(modelo.company_id == company_id)
    )
    return int(total or 0)


def _borrar_de_r2(claves: list[str]) -> None:
    storage = get_storage()
    for clave in claves:
        try:
            storage.delete(clave)
        except Exception:  # noqa: BLE001 - un objeto huérfano no debe abortar el reinicio
            logger.warning("No se pudo borrar de R2: %s", clave)
