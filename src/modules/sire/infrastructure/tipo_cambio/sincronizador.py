"""
Poblado de la tabla de TC.

La tabla se mantiene por tres vías que convergen en la MISMA forma SUNAT:
  - Import de Excel (`importar_excel`) y semilla histórica: ya es forma SUNAT.
  - Captura diaria del txt de SUNAT (`capturar_tc_hoy`): ya es forma SUNAT.
  - BCRP (`sincronizar_rango`): se le aplica el corrimiento +1 día hábil. Hoy el
    BCRP bloquea la IP del servidor, así que esta vía se usa SOLO desde la semilla
    (una máquina no bloqueada), no en la conciliación.

La conciliación (`preparar_para_periodo`) LEE la tabla; no llama a ninguna fuente
externa (así un día faltante o un 403 no tumban la hoja de Diferencias TC).
"""
import calendar
from datetime import date, timedelta

from sqlalchemy.orm import Session

from src.modules.sire.infrastructure.tipo_cambio import (
    bcrp,
    excel_parser,
    repositorio,
    sunat_txt,
)
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
    """TC del periodo (y meses atrás por NC/ND) LEÍDO de la tabla, listo para el
    subproceso: {fecha ISO 'AAAA-MM-DD': venta como str}. No consulta fuentes
    externas: la tabla la mantienen el import de Excel, la semilla y el txt diario."""
    anio, mes = int(periodo[:4]), int(periodo[4:6])
    desde = _restar_meses(anio, mes, _MESES_ATRAS_PERIODO)
    hasta = date(anio, mes, calendar.monthrange(anio, mes)[1])
    dias = {desde + timedelta(days=i) for i in range((hasta - desde).days + 1)}
    filas = repositorio.obtener_por_fechas(db, dias)
    return {fecha.isoformat(): str(fila.venta) for fecha, fila in filas.items()}


def importar_excel(db: Session, contenido: bytes) -> int:
    """Carga el Excel de TC (forma SUNAT) y hace upsert por fecha. Sirve para la
    semilla histórica y el mantenimiento manual."""
    cotizaciones = excel_parser.parse_tc_excel(contenido)
    return repositorio.upsert_tipos_cambio(db, cotizaciones, fuente="excel")


def capturar_tc_hoy(db: Session) -> int:
    """Baja el TC del día desde el txt de SUNAT y lo guarda (captura diaria)."""
    cotizaciones = sunat_txt.obtener_tc_hoy()
    por_fecha = {c.fecha: c for c in cotizaciones}
    return repositorio.upsert_tipos_cambio(db, por_fecha, fuente="sunat")
