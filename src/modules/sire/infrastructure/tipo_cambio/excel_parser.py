"""
Parser del Excel de tipo de cambio (formato SUNAT: hoja "TC", columnas
Día | Compra | Venta, una fila por día calendario). El valor ya viene en forma
SUNAT, así que NO se aplica ningún corrimiento — se usa tal cual.

Mismo destino que la semilla y el import manual (upsert por fecha): un solo parser.
"""
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import openpyxl

from src.modules.sire.infrastructure.tipo_cambio.bcrp import CotizacionSbs

_HOJA = "TC"


def _a_decimal(valor) -> Decimal | None:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, TypeError):
        return None


def parse_tc_excel(contenido: bytes) -> dict[date, CotizacionSbs]:
    """Lee el Excel y devuelve {fecha: cotización}. Toma las filas cuya 2ª columna
    sea una fecha y las dos siguientes numéricas (compra, venta); ignora cabeceras
    y títulos. Lanza ValueError si no encuentra ninguna fila válida."""
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    ws = wb[_HOJA] if _HOJA in wb.sheetnames else wb[wb.sheetnames[0]]

    cotizaciones: dict[date, CotizacionSbs] = {}
    for fila in ws.iter_rows(values_only=True):
        if len(fila) < 4:
            continue
        dia, compra, venta = fila[1], fila[2], fila[3]
        if not isinstance(dia, datetime):
            continue
        dec_compra, dec_venta = _a_decimal(compra), _a_decimal(venta)
        if dec_compra is None or dec_venta is None:
            continue
        cotizaciones[dia.date()] = CotizacionSbs(
            fecha=dia.date(), compra=dec_compra, venta=dec_venta
        )

    if not cotizaciones:
        raise ValueError(
            "No se encontraron filas de tipo de cambio en el Excel "
            "(se espera la hoja 'TC' con columnas Día, Compra, Venta)."
        )
    return cotizaciones
