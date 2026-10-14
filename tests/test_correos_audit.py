"""Auditoría (Fase 7): catálogo, registro best-effort, serialización, permiso y ruta."""
from datetime import UTC, datetime

from src.modules.correos.api.schemas import AuditEntry
from src.modules.correos.application import audit_service
from src.modules.correos.domain.audit import AuditAction
from src.modules.correos.infrastructure.models import AuditLogModel


def test_catalogo_de_acciones():
    assert AuditAction.REGISTRO_APROBADO.value == "record.approved"
    assert AuditAction.REPORTE_EXPORTADO.value == "report.exported"
    assert AuditAction.ADJUNTO_DESCARGADO.value == "attachment.downloaded"


class _DBCaida:
    """Sesión falsa cuyo `add` falla, para comprobar que la auditoría no propaga."""

    def __init__(self) -> None:
        self.rolled_back = False

    def add(self, _modelo) -> None:
        raise RuntimeError("base de datos caída")

    def rollback(self) -> None:
        self.rolled_back = True


def test_registrar_es_best_effort():
    db = _DBCaida()
    # No debe lanzar aunque la BD falle: la acción principal ya ocurrió.
    audit_service.registrar(
        db, company_id=1, actor_id=1, action=AuditAction.REGISTRO_APROBADO,
        resource_type="extracted_record", resource_id=7,
    )
    assert db.rolled_back


def test_audit_entry_serializa():
    modelo = AuditLogModel(
        id=5, company_id=1, actor_id=3, actor_ip="10.0.0.1", action="record.corrected",
        resource_type="extracted_record", resource_id="7",
        audit_metadata={"campos": ["importe_pagado"]}, occurred_at=datetime.now(UTC),
    )
    salida = AuditEntry.from_model(modelo)
    assert salida.action == "record.corrected"
    assert salida.resource_id == "7"
    assert salida.metadata == {"campos": ["importe_pagado"]}


def test_permiso_audit_view_solo_admin_y_supervisor():
    from src.seed import _rol_recibe

    assert _rol_recibe("admin_empresa", "correos.audit.view")
    assert _rol_recibe("supervisor", "correos.audit.view")
    assert not _rol_recibe("operador", "correos.audit.view")
    assert not _rol_recibe("consulta", "correos.audit.view")


def test_ruta_audit_montada():
    from src.main import create_app

    assert "/api/correos/audit" in create_app().openapi()["paths"]
