"""
Cliente del BCRP para el tipo de cambio oficial (serie SBS del sistema bancario).

El BCRP republica como open data el TC compra/venta que SUNAT usa en `tcS01Alias`:
- PD04639PD = Compra
- PD04640PD = Venta
API JSON pública: sin captcha, sin autenticación, sin terceros. El BCRP reordena
las series en la respuesta, así que se mapea por NOMBRE, no por posición.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

_BASE = "https://estadisticas.bcrp.gob.pe/estadisticas/series/api"
_SERIE_COMPRA = "PD04639PD"
_SERIE_VENTA = "PD04640PD"
_TIMEOUT_SEGUNDOS = 30

# El WAF del BCRP corta las peticiones sin User-Agent de navegador (403 desde
# datacenters), igual que SUNAT. Se envía un UA de navegador.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# El BCRP abrevia los meses en español, con "Set" (no "Sep") para setiembre.
_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


@dataclass(frozen=True)
class CotizacionSbs:
    """Cotización SBS de un día hábil, tal como la publica el BCRP para esa fecha."""

    fecha: date
    compra: Decimal
    venta: Decimal


def _parse_periodo(nombre: str) -> date:
    """'02.Ene.19' -> date(2019, 1, 2)."""
    dia, mes, anio = nombre.split(".")
    return date(2000 + int(anio), _MESES[mes.strip().lower()], int(dia))


def _parse_valor(bruto: str) -> Decimal | None:
    """Convierte el valor; None si el día no tiene dato ('n.d.' u otros)."""
    try:
        return Decimal(str(bruto))
    except (InvalidOperation, TypeError):
        return None


def _indices_series(config: dict) -> tuple[int, int]:
    """Ubica qué posición de `values` es compra y cuál venta (el BCRP reordena las
    series sin importar el orden pedido en la URL)."""
    compra_idx = venta_idx = None
    for i, serie in enumerate(config.get("series", [])):
        nombre = serie.get("name", "").lower()
        if "compra" in nombre:
            compra_idx = i
        elif "venta" in nombre:
            venta_idx = i
    if compra_idx is None or venta_idx is None:
        raise ValueError("Respuesta del BCRP sin series de compra/venta")
    return compra_idx, venta_idx


def parse_respuesta(data: dict) -> list[CotizacionSbs]:
    """Convierte el JSON del BCRP en cotizaciones de días hábiles (sin red)."""
    compra_idx, venta_idx = _indices_series(data.get("config", {}))
    cotizaciones: list[CotizacionSbs] = []
    for periodo in data.get("periods", []):
        valores = periodo.get("values", [])
        if len(valores) <= max(compra_idx, venta_idx):
            continue
        compra = _parse_valor(valores[compra_idx])
        venta = _parse_valor(valores[venta_idx])
        if compra is None or venta is None:
            continue  # día sin dato publicado
        cotizaciones.append(
            CotizacionSbs(fecha=_parse_periodo(periodo["name"]), compra=compra, venta=venta)
        )
    return cotizaciones


def obtener_cotizaciones(desde: date, hasta: date) -> list[CotizacionSbs]:
    """Trae del BCRP las cotizaciones SBS (días hábiles) en [desde, hasta]."""
    url = (
        f"{_BASE}/{_SERIE_COMPRA}-{_SERIE_VENTA}/json/"
        f"{desde.isoformat()}/{hasta.isoformat()}"
    )
    with httpx.Client(timeout=_TIMEOUT_SEGUNDOS, headers=_HEADERS) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return parse_respuesta(resp.json())
