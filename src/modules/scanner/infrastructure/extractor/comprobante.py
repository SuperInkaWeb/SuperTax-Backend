"""
comprobante.py — Extrae campos de comprobantes SUNAT:
  - Factura Electrónica
  - Boleta de Venta
  - Recibo por Honorarios
  - Nota de Crédito / Nota de Débito

Detecta el subtipo automáticamente si no se indica.
"""
import re
from src.modules.scanner.infrastructure.utils import (
    extraer_todos_los_ruc, extraer_monto, normalizar_fecha,
    extraer_serie_numero, EXTENSIONES_IMAGEN, leer_archivo,
)


# ── Dispatcher público ───────────────────────────────────────────────────────

def extract_comprobante(file_path: str, tipo: str | None = None) -> dict:
    """
    Extrae campos según el tipo de comprobante.
    Si tipo=None, lo detecta del contenido.
    """
    texto = leer_archivo(file_path)

    if tipo is None:
        tipo = _detectar_subtipo(texto)

    campos = _PARSERS.get(tipo, _parsear_factura)(texto)
    campos["tipo_comprobante"] = tipo
    return campos


def _detectar_subtipo(texto: str) -> str:
    t = texto.upper()
    if re.search(r"NOTA\s+DE\s+CR[EÉ]DITO", t):
        return "nota_credito"
    if re.search(r"NOTA\s+DE\s+D[EÉ]BITO", t):
        return "nota_debito"
    if re.search(r"RECIBO\s+POR\s+HONORARIOS|HONORARIOS\s+PROFESIONALES", t):
        return "recibo_honorarios"
    if re.search(r"BOLETA\s+DE\s+VENTA|\bB\d{3}\s*[-–]", t):
        return "boleta_venta"
    return "factura_electronica"


# ── Factura Electrónica ──────────────────────────────────────────────────────

def _parsear_factura(texto: str) -> dict:
    serie, numero = extraer_serie_numero(texto)
    return {
        "ruc_proveedor":      _ruc_proveedor(texto),
        "razon_social_prov":  _razon_proveedor(texto),
        "direccion_prov":     _extraer_direccion_proveedor(texto),
        "ruc_cliente":        _ruc_cliente_factura(texto),
        "razon_social_cli":   _razon_cliente_factura(texto),
        "direccion_cli":      _extraer_direccion_cliente(texto),
        "fecha_emision":      _fecha_factura(texto),
        "fecha_vencimiento":  _fecha_vencimiento(texto),
        "condicion_pago":     _condicion_pago(texto),
        "serie":              serie,
        "numero_doc":         numero,
        "moneda":             _extraer_moneda(texto),
        "descripcion":        _descripcion(texto),
        "subtotal":           extraer_monto(texto, ["OP. AFECTA", "OP. GRAVADA", "OP. INAFECTA",
                                                     "OP. EXONERADA", "SUBTOTAL", "Sub Total"]),
        "descuentos":         extraer_monto(texto, ["DESCUENTO", "DSCTO", "REBAJA"]),
        "igv":                extraer_monto(texto, ["IGV", "I.G.V", "I.G.V.", "IGV 18%"]),
        "otros_cargos":       extraer_monto(texto, ["OTROS CARGOS", "PERCEPCION", "DETRACCION"]),
        "total":              extraer_monto(texto, ["IMPORTE TOTAL", "TOTAL A PAGAR", "TOTAL",
                                                     "Importe Total"]),
    }


# ── Boleta de Venta ──────────────────────────────────────────────────────────

def _parsear_boleta(texto: str) -> dict:
    serie, numero = extraer_serie_numero(texto)
    return {
        "ruc_proveedor":     _ruc_proveedor(texto),
        "razon_social_prov": _razon_proveedor(texto),
        "nombre_cliente":    _nombre_cliente_boleta(texto),
        "ruc_cliente":       _ruc_cliente_boleta(texto),
        "fecha_emision":     normalizar_fecha(texto),
        "serie":             serie,
        "numero_doc":        numero,
        "moneda":            _extraer_moneda(texto),
        "descripcion":       _descripcion(texto),
        "subtotal":          extraer_monto(texto, ["OP. AFECTA", "OP. GRAVADA", "SUBTOTAL", "Sub Total"]),
        "igv":               extraer_monto(texto, ["IGV", "I.G.V", "I.G.V.", "IGV 18%"]),
        "total":             extraer_monto(texto, ["TOTAL", "IMPORTE TOTAL", "Total"]),
    }


# ── Recibo por Honorarios ────────────────────────────────────────────────────

def _parsear_honorarios(texto: str) -> dict:
    serie, numero = extraer_serie_numero(texto)
    honorarios = extraer_monto(texto, ["HONORARIOS", "IMPORTE", "MONTO"])
    retencion  = extraer_monto(texto, ["RETENCIÓN", "RETENCION", "RETENC"])
    # Si no se encontró retención, calcular 4%
    if honorarios and not retencion:
        retencion = round(honorarios * 0.04, 2)
    neto = extraer_monto(texto, ["NETO A PAGAR", "NETO", "IMPORTE NETO"])
    if honorarios and not neto:
        neto = round(honorarios - (retencion or 0), 2)
    return {
        "ruc_emisor":           _ruc_proveedor(texto),
        "nombre_emisor":        _razon_proveedor(texto),
        "dni_emisor":           _extraer_dni(texto),
        "nombre_cliente":       _nombre_cliente_boleta(texto),
        "ruc_cliente":          _ruc_cliente_boleta(texto),
        "fecha_emision":        normalizar_fecha(texto),
        "periodo_servicio":     _periodo_servicio(texto),
        "serie":                serie,
        "numero_doc":           numero,
        "moneda":               _extraer_moneda(texto),
        "descripcion_servicio": _descripcion(texto),
        "monto_honorarios":     honorarios,
        "retencion_pct":        4.0,
        "retencion_monto":      retencion,
        "neto_pagar":           neto,
    }


# ── Nota de Crédito / Débito ─────────────────────────────────────────────────

def _parsear_nota(texto: str) -> dict:
    serie, numero = extraer_serie_numero(texto)
    return {
        "ruc_proveedor":       _ruc_proveedor(texto),
        "razon_social_prov":   _razon_proveedor(texto),
        "ruc_cliente":         _ruc_cliente_factura(texto),
        "razon_social_cli":    _razon_cliente_factura(texto),
        "fecha_emision":       _fecha_factura(texto),
        "serie":               serie,
        "numero_doc":          numero,
        "moneda":              _extraer_moneda(texto),
        "descripcion":         _descripcion(texto),
        "doc_ref_tipo":        _doc_referencia_tipo(texto),
        "doc_ref_serie":       _doc_referencia_serie(texto),
        "doc_ref_numero":      _doc_referencia_numero(texto),
        "motivo":              _motivo_nota(texto),
        "monto_afectado":      extraer_monto(texto, ["OP. AFECTA", "OP. GRAVADA", "SUBTOTAL", "Sub Total"]),
        "igv":                 extraer_monto(texto, ["IGV", "I.G.V", "I.G.V."]),
        "total":               extraer_monto(texto, ["IMPORTE TOTAL", "TOTAL", "Total"]),
    }


_PARSERS = {
    "factura_electronica": _parsear_factura,
    "boleta_venta":        _parsear_boleta,
    "recibo_honorarios":   _parsear_honorarios,
    "nota_credito":        _parsear_nota,
    "nota_debito":         _parsear_nota,
}


# ── Funciones auxiliares compartidas ────────────────────────────────────────

def _ruc_proveedor(texto: str) -> str | None:
    m = re.search(r"([A-ZÁÉÍÓÚÑ][^\n]+)\s+RUC[:\s]+(\d{11})", texto, re.IGNORECASE)
    if m:
        return m.group(2)
    rucs = extraer_todos_los_ruc(texto)
    return rucs[0] if rucs else None


def _razon_proveedor(texto: str) -> str | None:
    m = re.search(
        r"^([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑA-Za-záéíóúñ\s\.\-&,]+(?:S\.A\.C\.?|S\.R\.L\.?|E\.I\.R\.L\.?|SAC|SRL|EIRL|S\.A\.))\s+RUC",
        texto, re.MULTILINE | re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r"^([A-ZÁÉÍÓÚÑ][A-Z\s\.&]+(?:S\.A\.C?\.?|S\.R\.L\.?|E\.I\.R\.L\.?|SAC|SRL|EIRL))\s*$",
        texto, re.MULTILINE
    )
    return m.group(1).strip() if m else None


def _extraer_direccion_proveedor(texto: str) -> str | None:
    m = re.search(
        r"(?:Direcci[oó]n|Domicilio|Av\.|Jr\.|Cal\.|Urb\.)[^\n]{5,80}",
        texto, re.IGNORECASE
    )
    return m.group(0).strip() if m else None


def _extraer_direccion_cliente(texto: str) -> str | None:
    m = re.search(
        r"(?:Direcci[oó]n\s+(?:del)?\s*(?:cliente|comprador)|Domicilio\s+Fiscal)[:\s]+([^\n]{5,80})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _ruc_cliente_factura(texto: str) -> str | None:
    m = re.search(r"SE.OR[^:\n]*:\s*.{3,80}?RUC[:\s]+(\d{11})", texto, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"(?:CLIENTE|RUC\s+(?:del)?\s*(?:cliente|comprador))[:\s]+(\d{11})", texto, re.IGNORECASE)
    if m:
        return m.group(1)
    rucs = extraer_todos_los_ruc(texto)
    return rucs[1] if len(rucs) > 1 else None


def _razon_cliente_factura(texto: str) -> str | None:
    m = re.search(r"SE.OR[^:\n]*:\s*([A-ZÁÉÍÓÚÑ][^\n]{3,80}?)\s+RUC", texto, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(?:Se[ñn]or(?:es)?|Señores|Cliente|Raz[oó]n\s+Social)\s*:\s*([A-ZÁÉÍÓÚÑ][^\n]{3,60})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _ruc_cliente_boleta(texto: str) -> str | None:
    rucs = extraer_todos_los_ruc(texto)
    return rucs[1] if len(rucs) > 1 else None


def _nombre_cliente_boleta(texto: str) -> str | None:
    m = re.search(
        r"(?:Se[ñn]or(?:es)?|Cliente|Comprador|Adquirente)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][^\n]{3,60})",
        texto, re.IGNORECASE
    )
    if m:
        nombre = m.group(1).strip()
        # Cortar si llega a "RUC" o similares
        nombre = re.split(r"\s+(?:RUC|DNI|Doc)\b", nombre, flags=re.IGNORECASE)[0]
        return nombre.strip()
    return None


def _fecha_factura(texto: str) -> str | None:
    m = re.search(
        r"FACTURA\s+ELECTR[OÓ]NICA.{0,300}?(\d{2}[/\-]\d{2}[/\-]\d{4})",
        texto, re.IGNORECASE | re.DOTALL
    )
    if m:
        return normalizar_fecha(m.group(1))
    return normalizar_fecha(texto)


def _fecha_vencimiento(texto: str) -> str | None:
    m = re.search(
        r"(?:Fecha\s+(?:de\s+)?Vencimiento|Vence|Vto\.?)\s*[:\-]?\s*(\d{2}[/\-]\d{2}[/\-]\d{4})",
        texto, re.IGNORECASE
    )
    return normalizar_fecha(m.group(1)) if m else None


def _condicion_pago(texto: str) -> str | None:
    m = re.search(
        r"(?:Condici[oó]n\s+(?:de\s+)?Pago|Forma\s+de\s+Pago)\s*[:\-]?\s*([^\n]{2,30})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _extraer_moneda(texto: str) -> str:
    if re.search(r"\bUSD\b|\bD[OÓ]LARES?\b|\$\s*\d", texto, re.IGNORECASE):
        return "USD"
    if re.search(r"\bEUR\b|\bEUROS?\b", texto, re.IGNORECASE):
        return "EUR"
    return "PEN"


def _extraer_dni(texto: str) -> str | None:
    m = re.search(r"\b(\d{8})\b", texto)
    return m.group(1) if m else None


def _periodo_servicio(texto: str) -> str | None:
    m = re.search(
        r"(?:Periodo|Per[íi]odo)\s+(?:de\s+)?(?:Servicio|Trabajo)[:\s]*([^\n]{3,30})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _descripcion(texto: str) -> str | None:
    """Detalle/concepto del comprobante (best-effort: en facturas es la tabla de
    ítems, así que captura la primera línea de descripción)."""
    m = re.search(
        r"(?:Descripci[oó]n|Concepto|Detalle|Servicio|Producto)\s*[:\-]?\s*([^\n]{5,120})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _doc_referencia_tipo(texto: str) -> str | None:
    m = re.search(
        r"(?:Comprobante|Documento)\s+que\s+(?:modifica|afecta)\s*[:\-]?\s*([^\n]{3,30})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _doc_referencia_serie(texto: str) -> str | None:
    m = re.search(
        r"(?:Doc\.?\s+Ref\.?|Referencia|Afecta\s+a?)\s*[:\-]?\s*([A-Z]\d{3})\s*[-–]",
        texto, re.IGNORECASE
    )
    return m.group(1) if m else None


def _doc_referencia_numero(texto: str) -> str | None:
    m = re.search(
        r"(?:Doc\.?\s+Ref\.?|Referencia|Afecta\s+a?)\s*[:\-]?\s*[A-Z]\d{3}\s*[-–]\s*(\d{5,9})",
        texto, re.IGNORECASE
    )
    return m.group(1) if m else None


def _motivo_nota(texto: str) -> str | None:
    m = re.search(
        r"(?:Motivo|Sustento|Glosa|Descripci[oó]n)\s*[:\-]?\s*([^\n]{5,120})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None
