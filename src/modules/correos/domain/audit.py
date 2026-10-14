"""
Catálogo de acciones auditables del módulo Correos (dominio puro).

Una enumeración y no texto libre: así las consultas de auditoría son fiables y
nadie inventa un nombre de acción que luego no aparece en los informes (OWASP A09:
constancia inmutable de quién hizo qué y cuándo).
"""
from __future__ import annotations

from enum import StrEnum


class AuditAction(StrEnum):
    BUZON_VINCULADO = "mailbox.linked"
    BUZON_DESVINCULADO = "mailbox.unlinked"
    ESCANEO_INICIADO = "scan.started"
    ESCANEO_CANCELADO = "scan.cancelled"
    REPORTE_EXPORTADO = "report.exported"
    REGISTRO_CORREGIDO = "record.corrected"
    REGISTRO_APROBADO = "record.approved"
    REGISTRO_RECHAZADO = "record.rejected"
    REGISTRO_ELIMINADO = "record.deleted"
    ADJUNTO_DESCARGADO = "attachment.downloaded"
    DATOS_REINICIADOS = "data.reset"
