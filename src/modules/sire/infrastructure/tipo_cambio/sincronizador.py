"""
Orquesta el poblado de la tabla de TC desde el BCRP.

`sincronizar_rango` baja y hace upsert de un rango; `asegurar_cobertura` baja solo
lo que falte para un conjunto de fechas (pensado para llamarse antes de conciliar
un periodo, de modo que el TC se mantenga al día on-demand).
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from src.modules.sire.infrastructure.tipo_cambio import bcrp, repositorio
from src.modules.sire.infrastructure.tipo_cambio.normalizador import (
    normalizar_a_forma_sunat,
)

# Arranque hacia atrás: asegura al menos una cotización hábil previa al primer día
# (necesaria para el desfase +1 día hábil), incluso cruzando feriados largos.
_DIAS_LEAD_IN = 10


def sincronizar_rango(db: Session, desde: date, hasta: date) -> int:
    """Trae del BCRP y hace upsert del TC (forma SUNAT) para [desde, hasta]."""
    cotizaciones = bcrp.obtener_cotizaciones(desde - timedelta(days=_DIAS_LEAD_IN), hasta)
    normalizadas = normalizar_a_forma_sunat(cotizaciones, desde, hasta)
    return repositorio.upsert_tipos_cambio(db, normalizadas)


def asegurar_cobertura(db: Session, fechas: set[date]) -> int:
    """Garantiza TC para todas las `fechas`; baja del BCRP solo el rango faltante."""
    if not fechas:
        return 0
    presentes = repositorio.fechas_existentes(db, min(fechas), max(fechas))
    faltantes = fechas - presentes
    if not faltantes:
        return 0
    return sincronizar_rango(db, min(faltantes), max(faltantes))
