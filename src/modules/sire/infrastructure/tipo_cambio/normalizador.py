"""
Normaliza las cotizaciones SBS (por día hábil) a la forma que publica SUNAT.

Regla verificada contra el histórico de SUNAT (`tcS01Alias`): el TC que SUNAT
muestra para un día calendario D es la cotización SBS del ÚLTIMO día hábil
ANTERIOR a D, con arrastre en findes y feriados. Dicho de otro modo: la cotización
con fecha Q aparece como TC de SUNAT desde Q+1 hasta el día previo a la siguiente
cotización. Esta función reproduce esa forma: una entrada por cada día calendario.
"""
from datetime import date, timedelta

from src.modules.sire.infrastructure.tipo_cambio.bcrp import CotizacionSbs


def normalizar_a_forma_sunat(
    cotizaciones: list[CotizacionSbs], desde: date, hasta: date
) -> dict[date, CotizacionSbs]:
    """Devuelve {día_calendario: cotización vigente} para [desde, hasta].

    Vigente para D = la cotización SBS de la mayor fecha hábil estrictamente menor
    que D (desfase +1 día hábil + arrastre). Los días sin cotización previa quedan
    fuera (p. ej. anteriores a la primera cotización disponible)."""
    ordenadas = sorted(cotizaciones, key=lambda c: c.fecha)
    resultado: dict[date, CotizacionSbs] = {}
    indice = 0
    vigente: CotizacionSbs | None = None
    dia = desde
    while dia <= hasta:
        # La vigente es la última cotización con fecha estrictamente anterior al día.
        while indice < len(ordenadas) and ordenadas[indice].fecha < dia:
            vigente = ordenadas[indice]
            indice += 1
        if vigente is not None:
            resultado[dia] = CotizacionSbs(
                fecha=dia, compra=vigente.compra, venta=vigente.venta
            )
        dia += timedelta(days=1)
    return resultado
