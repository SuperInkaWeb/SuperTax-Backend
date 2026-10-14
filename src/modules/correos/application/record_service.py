"""
Casos de uso de consulta y revisión humana de registros extraídos.

Cierra el ciclo de la extracción: lo que el pipeline no pudo leer con certeza lo
corrige una persona, y queda constancia de quién lo hizo. La corrección pasa por
los MISMOS objetos de valor que la extracción automática: una persona también
teclea mal un RUC, y aceptarlo sin verificar el dígito verificador dejaría entrar
justo el error que el pipeline existe para evitar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from src.modules.correos.domain import value_objects as vo
from src.modules.correos.infrastructure.models import ExtractedRecordModel, ReviewStatus
from src.modules.correos.infrastructure.repositories import SqlRecordRepository
from src.platform.database.base import utcnow

_LONGITUD_MAXIMA_TEXTO = 120

# Campos de texto libre corregibles (solo se recorta la longitud).
_CORREGIBLES_TEXTO = frozenset(
    {"nombre_contribuyente", "nombre_inquilino", "tipo_doc_inquilino", "tipo_de_bien"}
)
# Campos corregibles tipados y el objeto de valor que valida cada uno. Lo que no
# está aquí ni en el conjunto de texto no se puede corregir (perfil, confianza, etc.).
_CORREGIBLES_VO = {
    "ruc_contribuyente": vo.Ruc.interpretar,
    "ruc_inquilino": vo.Ruc.interpretar,
    "periodo": vo.PeriodoTributario.interpretar,
    "fecha_de_pago": vo.FechaDePago.interpretar,
    "numero_de_operacion": vo.NumeroDeOperacion.interpretar,
    "monto_alquiler": vo.Importe.interpretar,
    "tributo_resultante": vo.Importe.interpretar,
    "importe_pagado": vo.Importe.interpretar,
    "intereses_moratorios": vo.Importe.interpretar,
}


class CorreccionInvalida(ValueError):
    """Una corrección no se puede aplicar (campo no corregible o valor inválido)."""

    def __init__(self, message: str, campo: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.campo = campo


@dataclass(frozen=True, slots=True)
class FiltrosRegistro:
    """Filtros del listado de registros, todos opcionales y combinables."""

    job_id: int | None = None
    ruc: str | None = None
    ruc_inquilino: str | None = None
    periodo: str | None = None
    periodo_desde: str | None = None
    periodo_hasta: str | None = None
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    solo_aprobados: bool = False
    solo_pendientes: bool = False


def listar(
    db: Session, company_id: int, filtros: FiltrosRegistro, *, limit: int, offset: int,
    ids: list[int] | None = None,
) -> tuple[list[ExtractedRecordModel], int, dict[int, dict]]:
    """Registros + total + procedencia por `attachment_id` (para enriquecer la vista)."""
    repo = SqlRecordRepository(db)
    items, total = repo.listar(
        company_id, limit=limit, offset=offset,
        job_id=filtros.job_id, ruc=filtros.ruc, ruc_inquilino=filtros.ruc_inquilino,
        periodo=filtros.periodo, periodo_desde=filtros.periodo_desde,
        periodo_hasta=filtros.periodo_hasta, fecha_desde=filtros.fecha_desde,
        fecha_hasta=filtros.fecha_hasta, solo_aprobados=filtros.solo_aprobados,
        solo_pendientes=filtros.solo_pendientes, ids=ids,
    )
    origenes = repo.origenes(company_id, [r.attachment_id for r in items])
    return items, total, origenes


def obtener(
    db: Session, company_id: int, record_id: int
) -> tuple[ExtractedRecordModel, dict] | None:
    """Un registro con su procedencia, o None si no existe en la empresa."""
    repo = SqlRecordRepository(db)
    registro = repo.get(record_id, company_id)
    if registro is None:
        return None
    origen = repo.origenes(company_id, [registro.attachment_id]).get(registro.attachment_id, {})
    return registro, origen


def contar_pendientes(db: Session, company_id: int) -> int:
    return SqlRecordRepository(db).contar_pendientes(company_id)


def eliminar(db: Session, company_id: int, record_id: int) -> bool:
    return SqlRecordRepository(db).delete(record_id, company_id)


def aprobar(
    db: Session, company_id: int, user_id: int, record_id: int
) -> ExtractedRecordModel | None:
    """El revisor confirma que lo extraído es correcto."""
    repo = SqlRecordRepository(db)
    registro = repo.get(record_id, company_id)
    if registro is None:
        return None
    if registro.estado_de_revision != ReviewStatus.not_required.value:
        _marcar_revisado(registro, user_id, ReviewStatus.approved.value)
        repo.commit()
    return registro


def rechazar(
    db: Session, company_id: int, user_id: int, record_id: int
) -> ExtractedRecordModel | None:
    """Marca el registro como no utilizable (documento ilegible o ajeno)."""
    repo = SqlRecordRepository(db)
    registro = repo.get(record_id, company_id)
    if registro is None:
        return None
    _marcar_revisado(registro, user_id, ReviewStatus.rejected.value)
    repo.commit()
    return registro


def corregir_y_aprobar(
    db: Session, company_id: int, user_id: int, record_id: int, correcciones: dict[str, str]
) -> ExtractedRecordModel | None:
    """Aplica los valores corregidos por una persona y aprueba el registro. Levanta
    `CorreccionInvalida` si un campo no es corregible o su valor no es interpretable."""
    repo = SqlRecordRepository(db)
    registro = repo.get(record_id, company_id)
    if registro is None:
        return None

    valores = _interpretar(correcciones)
    # JSON: se reasignan dicts nuevos para que SQLAlchemy detecte el cambio.
    crudos = dict(registro.campos_crudos or {})
    confianzas = dict(registro.confianza_por_campo or {})
    for campo, valor in valores.items():
        setattr(registro, campo, valor)
        crudos[campo] = str(valor)
        confianzas[campo] = 1.0  # lo validó un humano mirando el documento
    registro.campos_crudos = crudos
    registro.confianza_por_campo = confianzas

    _marcar_revisado(registro, user_id, ReviewStatus.approved.value)
    repo.commit()
    return registro


def _marcar_revisado(registro: ExtractedRecordModel, user_id: int, estado: str) -> None:
    registro.estado_de_revision = estado
    registro.revisado_por_id = user_id
    registro.revisado_en = utcnow()


def _interpretar(correcciones: dict[str, str]) -> dict[str, str | Decimal | date]:
    """Convierte texto en valores de columna, rechazando lo no corregible o inválido."""
    resultado: dict[str, str | Decimal | date] = {}
    for nombre, crudo in correcciones.items():
        if nombre in _CORREGIBLES_TEXTO:
            resultado[nombre] = (crudo or "").strip()[:_LONGITUD_MAXIMA_TEXTO]
            continue
        interpretar = _CORREGIBLES_VO.get(nombre)
        if interpretar is None:
            raise CorreccionInvalida(f"El campo '{nombre}' no se puede corregir.", nombre)
        valor = interpretar(crudo)
        if valor is None:
            raise CorreccionInvalida(
                f"El valor introducido para '{nombre}' no es válido.", nombre
            )
        resultado[nombre] = _a_columna(valor)
    if not resultado:
        raise CorreccionInvalida("No se indicó ninguna corrección.")
    return resultado


def _a_columna(valor: object) -> str | Decimal | date:
    """Lleva el objeto de valor al tipo de la columna (texto RUC/periodo/nº, Decimal, date)."""
    if isinstance(valor, vo.Importe):
        return valor.cantidad
    if isinstance(valor, vo.FechaDePago):
        return valor.valor
    return str(valor)  # Ruc, PeriodoTributario (YYYYMM), NumeroDeOperacion
