"""
recibo_telefonia.py — Extrae campos de recibos de telefonía e internet peruanos.
Cubre: Claro, Movistar, Entel, Bitel, WOM, Virgin Mobile.
"""
import re
from src.modules.scanner.infrastructure.utils import (
    extraer_todos_los_ruc, extraer_monto, normalizar_fecha,
    extraer_mes, extraer_anio,
    extraer_fecha_vencimiento, leer_archivo,
)

OPERADORAS = [
    "CLARO", "MOVISTAR", "ENTEL", "BITEL", "WOM", "VIRGIN MOBILE",
    "AMERICA MOVIL", "TELEFONICA", "TELEFONÍCA",
]


def extract_recibo_telefonia(file_path: str) -> dict:
    return _parsear(leer_archivo(file_path))


def _parsear(texto: str) -> dict:
    rucs = extraer_todos_los_ruc(texto)
    return {
        "empresa":              _empresa(texto),
        "ruc_empresa":          rucs[0] if rucs else None,
        "nombre_cliente":       _nombre_cliente(texto),
        "ruc_cliente":          rucs[1] if len(rucs) > 1 else None,
        "numero_cuenta":        _numero_cuenta(texto),
        "numero_linea":         _numero_linea(texto),
        "plan":                 _plan(texto),
        "tipo_servicio":        _tipo_servicio(texto),
        "periodo":              _periodo(texto),
        "mes":                  extraer_mes(texto),
        "anio":                 extraer_anio(texto),
        "fecha_emision":        normalizar_fecha(texto),
        "fecha_vencimiento":    extraer_fecha_vencimiento(texto),
        "cargos_fijos":         extraer_monto(texto, ["CARGO FIJO", "RENTA BÁSICA",
                                                       "RENTA BASICA", "CARGOS FIJOS"]),
        "cargos_variables":     extraer_monto(texto, ["CARGOS VARIABLES", "CARGOS ADICIONALES",
                                                       "LLAMADAS", "SMS"]),
        "datos_adicionales":    extraer_monto(texto, ["DATOS ADICIONALES", "MB ADICIONALES",
                                                       "GB ADICIONALES"]),
        "roaming":              extraer_monto(texto, ["ROAMING", "ITINERANCIA"]),
        "descuentos":           extraer_monto(texto, ["DESCUENTO", "DESCUENTOS", "BONIF",
                                                       "BONIFICACION", "PROMO"]),
        "subtotal":             extraer_monto(texto, ["SUBTOTAL", "Sub Total"]),
        "igv":                  extraer_monto(texto, ["IGV", "I.G.V", "I.G.V."]),
        "deuda_anterior":       extraer_monto(texto, ["DEUDA ANTERIOR", "SALDO ANTERIOR",
                                                       "SALDO PENDIENTE"]),
        "total_pagar":          extraer_monto(texto, ["TOTAL A PAGAR", "Total a Pagar",
                                                       "IMPORTE TOTAL", "TOTAL DEL MES"]),
        # compatibilidad frontend
        "serie":                _numero_cuenta(texto),
        "numero_doc":           _numero_cuenta(texto),
        "total":                extraer_monto(texto, ["TOTAL A PAGAR", "IMPORTE TOTAL", "TOTAL"]),
    }


def _empresa(texto: str) -> str | None:
    texto_upper = texto.upper()
    for op in OPERADORAS:
        if op in texto_upper:
            return op
    return None


def _nombre_cliente(texto: str) -> str | None:
    m = re.search(
        r"(?:Cliente|Titular|Nombre|Suscriptor)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][^\n]{3,60})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _numero_cuenta(texto: str) -> str | None:
    m = re.search(
        r"N[°º\.ro]*\s*(?:DE\s+)?(?:CUENTA|CLIENTE|CONTRATO|SUSCRIPTOR)\s*[:\-]?\s*(\d{5,14})",
        texto, re.IGNORECASE
    )
    return m.group(1) if m else None


def _numero_linea(texto: str) -> str | None:
    m = re.search(
        r"N[°º\.ro]*\s*(?:DE\s+)?(?:L[IÍ]NEA|TEL[EÉ]FONO|CELULAR|NÚMERO)\s*[:\-]?\s*(\d[\d\s\-]{6,12}\d)",
        texto, re.IGNORECASE
    )
    if m:
        return re.sub(r"[\s\-]", "", m.group(1))
    # Número de 9 dígitos que empieza con 9 (celular peruano)
    m = re.search(r"\b(9\d{8})\b", texto)
    return m.group(1) if m else None


def _plan(texto: str) -> str | None:
    m = re.search(
        r"(?:PLAN|PLAN TARIFARIO|TARIFA|PAQUETE)\s*[:\-]?\s*([^\n]{3,50})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _tipo_servicio(texto: str) -> str | None:
    """Detecta si es móvil, fijo, internet o mixto."""
    servicios = []
    t = texto.upper()
    if re.search(r"CELULAR|M[OÓ]VIL|SMARTPHONE", t):
        servicios.append("Telefonía Móvil")
    if re.search(r"TELEFON[IÍ]A\s+FIJA|L[IÍ]NEA\s+FIJA", t):
        servicios.append("Telefonía Fija")
    if re.search(r"INTERNET|BANDA\s+ANCHA|FIBRA\s+[OÓ]PTICA|FIBRA\s+OPTICA", t):
        servicios.append("Internet")
    if re.search(r"CABLE|TV\s+CABLE|TELEVISION", t):
        servicios.append("TV Cable")
    return " + ".join(servicios) if servicios else None


def _periodo(texto: str) -> str | None:
    m = re.search(r"(?:Periodo|Per[íi]odo)\s*[:\-]?\s*([A-Z]+\s*\d{4})", texto, re.IGNORECASE)
    return m.group(1).strip() if m else None


