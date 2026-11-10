"""
Orquesta el poblado de la tabla de TC desde el BCRP.

`sincronizar_rango` baja y hace upsert de un rango; `asegurar_cobertura` baja solo
lo que falte para un conjunto de fechas (pensado para llamarse antes de conciliar
un periodo, de modo que el TC se mantenga al día on-demand).
"""
import calendar
from datetime import date, timedelta

from sqlalchemy.orm import Session

from src.modules.sire.infrastructure.tipo_cambio import bcrp, repositorio
from src.modules.sire.infrastructure.tipo_cambio.normalizador import (
    normalizar_a_forma_sunat,
)

# Arranque hacia atrás: asegura al menos una cotización hábil previa al primer día
# (necesaria para el desfase +1 día hábil), incluso cruzando feriados largos.
_DIAS_LEAD_IN = 10

# Meses hacia atrás a cubrir para un periodo: las NC/ND pueden referirse a
# comprobantes de hasta más de un año antes (su TC es el de la fecha del original).
_MESES_ATRAS_PERIODO = 24


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


def _restar_meses(anio: int, mes: int, n: int) -> date:
    """Primer día del mes resultante de restar `n` meses a (anio, mes)."""
    total = anio * 12 + (mes - 1) - n
    return date(total // 12, total % 12 + 1, 1)


def preparar_para_periodo(db: Session, periodo: str) -> dict[str, str]:
    """Asegura el TC del periodo (y meses atrás por NC/ND) y lo devuelve listo para
    el subproceso de conciliación: {fecha ISO 'AAAA-MM-DD': venta como str}."""
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    desde = _restar_meses(anio, mes, _MESES_ATRAS_PERIODO)
    hasta = date(anio, mes, calendar.monthrange(anio, mes)[1])
    dias = {desde + timedelta(days=i) for i in range((hasta - desde).days + 1)}
    asegurar_cobertura(db, dias)
    filas = repositorio.obtener_por_fechas(db, dias)
    return {fecha.isoformat(): str(fila.venta) for fecha, fila in filas.items()}
