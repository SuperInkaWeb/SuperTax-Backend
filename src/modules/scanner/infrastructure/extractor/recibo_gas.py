"""
recibo_gas.py — Extrae campos de recibos de gas natural peruanos.
Cubre: Cálidda (Lima), Gases del Pacífico, QUAVII, Contugas, GNC Peru.
"""
import re
from src.modules.scanner.infrastructure.utils import (
    extraer_todos_los_ruc, extraer_monto, normalizar_fecha,
    extraer_mes, extraer_anio,
    extraer_fecha_vencimiento, leer_archivo,
)

EMPRESAS_GAS = [
    "CALIDDA", "CÁLIDDA", "GASES DEL PACIFICO", "GASES DEL PACÍFICO",
    "QUAVII", "CONTUGAS", "GNC PERU", "GNC PERÚ",
]


def extract_recibo_gas(file_path: str) -> dict:
    return _parsear(leer_archivo(file_path))


def _parsear(texto: str) -> dict:
    rucs = extraer_todos_los_ruc(texto)
    return {
        "empresa":              _empresa(texto),
        "ruc_empresa":          rucs[0] if rucs else None,
        "nombre_cliente":       _nombre_cliente(texto),
        "ruc_cliente":          rucs[1] if len(rucs) > 1 else None,
        "numero_contrato":      _numero_contrato(texto),
        "numero_medidor":       _numero_medidor(texto),
        "direccion":            _direccion(texto),
        "uso":                  _uso(texto),
        "periodo":              _periodo(texto),
        "mes":                  extraer_mes(texto),
        "anio":                 extraer_anio(texto),
        "fecha_emision":        normalizar_fecha(texto),
        "fecha_vencimiento":    extraer_fecha_vencimiento(texto),
        "lectura_anterior":     _lectura(texto, "ANTERIOR"),
        "lectura_actual":       _lectura(texto, "ACTUAL"),
        "consumo_m3":           _consumo_m3(texto),
        "factor_conversion":    _factor_conversion(texto),
        "consumo_kwh":          _consumo_kwh_gas(texto),
        "cargo_fijo":           extraer_monto(texto, ["CARGO FIJO", "Cargo Fijo",
                                                       "CARGO DE CONEXION"]),
        "cargo_variable":       extraer_monto(texto, ["CARGO VARIABLE", "CARGO POR CONSUMO",
                                                       "ENERGIA CONSUMIDA"]),
        "cargo_comercializacion":extraer_monto(texto, ["CARGO DE COMERCIALIZACION",
                                                        "COMERCIALIZACION"]),
        "subtotal":             extraer_monto(texto, ["SUBTOTAL", "Sub Total"]),
        "igv":                  extraer_monto(texto, ["IGV", "I.G.V", "I.G.V."]),
        "deuda_anterior":       extraer_monto(texto, ["DEUDA ANTERIOR", "SALDO ANTERIOR"]),
        "total_pagar":          extraer_monto(texto, ["TOTAL A PAGAR", "Total a Pagar",
                                                       "IMPORTE TOTAL", "TOTAL"]),
        # compatibilidad frontend
        "serie":                _numero_contrato(texto),
        "numero_doc":           _numero_contrato(texto),
        "total":                extraer_monto(texto, ["TOTAL A PAGAR", "IMPORTE TOTAL", "TOTAL"]),
    }


def _empresa(texto: str) -> str | None:
    texto_upper = texto.upper()
    for emp in EMPRESAS_GAS:
        if emp.upper() in texto_upper:
            return emp
    return None


def _nombre_cliente(texto: str) -> str | None:
    m = re.search(
        r"(?:Cliente|Titular|Nombre)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][^\n]{3,60})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _numero_contrato(texto: str) -> str | None:
    m = re.search(
        r"N[°º\.ro]*\s*(?:DE\s+)?(?:CONTRATO|CLIENTE|CUENTA|SUMINISTRO)\s*[:\-]?\s*(\d{5,12})",
        texto, re.IGNORECASE
    )
    return m.group(1) if m else None


def _numero_medidor(texto: str) -> str | None:
    m = re.search(
        r"(?:N[°º]?\s*(?:DE\s+)?MEDIDOR|MEDIDOR\s*N[°º]?)\s*[:\-]?\s*([A-Z0-9\-]{4,15})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _direccion(texto: str) -> str | None:
    m = re.search(
        r"(?:Direcci[oó]n|Domicilio)\s*[:\-]?\s*([^\n]{5,80})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _uso(texto: str) -> str | None:
    m = re.search(r"(?:Uso|Tipo\s+de\s+Uso|Categor[íi]a)\s*[:\-]?\s*([^\n]{2,30})",
                  texto, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _periodo(texto: str) -> str | None:
    m = re.search(r"(?:Periodo|Per[íi]odo)\s*[:\-]?\s*([A-Z]+\s*\d{4})", texto, re.IGNORECASE)
    return m.group(1).strip() if m else None




def _lectura(texto: str, tipo: str) -> float | None:
    m = re.search(rf"LECTURA\s+{tipo}\s*[:\-]?\s*(\d+(?:\.\d+)?)", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _consumo_m3(texto: str) -> float | None:
    m = re.search(r"(?:CONSUMO|VOLUMEN)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*m[3³]", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _factor_conversion(texto: str) -> float | None:
    m = re.search(r"(?:FACTOR\s+(?:DE\s+)?CONVERSI[OÓ]N|FC)\s*[:\-]?\s*(\d+\.\d+)",
                  texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _consumo_kwh_gas(texto: str) -> float | None:
    m = re.search(r"(?:ENERGIA\s+CONSUMIDA|kWh)\s*[:\-]?\s*(\d+(?:\.\d+)?)", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None
