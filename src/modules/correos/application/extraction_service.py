"""
Fase de extracción de un escaneo (corre en el hilo del web, tras la descarga).

Para cada adjunto del escaneo sin registro: lee el archivo de R2, lanza el OCR/IA en
un subproceso efímero y persiste el registro tributario. Idempotente y tolerante:
cada fallo de un adjunto se registra y la extracción continúa; un cancel la detiene.

El presupuesto de IA se controla aquí (en el proceso web, que ve todos los adjuntos
del escaneo): cuando se agota, los adjuntos restantes degradan a los motores
gratuitos en vez de fallar. El subproceso solo recibe bytes + configuración y
devuelve datos; nunca toca la BD ni R2.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.extraction import subproceso
from src.modules.correos.infrastructure.models import ReviewStatus, ScanPhase
from src.modules.correos.infrastructure.repositories import (
    SqlAttachmentRepository,
    SqlErrorRepository,
    SqlRecordRepository,
    SqlScanRepository,
)
from src.platform.config.settings import settings
from src.platform.storage import get_storage

logger = logging.getLogger("correos.extraction")

# Columnas tipadas Decimal del registro (se convierten desde el texto del subproceso).
_CAMPOS_IMPORTE = (
    "monto_alquiler", "tributo_resultante", "importe_pagado", "intereses_moratorios",
)


def extraer_adjuntos(
    db: Session, job_id: int, company_id: int, contadores: dict, scan_repo: SqlScanRepository
) -> None:
    """Procesa los adjuntos pendientes del escaneo. Actualiza `contadores` in situ y
    el progreso (fase `extracting`, de 80% a 99%)."""
    att_repo = SqlAttachmentRepository(db)
    rec_repo = SqlRecordRepository(db)
    err_repo = SqlErrorRepository(db)
    storage = get_storage()

    pendientes = att_repo.pendientes_de_extraccion(company_id, job_id)
    if not pendientes:
        return

    ia_habilitada = settings.CORREOS_IA_HABILITADA and bool(os.getenv("GROQ_API_KEY"))
    tope_ia = settings.CORREOS_MAX_IA_POR_ESCANEO
    ia_usadas = 0
    total = len(pendientes)
    scan_repo.update_progress(job_id, phase=ScanPhase.extracting.value, progress=80,
                              counters=contadores)

    for indice, adjunto in enumerate(pendientes, start=1):
        if scan_repo.cancel_requested(job_id):
            return  # se deja en 'cancelled'
        try:
            contenido = storage.read(adjunto.storage_key)
        except Exception as exc:  # noqa: BLE001 - R2/FS como frontera
            contadores["errores_extraccion"] = contadores.get("errores_extraccion", 0) + 1
            err_repo.add(company_id=company_id, job_id=job_id, stage="extracting",
                         code="leer_r2", message=f"{adjunto.original_name}: {type(exc).__name__}")
            continue

        payload = {
            "contenido": contenido,
            "mime": adjunto.mime_type,
            "nombre": adjunto.original_name,
            "permitir_ia": ia_habilitada and ia_usadas < tope_ia,
            "tesseract_cmd": settings.SCANNER_TESSERACT_CMD,
            "groq_api_key": os.getenv("GROQ_API_KEY"),
            "groq_model": os.getenv("GROQ_MODEL"),
        }
        try:
            datos = subproceso.ejecutar(payload)
        except Exception as exc:  # noqa: BLE001 - el hijo falló o murió (OOM)
            logger.exception("Extracción del adjunto %s falló", adjunto.id)
            contadores["errores_extraccion"] = contadores.get("errores_extraccion", 0) + 1
            err_repo.add(company_id=company_id, job_id=job_id, stage="extracting",
                         code="extraer", message=f"{adjunto.original_name}: {type(exc).__name__}")
            continue

        if datos.get("uso_ia"):
            ia_usadas += 1
            contadores["llamadas_ia"] = contadores.get("llamadas_ia", 0) + 1

        if not datos.get("reconocido"):
            # No es una constancia o es ilegible: no se crea registro (evita llenar la
            # tabla de ruido). Queda contado para la vista del escaneo.
            contadores["adjuntos_sin_datos"] = contadores.get("adjuntos_sin_datos", 0) + 1
        else:
            rec_repo.create(_a_valores(company_id, job_id, adjunto.id, datos))
            contadores["registros_extraidos"] = contadores.get("registros_extraidos", 0) + 1
            if datos.get("estado_de_revision") == ReviewStatus.pending.value:
                contadores["registros_en_revision"] = (
                    contadores.get("registros_en_revision", 0) + 1
                )

        progreso = min(99, 80 + int((indice / total) * 19))
        scan_repo.update_progress(job_id, phase=ScanPhase.extracting.value,
                                  progress=progreso, counters=contadores)


def _a_valores(company_id: int, job_id: int, attachment_id: int, datos: dict) -> dict:
    """Convierte el resultado serializable del subproceso en valores de columna
    (texto → Decimal/date para las columnas tipadas)."""
    valores = {
        "company_id": company_id,
        "job_id": job_id,
        "attachment_id": attachment_id,
        "profile": datos.get("perfil", ""),
        "ruc_contribuyente": datos.get("ruc_contribuyente"),
        "nombre_contribuyente": datos.get("nombre_contribuyente", ""),
        "ruc_inquilino": datos.get("ruc_inquilino"),
        "nombre_inquilino": datos.get("nombre_inquilino", ""),
        "tipo_doc_inquilino": datos.get("tipo_doc_inquilino", ""),
        "tipo_de_bien": datos.get("tipo_de_bien", ""),
        "periodo": datos.get("periodo"),
        "fecha_de_pago": _fecha(datos.get("fecha_de_pago")),
        "numero_de_operacion": datos.get("numero_de_operacion"),
        "moneda": datos.get("moneda", "PEN"),
        "campos_crudos": datos.get("campos_crudos", {}),
        "confianza_por_campo": datos.get("confianza_por_campo", {}),
        "completitud": datos.get("completitud", "empty"),
        "estado_de_revision": datos.get("estado_de_revision", "not_required"),
        "estrategia_usada": datos.get("estrategia_usada"),
        "duracion_ms": int(datos.get("duracion_ms", 0)),
    }
    for campo in _CAMPOS_IMPORTE:
        valores[campo] = _decimal(datos.get(campo))
    return valores


def _decimal(crudo: str | None) -> Decimal | None:
    if not crudo:
        return None
    try:
        return Decimal(crudo)
    except InvalidOperation:
        return None


def _fecha(crudo: str | None) -> date | None:
    if not crudo:
        return None
    try:
        return date.fromisoformat(crudo)
    except ValueError:
        return None
