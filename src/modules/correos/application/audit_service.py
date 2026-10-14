"""
Registro de acciones sensibles en la bitácora de auditoría (OWASP A09).

`registrar` es best-effort: deja constancia de la acción pero NUNCA la hace fallar.
La acción principal (aprobar, exportar, descargar…) ya ocurrió y se persistió; si
el apunte de auditoría fallara, se registra en el log de la aplicación y se sigue,
en lugar de devolver un error por algo que ya se hizo. `audit_metadata` guarda el
contexto imprescindible (qué campos se corrigieron, el formato del reporte…), nunca
el dato sensible en sí.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from src.modules.correos.domain.audit import AuditAction
from src.modules.correos.infrastructure.models import AuditLogModel
from src.modules.correos.infrastructure.repositories import SqlAuditRepository

logger = logging.getLogger("correos.audit")


def registrar(
    db: Session, *, company_id: int, actor_id: int | None, action: AuditAction,
    resource_type: str = "", resource_id: int | str | None = None,
    actor_ip: str | None = None, metadata: dict | None = None,
) -> None:
    """Deja constancia de una acción. No propaga errores: la acción ya ocurrió."""
    try:
        SqlAuditRepository(db).add(
            company_id=company_id, actor_id=actor_id, action=action.value,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            actor_ip=actor_ip, metadata=metadata,
        )
    except Exception:  # noqa: BLE001 - la auditoría nunca debe tumbar la acción
        logger.exception("No se pudo registrar la auditoría de %s", action.value)
        db.rollback()


def listar(
    db: Session, company_id: int, *, limit: int, offset: int
) -> tuple[list[AuditLogModel], int]:
    return SqlAuditRepository(db).listar(company_id, limit=limit, offset=offset)
