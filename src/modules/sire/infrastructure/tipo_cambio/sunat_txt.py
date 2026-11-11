"""
Cliente del archivo diario de tipo de cambio de SUNAT (`tipoCambio.txt`).

Formato: `DD/MM/YYYY|compra|venta|` (una línea con el TC vigente del día). Ya viene
en forma SUNAT (sin corrimiento). Es un .txt estático, sin captcha y alcanzable
desde el servidor — por eso sirve para la captura diaria automática.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from src.modules.sire.infrastructure.tipo_cambio.bcrp import CotizacionSbs

_URL = "https://www.sunat.gob.pe/a/txt/tipoCambio.txt"
_TIMEOUT_SEGUNDOS = 30
# El WAF de SUNAT puede cortar peticiones sin User-Agent de navegador.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def parse_txt(texto: str) -> list[CotizacionSbs]:
    """Convierte el contenido del txt en cotizaciones (sin red)."""
    cotizaciones: list[CotizacionSbs] = []
    for linea in texto.splitlines():
        partes = linea.strip().split("|")
        if len(partes) < 3:
            continue
        try:
            fecha = datetime.strptime(partes[0].strip(), "%d/%m/%Y").date()
            compra = Decimal(partes[1].strip())
            venta = Decimal(partes[2].strip())
        except (ValueError, InvalidOperation):
            continue
        cotizaciones.append(CotizacionSbs(fecha=fecha, compra=compra, venta=venta))
    return cotizaciones


def obtener_tc_hoy() -> list[CotizacionSbs]:
    """Baja el TC del día desde SUNAT. Lanza httpx.HTTPError si falla la red."""
    with httpx.Client(timeout=_TIMEOUT_SEGUNDOS, headers=_HEADERS) as client:
        resp = client.get(_URL)
        resp.raise_for_status()
        return parse_txt(resp.text)
