"""
Generación del reporte Excel de registros extraídos.

Síncrono y por streaming (como el reporte del módulo SUNAT): a la escala real —
constancias de arrendamiento por periodo, no millones de filas— generar al vuelo
es simple y suficiente. Un tope de filas acota el costo de una petición
inesperadamente grande. Reutiliza los MISMOS filtros que el listado de registros,
para que "lo que veo" y "lo que exporto" coincidan.
"""
from __future__ import annotations

from src.modules.correos.application import record_service
from src.modules.correos.application.record_service import FiltrosRegistro
from src.modules.correos.infrastructure.models import (
    Completeness,
    ExtractedRecordModel,
    ReviewStatus,
)
from src.modules.correos.infrastructure.report_excel import FilaReporte, generar_reporte
from src.platform.config.settings import settings

# Etiquetas legibles para el reporte (los valores en BD son códigos en inglés).
_COMPLETITUD = {
    Completeness.complete.value: "Completo",
    Completeness.partial.value: "Parcial",
    Completeness.empty.value: "Vacío",
}
_REVISION = {
    ReviewStatus.not_required.value: "No requiere",
    ReviewStatus.pending.value: "Pendiente",
    ReviewStatus.approved.value: "Aprobado",
    ReviewStatus.rejected.value: "Rechazado",
}


def generar_excel(db, company_id: int, filtros: FiltrosRegistro) -> tuple[bytes, int, int]:
    """Devuelve (xlsx, filas_exportadas, total_que_cumple_el_filtro). Si el total supera
    el tope, se exportan las primeras `CORREOS_MAX_EXPORT_ROWS` (las más recientes)."""
    limite = settings.CORREOS_MAX_EXPORT_ROWS
    items, total, origenes = record_service.listar(
        db, company_id, filtros, limit=limite, offset=0
    )
    filas = [_a_fila(r, origenes.get(r.attachment_id, {})) for r in items]
    return generar_reporte(filas), len(filas), total


def _a_fila(m: ExtractedRecordModel, origen: dict) -> FilaReporte:
    return FilaReporte(
        ruc_contribuyente=m.ruc_contribuyente or "",
        nombre_contribuyente=m.nombre_contribuyente or "",
        tipo_doc_inquilino=m.tipo_doc_inquilino or "",
        ruc_inquilino=m.ruc_inquilino or "",
        nombre_inquilino=m.nombre_inquilino or "",
        tipo_de_bien=m.tipo_de_bien or "",
        periodo=m.periodo or "",
        monto_alquiler=_importe(m.monto_alquiler),
        tributo_resultante=_importe(m.tributo_resultante),
        importe_pagado=_importe(m.importe_pagado),
        intereses_moratorios=_importe(m.intereses_moratorios),
        moneda=m.moneda or "",
        fecha_de_pago=m.fecha_de_pago.strftime("%d/%m/%Y") if m.fecha_de_pago else "",
        numero_de_operacion=m.numero_de_operacion or "",
        estado=_COMPLETITUD.get(m.completitud, m.completitud or ""),
        revision=_REVISION.get(m.estado_de_revision, m.estado_de_revision or ""),
        archivo_origen=origen.get("nombre_adjunto", ""),
    )


def _importe(valor) -> str:  # noqa: ANN001 - Decimal | None del modelo
    return f"{valor:.2f}" if valor is not None else ""
