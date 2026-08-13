"""
recibo_luz.py — Extrae campos de recibos de electricidad de distribuidoras peruanas.
Cubre: ENEL, LUZ DEL SUR, HIDRANDINA, ELECTROCENTRO, SEAL, ELSE, PLUZ,
       ENOSA, ELECTRO ORIENTE, ELECTRO SUR ESTE, ELECTRO PUNO,
       ELECTRO UCAYALI, ADINELSA, EDELNOR y cualquier empresa con kWh/SUMINISTRO.
"""
import os
import re
from src.modules.scanner.infrastructure.utils import (
    limpiar_texto, extraer_todos_los_ruc, extraer_monto, normalizar_fecha,
    pdf_a_texto, imagen_a_texto, EXTENSIONES_IMAGEN, MESES,
    extraer_mes, extraer_anio, extraer_fecha_vencimiento,
    parsear_fecha_con_mes_abreviado, leer_archivo,
)

EMPRESAS_ELECTRICAS = [
    "ENEL", "LUZ DEL SUR", "ELECTRO ORIENTE", "SEAL", "ELSE",
    "ELECTRO SUR ESTE", "HIDRANDINA", "ENOSA", "ELECTRONOROESTE",
    "ELECTROCENTRO", "ELECTRO PUNO", "PLUZ", "PLUZ ENERGIA",
    "EDELNOR", "ELECTRO UCAYALI", "ADINELSA", "ELECTRO NORTE",
]

# OCR puede leer variantes garbled de estas empresas
_OCR_FUZZY = [
    (r"PI\.U\b|NUZ\s*ENER",               "PLUZ ENERGIA PERU S.A.A."),
    (r"LUZ\s+DEL\s+S",                    "LUZ DEL SUR S.A.A."),
    (r"ENEL\s+DISTRIB",                   "ENEL DISTRIBUCION PERU S.A.A."),
    (r"HIDRANDINA",                        "HIDRANDINA S.A."),
    (r"ELECTROCENTRO",                     "ELECTROCENTRO S.A."),
    (r"ELECTRO\s+SUR\s+ESTE",             "ELECTRO SUR ESTE S.A.A."),
    (r"SEAL\b",                            "SEAL S.A."),
]


def extract_recibo_luz(file_path: str) -> dict:
    return _parsear(leer_archivo(file_path))


def _parsear(texto: str) -> dict:
    rucs = extraer_todos_los_ruc(texto)
    num_sum = _numero_suministro(texto)
    return {
        "empresa":               _empresa(texto),
        "ruc_empresa":           rucs[0] if rucs else None,
        "nombre_cliente":        _nombre_cliente(texto),
        "ruc_cliente":           rucs[1] if len(rucs) > 1 else None,
        "numero_suministro":     num_sum,
        "direccion_suministro":  _direccion_suministro(texto),
        "tarifa":                _tarifa(texto),
        "periodo":               _periodo(texto),
        "mes":                   extraer_mes(texto),
        "anio":                  extraer_anio(texto),
        "fecha_emision":         normalizar_fecha(texto),
        "fecha_vencimiento":     extraer_fecha_vencimiento(texto),
        "lectura_anterior":      _lectura(texto, "ANTERIOR"),
        "fecha_lectura_anterior":_fecha_lectura(texto, "ANTERIOR"),
        "lectura_actual":        _lectura(texto, "ACTUAL"),
        "fecha_lectura_actual":  _fecha_lectura(texto, "ACTUAL"),
        "consumo_kwh":           _consumo_kwh(texto),
        "cargo_fijo":            extraer_monto(texto, ["CARGO FIJO", "Cargo Fijo"]),
        "energia_activa":        extraer_monto(texto, ["ENERGÍA ACTIVA", "ENERGIA ACTIVA",
                                                        "CARGO POR ENERGÍA", "CARGO POR ENERGIA"]),
        "alumbrado_publico":     extraer_monto(texto, ["ALUMBRADO PÚBLICO", "ALUMBRADO PUBLICO",
                                                        "APORTE ALUMBRADO"]),
        "mantenimiento_redes":   extraer_monto(texto, ["MANTENIMIENTO", "MANTENIMIENTO DE REDES"]),
        "reajuste":              extraer_monto(texto, ["REAJUSTE", "FACTOR DE REAJUSTE"]),
        "subtotal":              extraer_monto(texto, ["SUBTOTAL", "Sub Total", "TOTAL DEL MES",
                                                        "Total del Mes"]),
        "igv":                   extraer_monto(texto, ["IGV", "I.G.V", "I.G.V."]),
        "deuda_anterior":        extraer_monto(texto, ["DEUDA ANTERIOR", "SALDO ANTERIOR",
                                                        "DEUDA VENCIDA"]),
        "redondeo":              extraer_monto(texto, ["REDONDEO", "REDONDEADO", "AJUSTE"]),
        "total_pagar":           extraer_monto(texto, ["TOTAL A PAGAR", "Total a Pagar",
                                                        "IMPORTE A PAGAR", "MONTO A PAGAR"]),
        # serie/numero_doc = número de suministro (no tiene serie SUNAT)
        "serie":                 num_sum,
        "numero_doc":            num_sum,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _empresa(texto: str) -> str | None:
    texto_upper = texto.upper()
    for emp in EMPRESAS_ELECTRICAS:
        if emp in texto_upper:
            return emp
    for pattern, nombre in _OCR_FUZZY:
        if re.search(pattern, texto_upper):
            return nombre
    return None


def _nombre_cliente(texto: str) -> str | None:
    m = re.search(
        r"(?:Cliente|Titular|Propietario|Suministrado\s+a|Nombre)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][^\n]{3,60})",
        texto, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # OCR: buscar nombre en ALL CAPS solo en línea (Ej. "QUISPE FLORES TEODOSIO")
    for linea in texto.splitlines():
        linea = linea.strip()
        if re.match(r'^[A-ZÁÉÍÓÚÑ]{3,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,4}$', linea):
            if not any(k in linea for k in ["DATOS", "LECTURA", "CONSUMO", "PERIODO",
                                             "PLIEGO", "SISTEMA", "ENERG", "VENCIM"]):
                return linea
    return None


def _numero_suministro(texto: str) -> str | None:
    m = re.search(
        r"N[°º\.ro]*\s*(?:DE\s+)?SUMINISTRO\s*[:\-]?\s*(\d{5,10})",
        texto, re.IGNORECASE
    )
    if m:
        return m.group(1)
    m = re.search(r"\bSuministro\b[:\s]+(\d{5,10})", texto, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback OCR: número de exactamente 7 dígitos
    m = re.search(r'\b(\d{7})\b', texto)
    if m:
        return m.group(1)
    # Último recurso: 6-9 dígitos solos en una línea
    for linea in texto.splitlines():
        lc = linea.strip().strip("| ")
        if re.fullmatch(r'\d{6,9}', lc):
            return lc
    return None


def _direccion_suministro(texto: str) -> str | None:
    m = re.search(
        r"(?:Direcci[oó]n|Domicilio|Predio|Inmueble)\s*(?:del?\s+Suministro)?[:\-]?\s*([^\n]{5,80})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _tarifa(texto: str) -> str | None:
    m = re.search(r"(?:Tarifa|Tipo\s+de\s+Tarifa|Pliego\s+Tarifario)\s*[:\-]?\s*([^\n]{2,20})",
                  texto, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _periodo(texto: str) -> str | None:
    m = re.search(
        r"(?:Periodo|Per[íi]odo)\s*[:\-]?\s*([A-Z]+\s*\d{4})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None




def _lectura(texto: str, tipo: str) -> float | None:
    m = re.search(rf"LECTURA\s+{tipo}\s*[:\-]?\s*(\d+)", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _fecha_lectura(texto: str, tipo: str) -> str | None:
    # Busca la fecha entre paréntesis o después de la lectura
    m = re.search(
        rf"LECTURA\s+{tipo}[^\n]*?[\(\[]?(\d{{2}}/\d{{2}}/\d{{4}}|\d{{2}}/[A-Z]{{3}}/\d{{4}})[\)\]]?",
        texto, re.IGNORECASE
    )
    if m:
        return parsear_fecha_con_mes_abreviado(m.group(1)) or normalizar_fecha(m.group(1))
    return None


def _consumo_kwh(texto: str) -> float | None:
    m = re.search(r"(?:CONSUMO|ENERGIA\s+ACTIVA)[^\n]{0,30}?(\d+(?:\.\d+)?)\s*kWh",
                  texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # También puede aparecer como "kWh 148" o "148 kWh"
    m = re.search(r"(\d+)\s*kWh\b", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


